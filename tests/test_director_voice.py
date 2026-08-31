import base64
import os
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient

TEST_NIE_OWNER_KEY = "explicit-test-only-owner-key"
os.environ.setdefault("NIE_ENV", "test")
os.environ["NIE_OWNER_KEY"] = TEST_NIE_OWNER_KEY

from app.main import app
from app.services.director_interaction_service import director_interaction_service
from app.services.director_realtime_voice_service import (
    DirectorVoiceSession,
    director_realtime_voice_service,
)
from app.services.director_voice_service import (
    DirectorAudioResult,
    director_voice_service,
)


class _GatewayResponse:
    def __init__(self, status_code=200, content=b"RIFF-test-wav", headers=None):
        self.status_code = status_code
        self.content = content
        self.headers = headers or {
            "X-Audio-Format": "wav",
            "X-Sample-Rate": "16000",
            "X-Channels": "1",
        }


class _GatewayClient:
    def __init__(self):
        self.is_closed = False
        self.post = AsyncMock(return_value=_GatewayResponse())


class _WebSocketRecorder:
    def __init__(self):
        self.messages = []

    async def send_json(self, message):
        self.messages.append(message)

class TestDirectorVoice(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.headers = {"Authorization": f"Bearer {TEST_NIE_OWNER_KEY}"}
        self.environment = patch.dict(os.environ, {
            "VOICE_GATEWAY_URL": "https://voice-gateway.test/",
            "VOICE_GATEWAY_API_KEY": "internal-test-secret",
            "VOICE_GATEWAY_TIMEOUT_SECONDS": "45",
            "DIRECTOR_PIPER_SAMPLE_RATE": "16000",
        })
        self.environment.start()
        self.previous_http_client = director_voice_service._http_client
        self.gateway_client = _GatewayClient()
        director_voice_service._http_client = self.gateway_client

    def tearDown(self):
        director_voice_service._http_client = self.previous_http_client
        self.environment.stop()

    def test_gateway_request_headers_key_and_wav_bytes(self):
        res = self.client.post("/api/v1/director/voice/speak", json={
            "briefing_type": "RAW",
            "text": "  Director   gateway test.  ",
        }, headers=self.headers)

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.headers["content-type"], "audio/wav")
        self.assertEqual(res.content, b"RIFF-test-wav")
        call = self.gateway_client.post.await_args
        self.assertEqual(call.args[0], "https://voice-gateway.test/api/v1/tts/wav")
        self.assertEqual(call.kwargs["json"], {"text": "Director gateway test."})
        self.assertEqual(call.kwargs["headers"]["X-NapsterTec-Key"], "internal-test-secret")
        self.assertEqual(call.kwargs["headers"]["Accept"], "audio/wav")
        self.assertNotIn("internal-test-secret", res.content.decode(errors="ignore"))

    def test_company_status_still_resolves_canonical_briefing_text(self):
        res = self.client.post("/api/v1/director/voice/speak", json={
            "briefing_type": "COMPANY_STATUS",
        }, headers=self.headers)
        self.assertEqual(res.status_code, 200)
        called_text = self.gateway_client.post.await_args.kwargs["json"]["text"]
        self.assertIn("The Intelligence Engine is", called_text)

    def test_missing_gateway_configuration_returns_safe_error(self):
        os.environ["VOICE_GATEWAY_API_KEY"] = ""
        res = self.client.post("/api/v1/director/voice/speak", json={
            "briefing_type": "COMPANY_STATUS"
        }, headers=self.headers)

        self.assertEqual(res.status_code, 503)
        self.assertIn("VOICE_NOT_CONFIGURED", res.json()["detail"])
        self.gateway_client.post.assert_not_awaited()

    def test_gateway_auth_failures_are_safe_and_mapped_to_bad_gateway(self):
        for status in (401, 403):
            with self.subTest(status=status):
                self.gateway_client.post.reset_mock()
                self.gateway_client.post.return_value = _GatewayResponse(status_code=status)
                res = self.client.post("/api/v1/director/voice/speak", json={
                    "briefing_type": "RAW",
                    "text": "Authorization check",
                }, headers=self.headers)
                self.assertEqual(res.status_code, 502)
                self.assertEqual(res.json()["detail"], "VOICE_GATEWAY_UNAUTHORIZED")
                self.assertNotIn("internal-test-secret", res.text)

    def test_gateway_rate_limit_maps_to_429(self):
        self.gateway_client.post.return_value = _GatewayResponse(status_code=429)
        res = self.client.post("/api/v1/director/voice/speak", json={
            "briefing_type": "RAW",
            "text": "Rate limit check",
        }, headers=self.headers)
        self.assertEqual(res.status_code, 429)
        self.assertEqual(res.json()["detail"], "VOICE_GATEWAY_RATE_LIMITED")

    def test_gateway_timeout_maps_to_504(self):
        self.gateway_client.post.side_effect = httpx.ReadTimeout("bounded timeout")
        res = self.client.post("/api/v1/director/voice/speak", json={
            "briefing_type": "RAW",
            "text": "Timeout check",
        }, headers=self.headers)
        self.assertEqual(res.status_code, 504)
        self.assertEqual(res.json()["detail"], "VOICE_GATEWAY_TIMEOUT")

    def test_empty_gateway_audio_fails_closed(self):
        self.gateway_client.post.return_value = _GatewayResponse(content=b"")
        res = self.client.post("/api/v1/director/voice/speak", json={
            "briefing_type": "RAW",
            "text": "Empty response check",
        }, headers=self.headers)
        self.assertEqual(res.status_code, 502)
        self.assertEqual(res.json()["detail"], "VOICE_GENERATION_FAILED")

    def test_invalid_briefing_rejected(self):
        res = self.client.post("/api/v1/director/voice/speak", json={
            "briefing_type": "DEPARTMENT",
            "target_id": "invalid_dept"
        }, headers=self.headers)

        self.assertEqual(res.status_code, 400)
        self.assertTrue(res.json()["detail"])


