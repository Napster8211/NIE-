import os
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException
from fastapi.testclient import TestClient


TEST_OWNER_KEY = "explicit-test-only-owner-key"
TEST_ORIGIN = "http://localhost:5173"
os.environ.setdefault("NIE_ENV", "test")
os.environ["NIE_OWNER_KEY"] = TEST_OWNER_KEY
os.environ.setdefault("NIE_TRUSTED_FRONTEND_ORIGINS", TEST_ORIGIN)
os.environ.setdefault("DIRECTOR_SESSION_COOKIE_SECURE", "false")
os.environ.setdefault("DIRECTOR_SESSION_COOKIE_SAMESITE", "lax")

from app.main import app
from app.schemas.director_speech import DirectorTranscriptionResponse
from app.services.authorization import verify_owner_key_token
from app.services.director_auth_service import (
    DIRECTOR_REALTIME_PURPOSE,
    DirectorAuthError,
    DirectorAuthService,
    FirebaseOwnerIdentityVerifier,
    VerifiedOwnerIdentity,
    get_director_auth_service,
    trusted_frontend_origins,
)


def utc_now():
    return datetime.now(timezone.utc)


class FakeIdentityVerifier:
    async def verify(self, assertion):
        if assertion == "expired-identity":
            raise DirectorAuthError("DIRECTOR_IDENTITY_INVALID", 401)
        if assertion == "not-allowed":
            raise DirectorAuthError("DIRECTOR_OWNER_NOT_ALLOWED", 403)
        if assertion != "valid-owner-identity":
            raise DirectorAuthError("DIRECTOR_IDENTITY_INVALID", 401)
        return VerifiedOwnerIdentity(uid="firebase-owner-uid", email="owner@example.test")


class FakeDirectorAuthRepository:
    def __init__(self):
        self.sessions = {}
        self.tickets = {}

    async def create_session(self, **values):
        record = SimpleNamespace(**values, revoked_at=None)
        self.sessions[values["token_hash"]] = record
        return record

    async def get_session(self, token_hash):
        return self.sessions.get(token_hash)

    async def revoke_session(self, token_hash, revoked_at):
        record = self.sessions.get(token_hash)
        if not record or record.revoked_at is not None:
            return False
        record.revoked_at = revoked_at
        for ticket in self.tickets.values():
            if ticket.session_id == record.session_id and ticket.revoked_at is None:
                ticket.revoked_at = revoked_at
        return True

    async def create_ticket(self, **values):
        record = SimpleNamespace(**values, consumed_at=None, revoked_at=None)
        self.tickets[values["ticket_hash"]] = record
        return record

    async def consume_ticket(self, ticket_hash, purpose, now=None):
        now = now or utc_now()
        record = self.tickets.get(ticket_hash)
        if (
            record is None
            or record.purpose != purpose
            or record.consumed_at is not None
            or record.revoked_at is not None
            or record.expires_at <= now
        ):
            return None
        session = next(
            (item for item in self.sessions.values() if item.session_id == record.session_id),
            None,
        )
        if session is None or session.revoked_at is not None or session.expires_at <= now:
            return None
        record.consumed_at = now
        return record


class DirectorAuthServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.repository = FakeDirectorAuthRepository()
        self.service = DirectorAuthService(self.repository, FakeIdentityVerifier())

    async def test_invalid_expired_and_non_allowlisted_identity_are_rejected(self):
        for assertion, code in (
            ("invalid", "DIRECTOR_IDENTITY_INVALID"),
            ("expired-identity", "DIRECTOR_IDENTITY_INVALID"),
            ("not-allowed", "DIRECTOR_OWNER_NOT_ALLOWED"),
        ):
            with self.subTest(assertion=assertion):
                with self.assertRaises(DirectorAuthError) as caught:
                    await self.service.create_session(assertion)
                self.assertEqual(caught.exception.code, code)

    async def test_authorized_identity_creates_opaque_finite_session(self):
        issued = await self.service.create_session("valid-owner-identity")
        self.assertNotIn(issued.token, self.repository.sessions)
        self.assertNotEqual(issued.token, issued.csrf_token)
        self.assertEqual(issued.principal.owner_uid, "firebase-owner-uid")
        self.assertGreater(issued.principal.expires_at, utc_now())
        self.assertLessEqual(issued.principal.expires_at, utc_now() + timedelta(hours=24, seconds=2))
        validated = await self.service.validate_session(issued.token)
        self.assertEqual(validated.session_id, issued.principal.session_id)

    async def test_expired_and_revoked_sessions_fail_closed(self):
        expired = await self.service.create_session("valid-owner-identity")
        self.repository.sessions[expired.principal.session_token_hash].expires_at = utc_now() - timedelta(seconds=1)
        with self.assertRaises(DirectorAuthError) as caught:
            await self.service.validate_session(expired.token)
        self.assertEqual(caught.exception.code, "DIRECTOR_SESSION_EXPIRED")

        revoked = await self.service.create_session("valid-owner-identity")
        await self.service.revoke_session(revoked.principal)
        with self.assertRaises(DirectorAuthError) as caught:
            await self.service.validate_session(revoked.token)
        self.assertEqual(caught.exception.code, "DIRECTOR_SESSION_REVOKED")

    async def test_csrf_is_bound_to_session(self):
        issued = await self.service.create_session("valid-owner-identity")
        self.service.validate_csrf(issued.principal, issued.csrf_token)
        with self.assertRaises(DirectorAuthError) as caught:
            self.service.validate_csrf(issued.principal, "wrong-csrf")
        self.assertEqual(caught.exception.code, "DIRECTOR_CSRF_INVALID")

    async def test_realtime_ticket_is_short_lived_single_use_and_session_bound(self):
        issued = await self.service.create_session("valid-owner-identity")
        ticket = await self.service.issue_realtime_ticket(issued.principal)
        self.assertLessEqual(ticket.expires_at, utc_now() + timedelta(seconds=122))
        principal = await self.service.consume_realtime_ticket(ticket.ticket)
        self.assertEqual(principal.owner_uid, issued.principal.owner_uid)
        with self.assertRaises(DirectorAuthError):
            await self.service.consume_realtime_ticket(ticket.ticket)

        second = await self.service.issue_realtime_ticket(issued.principal)
        self.assertNotEqual(second.ticket, ticket.ticket)
        await self.service.revoke_session(issued.principal)
        with self.assertRaises(DirectorAuthError):
            await self.service.consume_realtime_ticket(second.ticket)

    async def test_expired_and_wrong_purpose_tickets_are_rejected(self):
        issued = await self.service.create_session("valid-owner-identity")
        expired = await self.service.issue_realtime_ticket(issued.principal)
        expired_record = list(self.repository.tickets.values())[-1]
        expired_record.expires_at = utc_now() - timedelta(seconds=1)
        with self.assertRaises(DirectorAuthError):
            await self.service.consume_realtime_ticket(expired.ticket)

        wrong = await self.service.issue_realtime_ticket(issued.principal)
        wrong_record = list(self.repository.tickets.values())[-1]
        wrong_record.purpose = "wrong_purpose"
        with self.assertRaises(DirectorAuthError):
            await self.service.consume_realtime_ticket(wrong.ticket)

    async def test_firebase_verifier_checks_signature_claims_and_uid_allowlist(self):
        verifier = FirebaseOwnerIdentityVerifier()
        env = {
            "FIREBASE_PROJECT_ID": "firebase-project-test",
            "NIE_OWNER_FIREBASE_UIDS": "allowed-owner",
        }
        claims = {
            "sub": "allowed-owner",
            "email": "owner@example.test",
            "email_verified": True,
        }
        with patch.dict(os.environ, env), patch(
            "app.services.director_auth_service.google_id_token.verify_firebase_token",
            return_value=claims,
        ):
            identity = await verifier.verify("signed-firebase-token")
        self.assertEqual(identity.uid, "allowed-owner")

        claims["sub"] = "different-owner"
        with patch.dict(os.environ, env), patch(
            "app.services.director_auth_service.google_id_token.verify_firebase_token",
            return_value=claims,
        ):
            with self.assertRaises(DirectorAuthError) as caught:
                await verifier.verify("signed-firebase-token")
        self.assertEqual(caught.exception.code, "DIRECTOR_OWNER_NOT_ALLOWED")


