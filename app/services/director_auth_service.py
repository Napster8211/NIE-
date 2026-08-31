import asyncio
import hashlib
import os
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from google.auth.exceptions import TransportError
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2 import id_token as google_id_token

from app.repositories.director_auth_repository import (
    DirectorAuthRepository,
    director_auth_repository,
)
from app.schemas.director_auth import DirectorPrincipal
from app.services.authorization import verify_owner_key_token


DIRECTOR_SESSION_COOKIE = "nie_director_session"
DIRECTOR_REALTIME_PURPOSE = "director_realtime"
_optional_bearer = HTTPBearer(auto_error=False)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def runtime_environment() -> str:
    configured = os.getenv("NIE_ENV") or os.getenv("ENVIRONMENT")
    if configured:
        return configured.strip().lower()
    if os.getenv("RENDER", "").strip().lower() in {"1", "true", "yes"}:
        return "production"
    return "development"


def is_production() -> bool:
    return runtime_environment() in {"production", "prod"}


def trusted_frontend_origins() -> tuple[str, ...]:
    configured = os.getenv("NIE_TRUSTED_FRONTEND_ORIGINS", "")
    origins = tuple(
        item.strip().rstrip("/")
        for item in configured.split(",")
        if item.strip()
    )
    if origins:
        return origins
    if is_production():
        return ()
    return ("http://localhost:5173", "http://127.0.0.1:5173")


def session_ttl_seconds() -> int:
    requested = int(os.getenv("DIRECTOR_SESSION_TTL_SECONDS", "1800"))
    return max(300, min(requested, 86_400))


def realtime_ticket_ttl_seconds() -> int:
    requested = int(os.getenv("DIRECTOR_REALTIME_TICKET_TTL_SECONDS", "45"))
    return max(10, min(requested, 120))


def session_cookie_secure() -> bool:
    configured = os.getenv("DIRECTOR_SESSION_COOKIE_SECURE")
    if configured is not None:
        return configured.strip().lower() in {"1", "true", "yes"}
    return is_production()


def session_cookie_samesite() -> str:
    configured = os.getenv("DIRECTOR_SESSION_COOKIE_SAMESITE")
    value = (configured or ("none" if is_production() else "lax")).strip().lower()
    if value not in {"lax", "strict", "none"}:
        raise RuntimeError("DIRECTOR_COOKIE_SAMESITE_INVALID")
    if value == "none" and not session_cookie_secure():
        raise RuntimeError("DIRECTOR_COOKIE_SECURE_REQUIRED")
    return value


def _digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class DirectorAuthError(Exception):
    def __init__(self, code: str, http_status: int):
        super().__init__(code)
        self.code = code
        self.http_status = http_status


@dataclass(frozen=True)
class VerifiedOwnerIdentity:
    uid: str
    email: Optional[str]


@dataclass(frozen=True)
class IssuedDirectorSession:
    token: str
    csrf_token: str
    principal: DirectorPrincipal


@dataclass(frozen=True)
class IssuedRealtimeTicket:
    ticket: str
    expires_at: datetime


class FirebaseOwnerIdentityVerifier:
    """Verifies Firebase assertions and applies a server-side UID allowlist."""

    async def verify(self, assertion: str) -> VerifiedOwnerIdentity:
        project_id = os.getenv("FIREBASE_PROJECT_ID", "").strip()
        allowlisted_uids = {
            item.strip()
            for item in os.getenv("NIE_OWNER_FIREBASE_UIDS", "").split(",")
            if item.strip()
        }
        if not project_id or not allowlisted_uids:
            raise DirectorAuthError("DIRECTOR_IDENTITY_NOT_CONFIGURED", status.HTTP_503_SERVICE_UNAVAILABLE)
        if not assertion:
            raise DirectorAuthError("DIRECTOR_IDENTITY_REQUIRED", status.HTTP_401_UNAUTHORIZED)

        try:
            claims: dict[str, Any] = await asyncio.to_thread(
                google_id_token.verify_firebase_token,
                assertion,
                GoogleAuthRequest(),
                project_id,
            )
        except TransportError as exc:
            raise DirectorAuthError(
                "DIRECTOR_IDENTITY_PROVIDER_UNAVAILABLE",
                status.HTTP_503_SERVICE_UNAVAILABLE,
            ) from exc
        except (ValueError, TypeError) as exc:
            raise DirectorAuthError("DIRECTOR_IDENTITY_INVALID", status.HTTP_401_UNAUTHORIZED) from exc

        uid = str(claims.get("sub") or claims.get("user_id") or "").strip()
        if not uid:
            raise DirectorAuthError("DIRECTOR_IDENTITY_INVALID", status.HTTP_401_UNAUTHORIZED)
        if uid not in allowlisted_uids:
            raise DirectorAuthError("DIRECTOR_OWNER_NOT_ALLOWED", status.HTTP_403_FORBIDDEN)

        email = claims.get("email")
        if email and claims.get("email_verified") is not True:
            raise DirectorAuthError("DIRECTOR_EMAIL_NOT_VERIFIED", status.HTTP_403_FORBIDDEN)
        return VerifiedOwnerIdentity(uid=uid, email=str(email) if email else None)