class TestDirectorRealtimeVoice(unittest.IsolatedAsyncioTestCase):
    async def test_realtime_emits_wav_with_gateway_sample_rate(self):
        websocket = _WebSocketRecorder()
        session = DirectorVoiceSession("session", "owner", websocket)
        session.status = "PROCESSING"
        session.current_turn_id = "turn-1"
        expected = DirectorAudioResult(b"RIFF-realtime", sample_rate=22050)

        with patch.object(
            director_voice_service,
            "synthesize_text",
            new=AsyncMock(return_value=expected),
        ):
            await director_realtime_voice_service._stream_synthesize_chunk(
                session,
                "turn-1",
                "Speak this.",
            )

        self.assertEqual(len(websocket.messages), 1)
        event = websocket.messages[0]
        self.assertEqual(event["audio_format"], "wav")
        self.assertEqual(event["sample_rate"], 22050)
        self.assertEqual(event["channels"], 1)
        self.assertEqual(base64.b64decode(event["audio_base64"]), b"RIFF-realtime")

    async def test_tts_failure_does_not_stop_text_streaming(self):
        websocket = _WebSocketRecorder()
        session = DirectorVoiceSession("session", "owner", websocket)
        session.status = "PROCESSING"
        session.current_turn_id = "turn-2"

        async def stream_tokens(**_kwargs):
            yield "The Director response remains available when speech fails."

        with patch.object(
            director_voice_service,
            "synthesize_text",
            new=AsyncMock(side_effect=ValueError("VOICE_GATEWAY_UNAVAILABLE")),
        ), patch.object(
            director_interaction_service,
            "stream_interaction",
            new=stream_tokens,
        ):
            await director_realtime_voice_service._execute_streaming_turn(
                session,
                "turn-2",
                "Give me an update",
            )

        event_types = [message["type"] for message in websocket.messages]
        self.assertIn("director.text.delta", event_types)
        self.assertIn("director.text.final", event_types)
        self.assertNotIn("audio.output.chunk", event_types)
        self.assertNotIn("error", event_types)

    async def test_barge_in_discards_completed_stale_audio(self):
        websocket = _WebSocketRecorder()
        session = DirectorVoiceSession("session", "owner", websocket)
        session.status = "PROCESSING"
        session.current_turn_id = "turn-old"

        async def interrupted_synthesis(_text):
            session.status = "INTERRUPTED"
            session.current_turn_id = "turn-new"
            return DirectorAudioResult(b"RIFF-stale")

        with patch.object(
            director_voice_service,
            "synthesize_text",
            new=AsyncMock(side_effect=interrupted_synthesis),
        ):
            await director_realtime_voice_service._stream_synthesize_chunk(
                session,
                "turn-old",
                "Stale speech",
            )

        self.assertEqual(websocket.messages, [])

if __name__ == "__main__":
    unittest.main()