class DirectorAuthEndpointTests(unittest.TestCase):
    def setUp(self):
        self.repository = FakeDirectorAuthRepository()
        self.service = DirectorAuthService(self.repository, FakeIdentityVerifier())
        app.dependency_overrides[get_director_auth_service] = lambda: self.service
        self.client = TestClient(app)

    def tearDown(self):
        app.dependency_overrides.pop(get_director_auth_service, None)

    def create_session(self):
        response = self.client.post(
            "/api/v1/director/auth/session",
            headers={
                "Authorization": "Bearer valid-owner-identity",
                "Origin": TEST_ORIGIN,
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertTrue(body["csrf_token"])
        self.assertNotIn("valid-owner-identity", response.text)
        return body

    def test_identity_and_origin_are_required_for_session_creation(self):
        missing = self.client.post(
            "/api/v1/director/auth/session",
            headers={"Origin": TEST_ORIGIN},
        )
        self.assertEqual(missing.status_code, 401)
        invalid = self.client.post(
            "/api/v1/director/auth/session",
            headers={"Authorization": "Bearer invalid", "Origin": TEST_ORIGIN},
        )
        self.assertEqual(invalid.status_code, 401)
        wrong_origin = self.client.post(
            "/api/v1/director/auth/session",
            headers={"Authorization": "Bearer valid-owner-identity", "Origin": "https://attacker.invalid"},
        )
        self.assertEqual(wrong_origin.status_code, 403)

    def test_transcription_requires_valid_session_and_csrf_then_preserves_multipart(self):
        unauthenticated = self.client.post(
            "/api/v1/director/voice/transcribe",
            files={"file": ("voice.webm", b"audio", "audio/webm")},
            headers={"Origin": TEST_ORIGIN, "X-CSRF-Token": "none"},
        )
        self.assertEqual(unauthenticated.status_code, 401)

        session = self.create_session()
        response_model = DirectorTranscriptionResponse(
            request_id="req_secure_auth",
            transcript="Secure transcription.",
            confidence=0.99,
            language="en",
            duration_ms=500,
        )
        with patch(
            "app.api.director_desktop.director_speech_service.transcribe",
            new_callable=AsyncMock,
            return_value=response_model,
        ) as transcribe:
            missing_csrf = self.client.post(
                "/api/v1/director/voice/transcribe",
                files={"file": ("voice.webm", b"audio", "audio/webm")},
                headers={"Origin": TEST_ORIGIN},
            )
            self.assertEqual(missing_csrf.status_code, 403)
            accepted = self.client.post(
                "/api/v1/director/voice/transcribe",
                files={"file": ("voice.webm", b"audio", "audio/webm")},
                headers={"Origin": TEST_ORIGIN, "X-CSRF-Token": session["csrf_token"]},
            )
        self.assertEqual(accepted.status_code, 200, accepted.text)
        self.assertEqual(accepted.json()["transcript"], "Secure transcription.")
        self.assertEqual(transcribe.await_count, 1)
        upload = transcribe.await_args.args[0]
        self.assertEqual(upload.content_type, "audio/webm")

    def test_ticket_requires_session_and_csrf_and_logout_revokes_both(self):
        unauthenticated = self.client.post(
            "/api/v1/director/auth/realtime-ticket",
            headers={"Origin": TEST_ORIGIN, "X-CSRF-Token": "none"},
        )
        self.assertEqual(unauthenticated.status_code, 401)
        session = self.create_session()
        ticket = self.client.post(
            "/api/v1/director/auth/realtime-ticket",
            headers={"Origin": TEST_ORIGIN, "X-CSRF-Token": session["csrf_token"]},
        )
        self.assertEqual(ticket.status_code, 200, ticket.text)
        ticket_value = ticket.json()["ticket"]

        logout = self.client.delete(
            "/api/v1/director/auth/session",
            headers={"Origin": TEST_ORIGIN, "X-CSRF-Token": session["csrf_token"]},
        )
        self.assertEqual(logout.status_code, 200)
        with self.assertRaises(DirectorAuthError):
            import asyncio
            asyncio.run(self.service.consume_realtime_ticket(ticket_value))

    def test_valid_ticket_authenticates_websocket_once(self):
        session = self.create_session()
        ticket = self.client.post(
            "/api/v1/director/auth/realtime-ticket",
            headers={"Origin": TEST_ORIGIN, "X-CSRF-Token": session["csrf_token"]},
        ).json()["ticket"]

        async def finish_connection(websocket, owner_id, already_accepted=False):
            self.assertTrue(already_accepted)
            self.assertEqual(owner_id, "firebase:firebase-owner-uid")
            await websocket.send_json({"type": "session.ready"})
            await websocket.close()

        with patch(
            "app.api.director_desktop.director_realtime_voice_service.handle_connection",
            new_callable=AsyncMock,
            side_effect=finish_connection,
        ) as handler:
            with self.client.websocket_connect(
                "/api/v1/director/voice/realtime",
                subprotocols=["nie-director-v1"],
                headers={"Origin": TEST_ORIGIN},
            ) as websocket:
                websocket.send_json({"type": "session.authenticate", "ticket": ticket})
                ready = websocket.receive_json()
                self.assertEqual(ready["type"], "session.ready")
        self.assertEqual(handler.await_count, 1)

        with self.client.websocket_connect(
            "/api/v1/director/voice/realtime",
            subprotocols=["nie-director-v1"],
            headers={"Origin": TEST_ORIGIN},
        ) as replay:
            replay.send_json({"type": "session.authenticate", "ticket": ticket})
            self.assertEqual(replay.receive_json()["detail"], "DIRECTOR_REALTIME_TICKET_INVALID")


class DirectorAuthFailClosedTests(unittest.TestCase):
    def test_missing_production_owner_key_and_origins_fail_closed(self):
        with patch.dict(os.environ, {"NIE_ENV": "production"}, clear=False):
            with patch.dict(os.environ, {"NIE_OWNER_KEY": ""}):
                with self.assertRaises(HTTPException) as caught:
                    verify_owner_key_token("anything")
                self.assertEqual(caught.exception.status_code, 503)
            with patch.dict(os.environ, {"NIE_TRUSTED_FRONTEND_ORIGINS": ""}):
                self.assertEqual(trusted_frontend_origins(), ())


if __name__ == "__main__":
    unittest.main()