class DirectorAuthService:
    def __init__(
        self,
        repository: DirectorAuthRepository = director_auth_repository,
        identity_verifier: Optional[FirebaseOwnerIdentityVerifier] = None,
    ):
        self.repository = repository
        self.identity_verifier = identity_verifier or FirebaseOwnerIdentityVerifier()

    async def create_session(self, assertion: str) -> IssuedDirectorSession:
        identity = await self.identity_verifier.verify(assertion)
        token = secrets.token_urlsafe(32)
        csrf_token = secrets.token_urlsafe(32)
        session_id = f"dss_{secrets.token_hex(12)}"
        now = utc_now()
        expires_at = now + timedelta(seconds=session_ttl_seconds())
        try:
            await self.repository.create_session(
                token_hash=_digest(token),
                session_id=session_id,
                owner_uid=identity.uid,
                owner_email=identity.email,
                csrf_token_hash=_digest(csrf_token),
                created_at=now,
                expires_at=expires_at,
                last_seen_at=now,
            )
        except Exception as exc:
            raise DirectorAuthError(
                "DIRECTOR_SESSION_STORAGE_UNAVAILABLE",
                status.HTTP_503_SERVICE_UNAVAILABLE,
            ) from exc

        principal = DirectorPrincipal(
            owner_id=f"firebase:{identity.uid}",
            owner_uid=identity.uid,
            owner_email=identity.email,
            auth_method="director_session",
            session_id=session_id,
            session_token_hash=_digest(token),
            csrf_token_hash=_digest(csrf_token),
            expires_at=expires_at,
        )
        return IssuedDirectorSession(token=token, csrf_token=csrf_token, principal=principal)

    async def validate_session(self, token: str) -> DirectorPrincipal:
        if not token:
            raise DirectorAuthError("DIRECTOR_SESSION_REQUIRED", status.HTTP_401_UNAUTHORIZED)
        token_hash = _digest(token)
        try:
            record = await self.repository.get_session(token_hash)
        except Exception as exc:
            raise DirectorAuthError(
                "DIRECTOR_SESSION_STORAGE_UNAVAILABLE",
                status.HTTP_503_SERVICE_UNAVAILABLE,
            ) from exc
        now = utc_now()
        if record is None:
            raise DirectorAuthError("DIRECTOR_SESSION_INVALID", status.HTTP_401_UNAUTHORIZED)
        if record.revoked_at is not None:
            raise DirectorAuthError("DIRECTOR_SESSION_REVOKED", status.HTTP_401_UNAUTHORIZED)
        if _as_utc(record.expires_at) <= now:
            raise DirectorAuthError("DIRECTOR_SESSION_EXPIRED", status.HTTP_401_UNAUTHORIZED)
        return DirectorPrincipal(
            owner_id=f"firebase:{record.owner_uid}",
            owner_uid=record.owner_uid,
            owner_email=record.owner_email,
            auth_method="director_session",
            session_id=record.session_id,
            session_token_hash=token_hash,
            csrf_token_hash=record.csrf_token_hash,
            expires_at=_as_utc(record.expires_at),
        )

    def validate_csrf(self, principal: DirectorPrincipal, csrf_token: str) -> None:
        if (
            not csrf_token
            or not principal.csrf_token_hash
            or not secrets.compare_digest(_digest(csrf_token), principal.csrf_token_hash)
        ):
            raise DirectorAuthError("DIRECTOR_CSRF_INVALID", status.HTTP_403_FORBIDDEN)

    async def revoke_session(self, principal: DirectorPrincipal) -> None:
        if not principal.session_token_hash:
            raise DirectorAuthError("DIRECTOR_SESSION_REQUIRED", status.HTTP_401_UNAUTHORIZED)
        try:
            await self.repository.revoke_session(principal.session_token_hash, utc_now())
        except Exception as exc:
            raise DirectorAuthError(
                "DIRECTOR_SESSION_STORAGE_UNAVAILABLE",
                status.HTTP_503_SERVICE_UNAVAILABLE,
            ) from exc

    async def issue_realtime_ticket(self, principal: DirectorPrincipal) -> IssuedRealtimeTicket:
        if principal.auth_method != "director_session" or not principal.session_id:
            raise DirectorAuthError("DIRECTOR_SESSION_REQUIRED", status.HTTP_401_UNAUTHORIZED)
        ticket = secrets.token_urlsafe(32)
        ticket_id = f"drt_{secrets.token_hex(10)}"
        now = utc_now()
        expires_at = now + timedelta(seconds=realtime_ticket_ttl_seconds())
        try:
            await self.repository.create_ticket(
                ticket_hash=_digest(ticket),
                ticket_id=ticket_id,
                session_id=principal.session_id,
                owner_uid=principal.owner_uid,
                purpose=DIRECTOR_REALTIME_PURPOSE,
                created_at=now,
                expires_at=expires_at,
            )
        except Exception as exc:
            raise DirectorAuthError(
                "DIRECTOR_SESSION_STORAGE_UNAVAILABLE",
                status.HTTP_503_SERVICE_UNAVAILABLE,
            ) from exc
        return IssuedRealtimeTicket(ticket=ticket, expires_at=expires_at)

    async def consume_realtime_ticket(self, ticket: str) -> DirectorPrincipal:
        if not ticket:
            raise DirectorAuthError("DIRECTOR_REALTIME_TICKET_REQUIRED", status.HTTP_401_UNAUTHORIZED)
        try:
            record = await self.repository.consume_ticket(
                _digest(ticket),
                DIRECTOR_REALTIME_PURPOSE,
                utc_now(),
            )
        except Exception as exc:
            raise DirectorAuthError(
                "DIRECTOR_SESSION_STORAGE_UNAVAILABLE",
                status.HTTP_503_SERVICE_UNAVAILABLE,
            ) from exc
        if record is None:
            raise DirectorAuthError("DIRECTOR_REALTIME_TICKET_INVALID", status.HTTP_401_UNAUTHORIZED)
        return DirectorPrincipal(
            owner_id=f"firebase:{record.owner_uid}",
            owner_uid=record.owner_uid,
            auth_method="realtime_ticket",
            session_id=record.session_id,
        )


