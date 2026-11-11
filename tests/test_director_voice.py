import os
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient

TEST_NIE_OWNER_KEY = "explicit-test-only-owner-key"
os.environ.setdefault("NIE_ENV", "test")
os.environ["NIE_OWNER_KEY"] = TEST_NIE_OWNER_KEY

from app.main import app
from app.services.director_voice_service import (
    DirectorAudioResult,
    director_voice_service,
)


class _MockResponse:
    def __init__(self, status_code=200, content=b"fake-mp3-bytes", json_data=None):
        self.status_code = status_code
        self.content = content
        self._json_data = json_data or {}
        self.headers = {"Content-Type": "audio/mpeg"}

    def json(self):
        return self._json_data


class _MockClient:
    def __init__(self, response=None):
        self.is_closed = False
        self.post = AsyncMock(return_value=response or _MockResponse())


class TestDirectorVoice(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.headers = {"Authorization": f"Bearer {TEST_NIE_OWNER_KEY}"}
        self.env_patcher = patch.dict(os.environ, {
            "ELEVENLABS_API_KEY": "test_elevenlabs_api_key_12345",
            "ELEVENLABS_VOICE_ID": "test_voice_id_67890",
            "ELEVENLABS_MODEL_ID": "eleven_multilingual_v2",
            "ELEVENLABS_TIMEOUT_SECONDS": "15",
        })
        self.env_patcher.start()
        self.previous_http_client = director_voice_service._http_client
        self.mock_client = _MockClient()
        director_voice_service._http_client = self.mock_client

    def tearDown(self):
        director_voice_service._http_client = self.previous_http_client
        self.env_patcher.stop()

    def test_valid_briefing_produces_audio(self):
        res = self.client.post("/api/v1/director/voice/speak", json={
            "briefing_type": "COMPANY_STATUS"
        }, headers=self.headers)

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.headers["content-type"], "audio/mpeg")
        self.assertEqual(res.content, b"fake-mp3-bytes")

        call = self.mock_client.post.await_args
        self.assertEqual(call.args[0], "https://api.elevenlabs.io/v1/text-to-speech/test_voice_id_67890")
        self.assertEqual(call.kwargs["headers"]["xi-api-key"], "test_elevenlabs_api_key_12345")
        self.assertIn("The Intelligence Engine is", call.kwargs["json"]["text"])
        self.assertNotIn("test_elevenlabs_api_key_12345", res.text)

    def test_raw_text_synthesis(self):
        res = self.client.post("/api/v1/director/voice/speak", json={
            "briefing_type": "RAW",
            "text": "Hello Sayibu, Director is online."
        }, headers=self.headers)

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.content, b"fake-mp3-bytes")
        call = self.mock_client.post.await_args
        self.assertEqual(call.kwargs["json"]["text"], "Hello Sayibu, Director is online.")

    def test_missing_api_key_returns_safe_error(self):
        with patch.dict(os.environ, {"ELEVENLABS_API_KEY": ""}):
            res = self.client.post("/api/v1/director/voice/speak", json={
                "briefing_type": "COMPANY_STATUS"
            }, headers=self.headers)

            self.assertEqual(res.status_code, 503)
            self.assertIn("VOICE_NOT_CONFIGURED", res.json()["detail"])
            self.mock_client.post.assert_not_awaited()

    def test_missing_voice_id_returns_safe_error(self):
        with patch.dict(os.environ, {"ELEVENLABS_VOICE_ID": ""}):
            res = self.client.post("/api/v1/director/voice/speak", json={
                "briefing_type": "COMPANY_STATUS"
            }, headers=self.headers)

            self.assertEqual(res.status_code, 503)
            self.assertIn("VOICE_NOT_CONFIGURED", res.json()["detail"])

    def test_elevenlabs_unauthorized(self):
        self.mock_client.post.return_value = _MockResponse(status_code=401)
        res = self.client.post("/api/v1/director/voice/speak", json={
            "briefing_type": "RAW",
            "text": "Auth test"
        }, headers=self.headers)

        self.assertEqual(res.status_code, 502)
        self.assertEqual(res.json()["detail"], "ELEVENLABS_UNAUTHORIZED")
        self.assertNotIn("test_elevenlabs_api_key_12345", res.text)

    def test_elevenlabs_paid_plan_required(self):
        self.mock_client.post.return_value = _MockResponse(
            status_code=402,
            json_data={"detail": {"code": "paid_plan_required", "message": "Upgrade required"}}
        )
        res = self.client.post("/api/v1/director/voice/speak", json={
            "briefing_type": "RAW",
            "text": "Plan test"
        }, headers=self.headers)

        self.assertEqual(res.status_code, 402)
        self.assertEqual(res.json()["detail"], "ELEVENLABS_PAID_PLAN_REQUIRED")

    def test_elevenlabs_rate_limit(self):
        self.mock_client.post.return_value = _MockResponse(status_code=429)
        res = self.client.post("/api/v1/director/voice/speak", json={
            "briefing_type": "RAW",
            "text": "Rate limit test"
        }, headers=self.headers)

        self.assertEqual(res.status_code, 429)
        self.assertEqual(res.json()["detail"], "ELEVENLABS_RATE_LIMITED")

    def test_elevenlabs_timeout(self):
        self.mock_client.post.side_effect = httpx.ReadTimeout("Elevenlabs timed out")
        res = self.client.post("/api/v1/director/voice/speak", json={
            "briefing_type": "RAW",
            "text": "Timeout test"
        }, headers=self.headers)

        self.assertEqual(res.status_code, 504)
        self.assertEqual(res.json()["detail"], "ELEVENLABS_TIMEOUT")

    def test_empty_speech_text_rejected(self):
        res = self.client.post("/api/v1/director/voice/speak", json={
            "briefing_type": "RAW",
            "text": "    "
        }, headers=self.headers)

        self.assertEqual(res.status_code, 422)
        self.assertEqual(res.json()["detail"], "EMPTY_SPEECH_TEXT")


if __name__ == "__main__":
    unittest.main()
