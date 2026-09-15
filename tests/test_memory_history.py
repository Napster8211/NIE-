import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi import HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials

from app.api.memory import (
    get_messages,
    list_conversations,
    memory_owner_ids,
    resolve_memory_owner,
)
from app.services.director_auth_service import VerifiedOwnerIdentity


class FakeResult:
    def __init__(self, values=None, scalar=None):
        self.values = list(values or [])
        self.scalar = scalar

    def scalars(self):
        return self

    def all(self):
        return self.values

    def first(self):
        return self.values[0] if self.values else None

    def scalar_one_or_none(self):
        return self.scalar


class FakeSession:
    def __init__(self, *results):
        self.results = list(results)
        self.statements = []

    async def execute(self, statement):
        self.statements.append(statement)
        return self.results.pop(0)


def statement_values(statement) -> set[str]:
    values: set[str] = set()
    for value in statement.compile().params.values():
        if isinstance(value, tuple | list | set):
            values.update(str(item) for item in value)
        else:
            values.add(str(value))
    return values


class MemoryHistoryContractTests(unittest.IsolatedAsyncioTestCase):
    def request(self) -> Request:
        return Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/api/v1/memory/conversations",
                "headers": [(b"origin", b"http://localhost:5173")],
            }
        )

    async def test_authenticated_owner_lists_current_and_legacy_conversations(self):
        conversations = [SimpleNamespace(id="current"), SimpleNamespace(id="legacy")]
        db = FakeSession(FakeResult(conversations))
        with patch.dict(os.environ, {"NIE_OWNER_FIREBASE_UIDS": "owner"}, clear=False):
            result = await list_conversations(db=db, owner_id="firebase:owner")
        self.assertEqual(conversations, result)
        self.assertTrue({"firebase:owner", "local_user"} <= statement_values(db.statements[0]))

    async def test_conversation_returned_by_list_opens_with_messages(self):
        conversation = SimpleNamespace(id="conversation-1")
        messages = [SimpleNamespace(id="message-1", conversation_id=conversation.id)]
        list_db = FakeSession(FakeResult([conversation]))
        message_db = FakeSession(FakeResult(scalar=conversation.id), FakeResult(messages))
        with patch.dict(os.environ, {"NIE_OWNER_FIREBASE_UIDS": "owner"}, clear=False):
            listed = await list_conversations(db=list_db, owner_id="firebase:owner")
            loaded = await get_messages(listed[0].id, db=message_db, owner_id="firebase:owner")
        self.assertEqual(messages, loaded)

    async def test_empty_conversation_returns_empty_message_list(self):
        db = FakeSession(FakeResult(scalar="empty"), FakeResult([]))
        loaded = await get_messages("empty", db=db, owner_id="firebase:user")
        self.assertEqual([], loaded)

    async def test_nonexistent_conversation_returns_404(self):
        db = FakeSession(FakeResult(scalar=None))
        with self.assertRaises(HTTPException) as caught:
            await get_messages("missing", db=db, owner_id="firebase:user")
        self.assertEqual(404, caught.exception.status_code)
        self.assertEqual("Conversation not found", caught.exception.detail)

    async def test_unauthenticated_history_is_rejected(self):
        with self.assertRaises(HTTPException) as caught:
            await resolve_memory_owner(self.request(), None)
        self.assertEqual(401, caught.exception.status_code)
        self.assertEqual("CHAT_AUTH_REQUIRED", caught.exception.detail)

    async def test_verified_firebase_identity_is_authoritative(self):
        credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials="assertion")
        identity = VerifiedOwnerIdentity(uid="verified-user", email="user@example.test")
        settings = {
            "NIE_ENV": "test",
            "NIE_TRUSTED_FRONTEND_ORIGINS": "http://localhost:5173",
            "FIREBASE_PROJECT_ID": "test-project",
        }
        with (
            patch.dict(os.environ, settings, clear=True),
            patch("app.api.memory.verify_firebase_identity", new=AsyncMock(return_value=identity)),
        ):
            owner_id = await resolve_memory_owner(self.request(), credentials)
        self.assertEqual("firebase:verified-user", owner_id)

    async def test_cross_user_cannot_probe_another_conversation(self):
        db = FakeSession(FakeResult(scalar=None))
        with patch.dict(os.environ, {"NIE_OWNER_FIREBASE_UIDS": "owner"}, clear=False):
            with self.assertRaises(HTTPException) as caught:
                await get_messages("owner-conversation", db=db, owner_id="firebase:other-user")
        self.assertEqual(404, caught.exception.status_code)
        values = statement_values(db.statements[0])
        self.assertIn("firebase:other-user", values)
        self.assertNotIn("firebase:owner", values)
        self.assertNotIn("local_user", values)

    def test_legacy_alias_is_available_only_to_one_configured_owner(self):
        with patch.dict(os.environ, {"NIE_OWNER_FIREBASE_UIDS": "owner"}, clear=False):
            self.assertEqual(("firebase:owner", "local_user"), memory_owner_ids("firebase:owner"))
            self.assertEqual(("firebase:other",), memory_owner_ids("firebase:other"))
        with patch.dict(os.environ, {"NIE_OWNER_FIREBASE_UIDS": "owner,second-owner"}, clear=False):
            self.assertEqual(("firebase:owner",), memory_owner_ids("firebase:owner"))


if __name__ == "__main__":
    unittest.main()