director_auth_service = DirectorAuthService()


def get_director_auth_service() -> DirectorAuthService:
    return director_auth_service


def validate_origin_value(origin: str) -> None:
    origin = (origin or "").rstrip("/")
    trusted = trusted_frontend_origins()
    if not trusted:
        raise DirectorAuthError("DIRECTOR_TRUSTED_ORIGINS_NOT_CONFIGURED", status.HTTP_503_SERVICE_UNAVAILABLE)
    if not origin or origin not in trusted:
        raise DirectorAuthError("DIRECTOR_ORIGIN_REJECTED", status.HTTP_403_FORBIDDEN)


def validate_trusted_origin(request: Request) -> None:
    validate_origin_value(request.headers.get("origin") or "")


def _raise_http(error: DirectorAuthError) -> None:
    raise HTTPException(status_code=error.http_status, detail=error.code)


async def require_director_session(
    request: Request,
    service: DirectorAuthService = Depends(get_director_auth_service),
) -> DirectorPrincipal:
    try:
        return await service.validate_session(request.cookies.get(DIRECTOR_SESSION_COOKIE, ""))
    except DirectorAuthError as error:
        _raise_http(error)


async def require_director_access(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_optional_bearer),
    service: DirectorAuthService = Depends(get_director_auth_service),
) -> DirectorPrincipal:
    session_token = request.cookies.get(DIRECTOR_SESSION_COOKIE, "")
    if session_token:
        try:
            return await service.validate_session(session_token)
        except DirectorAuthError as error:
            _raise_http(error)
    if credentials:
        owner_id = verify_owner_key_token(credentials.credentials)
        return DirectorPrincipal(
            owner_id=owner_id,
            owner_uid=owner_id,
            auth_method="owner_key",
        )
    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="DIRECTOR_AUTH_REQUIRED")


async def require_director_mutation(
    request: Request,
    principal: DirectorPrincipal = Depends(require_director_access),
    service: DirectorAuthService = Depends(get_director_auth_service),
) -> DirectorPrincipal:
    if principal.auth_method == "director_session":
        try:
            validate_trusted_origin(request)
            service.validate_csrf(principal, request.headers.get("x-csrf-token", ""))
        except DirectorAuthError as error:
            _raise_http(error)
    return principal


async def require_browser_mutation(
    request: Request,
    principal: DirectorPrincipal = Depends(require_director_session),
    service: DirectorAuthService = Depends(get_director_auth_service),
) -> DirectorPrincipal:
    try:
        validate_trusted_origin(request)
        service.validate_csrf(principal, request.headers.get("x-csrf-token", ""))
    except DirectorAuthError as error:
        _raise_http(error)
    return principal
