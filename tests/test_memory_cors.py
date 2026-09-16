import os
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient

os.environ.setdefault("NIE_ENV", "test")
os.environ.setdefault("NIE_TRUSTED_FRONTEND_ORIGINS", "http://localhost:5173")

from app.api.memory import resolve_memory_owner
from app.database import get_db_session
from app.main import ApplicationCORSMiddleware, app
from app.models.memory_models import Conversation, Message
from app.schemas.memory_schemas import MessageResponse
from app.services.director_auth_service import trusted_frontend_origins

APPROVED_ORIGIN = "http://localhost:5173"


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
        self.added = []

    async def execute(self, _statement):
        return self.results.pop(0)

    def add(self, value):
        self.added.append(value)

    async def commit(self):
        return None

    async def refresh(self, value):
        if getattr(value, "id", None) is None:
            value.id = "generated-message"
        if getattr(value, "created_at", None) is None:
            value.created_at = datetime.now(timezone.utc)
        if getattr(value, "updated_at", None) is None and isinstance(value, Conversation):
            value.updated_at = datetime.now(timezone.utc)


def conversation() -> Conversation:
    now = datetime.now(timezone.utc)
    return Conversation(
        id="conversation-1",
        user_id="firebase:owner",
        title="Original title",
        created_at=now,
        updated_at=now,
    )


class MemoryCorsTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app, raise_server_exceptions=False)

    def tearDown(self):
        app.dependency_overrides.clear()

    def headers(self):
        return {
            "Origin": APPROVED_ORIGIN,
            "Authorization": "Bearer deterministic-test-assertion",
        }

    def use_owner(self):
        app.dependency_overrides[resolve_memory_owner] = lambda: "firebase:owner"

    def use_db(self, session):
        app.dependency_overrides[get_db_session] = lambda: session

    def test_application_uses_global_cors_wrapper(self):
        self.assertIsInstance(app, ApplicationCORSMiddleware)

    def test_production_origin_parser_is_exact_and_normalized(self):
        with patch.dict(
            os.environ,
            {"NIE_TRUSTED_FRONTEND_ORIGINS": " https://napstertecai.vercel.app/ "},
            clear=False,
        ):
            self.assertEqual(("https://napstertecai.vercel.app",), trusted_frontend_origins())

    def test_approved_origin_preflights_never_require_authentication(self):
        for method, path in (
            ("GET", "/api/v1/memory/conversations/conversation-1/messages"),
            ("POST", "/api/v1/memory/conversations/conversation-1/messages"),
            ("PUT", "/api/v1/memory/conversations/conversation-1"),
        ):
            with self.subTest(method=method):
                response = self.client.options(
                    path,
                    headers={
                        "Origin": APPROVED_ORIGIN,
                        "Access-Control-Request-Method": method,
                        "Access-Control-Request-Headers": "authorization,content-type",
                    },
                )
                self.assertEqual(200, response.status_code)
                self.assertEqual(APPROVED_ORIGIN, response.headers["access-control-allow-origin"])
                self.assertIn(method, response.headers["access-control-allow-methods"])
                self.assertIn("Authorization", response.headers["access-control-allow-headers"])
                self.assertEqual("true", response.headers["access-control-allow-credentials"])

    def test_approved_origin_authenticated_get(self):
        self.client = TestClient(app)
        message = Message(
            id="message-1",
            conversation_id="conversation-1",
            role="assistant",
            content="Hello",
            tokens_used=0,
            status="COMPLETED",
            message_metadata={},
            created_at=datetime.now(timezone.utc),
        )
        self.use_owner()
        self.use_db(FakeSession(FakeResult(scalar="conversation-1"), FakeResult([message])))
        response = self.client.get(
            "/api/v1/memory/conversations/conversation-1/messages",
            headers=self.headers(),
        )
        self.assertEqual(200, response.status_code)
        self.assertEqual(APPROVED_ORIGIN, response.headers["access-control-allow-origin"])
        self.assertEqual("message-1", response.json()[0]["id"])

    def test_message_schema_reads_orm_metadata_without_class_metadata_collision(self):
        message = Message(
            id="message-1",
            conversation_id="conversation-1",
            role="assistant",
            content="Hello",
            tokens_used=0,
            status="COMPLETED",
            message_metadata={"source": "memory"},
            created_at=datetime.now(timezone.utc),
        )
        payload = MessageResponse.model_validate(message).model_dump(by_alias=True)
        self.assertEqual({"source": "memory"}, payload["metadata"])

    def test_approved_origin_authenticated_post(self):
        self.client = TestClient(app)
        self.use_owner()
        self.use_db(FakeSession(FakeResult([conversation()])))
        response = self.client.post(
            "/api/v1/memory/conversations/conversation-1/messages",
            headers=self.headers(),
            json={"role": "user", "content": "Hello"},
        )
        self.assertEqual(200, response.status_code)
        self.assertEqual(APPROVED_ORIGIN, response.headers["access-control-allow-origin"])
        self.assertEqual("generated-message", response.json()["id"])

    def test_approved_origin_authenticated_title_update(self):
        self.client = TestClient(app)
        item = conversation()
        self.use_owner()
        self.use_db(FakeSession(FakeResult([item])))
        response = self.client.put(
            "/api/v1/memory/conversations/conversation-1",
            headers=self.headers(),
            json={"title": "Updated title"},
        )
        self.assertEqual(200, response.status_code)
        self.assertEqual(APPROVED_ORIGIN, response.headers["access-control-allow-origin"])
        self.assertIn("title", response.json(), response.text)
        self.assertEqual("Updated title", response.json()["title"])

    def test_approved_origin_401_has_cors_headers(self):
        self.use_db(FakeSession())
        response = self.client.get(
            "/api/v1/memory/conversations/conversation-1/messages",
            headers={"Origin": APPROVED_ORIGIN},
        )
        self.assertEqual(401, response.status_code)
        self.assertEqual("CHAT_AUTH_REQUIRED", response.json()["detail"])
        self.assertEqual(APPROVED_ORIGIN, response.headers["access-control-allow-origin"])

    def test_approved_origin_404_has_cors_headers(self):
        self.use_owner()
        self.use_db(FakeSession(FakeResult(scalar=None)))
        response = self.client.get(
            "/api/v1/memory/conversations/missing/messages",
            headers=self.headers(),
        )
        self.assertEqual(404, response.status_code)
        self.assertEqual(APPROVED_ORIGIN, response.headers["access-control-allow-origin"])

    def test_title_update_rejects_cross_user_conversation(self):
        self.use_owner()
        self.use_db(FakeSession(FakeResult([])))
        response = self.client.put(
            "/api/v1/memory/conversations/other-user-conversation",
            headers=self.headers(),
            json={"title": "Cannot change"},
        )
        self.assertEqual(404, response.status_code)
        self.assertEqual(APPROVED_ORIGIN, response.headers["access-control-allow-origin"])

    def test_unhandled_error_has_cors_headers(self):
        self.use_owner()
        app.dependency_overrides[get_db_session] = lambda: (_ for _ in ()).throw(RuntimeError("test"))
        response = self.client.get(
            "/api/v1/memory/conversations/conversation-1/messages",
            headers=self.headers(),
        )
        self.assertEqual(500, response.status_code)
        self.assertEqual(APPROVED_ORIGIN, response.headers["access-control-allow-origin"])

    def test_unapproved_origin_receives_no_cors_authorization(self):
        response = self.client.options(
            "/api/v1/memory/conversations/conversation-1/messages",
            headers={
                "Origin": "https://unapproved.example",
                "Access-Control-Request-Method": "GET",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )
        self.assertEqual(400, response.status_code)
        self.assertNotIn("access-control-allow-origin", response.headers)


if __name__ == "__main__":
    unittest.main()
