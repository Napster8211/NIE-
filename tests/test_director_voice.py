import unittest
from unittest.mock import patch, AsyncMock
from fastapi.testclient import TestClient

from app.main import app
from app.services.authorization import NIE_OWNER_KEY
from app.services.director_voice_service import director_voice_service

class TestDirectorVoice(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.headers = {"Authorization": f"Bearer {NIE_OWNER_KEY}"}

    @patch("app.services.director_voice_service.ELEVENLABS_VOICE_ID", "fake_voice_id")
    @patch("app.services.director_voice_service.ELEVENLABS_API_KEY", "fake_key")
    @patch("httpx.AsyncClient.post", new_callable=AsyncMock)
    def test_valid_briefing_produces_audio(self, mock_post):
        # Mock ElevenLabs success response
        mock_post.return_value.status_code = 200
        mock_post.return_value.content = b"fake_audio_bytes"

        res = self.client.post("/api/v1/director/voice/speak", json={
            "briefing_type": "COMPANY_STATUS"
        }, headers=self.headers)
        
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.headers["content-type"], "audio/mpeg")
        self.assertEqual(res.content, b"fake_audio_bytes")
        
        # Verify the actual speech_text was sent, NOT arbitrary frontend text
        called_json = mock_post.call_args[1]["json"]
        self.assertTrue("The Intelligence Engine is" in called_json["text"])
        
        # Verify secrets are NOT exposed
        self.assertFalse("fake_key" in res.content.decode(errors="ignore"))

    @patch("app.services.director_voice_service.ELEVENLABS_API_KEY", None)
    def test_missing_api_key_returns_safe_error(self):
        res = self.client.post("/api/v1/director/voice/speak", json={
            "briefing_type": "COMPANY_STATUS"
        }, headers=self.headers)
        
        self.assertEqual(res.status_code, 503)
        self.assertIn("VOICE_NOT_CONFIGURED", res.json()["detail"])

    @patch("app.services.director_voice_service.ELEVENLABS_VOICE_ID", "fake_voice_id")
    @patch("app.services.director_voice_service.ELEVENLABS_API_KEY", "fake_key")
    def test_invalid_briefing_rejected(self):
        res = self.client.post("/api/v1/director/voice/speak", json={
            "briefing_type": "DEPARTMENT",
            "target_id": "invalid_dept"
        }, headers=self.headers)
        
        self.assertEqual(res.status_code, 400)
        self.assertTrue(res.json()["detail"])

if __name__ == "__main__":
    unittest.main()