import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock, Mock

from fastapi.testclient import TestClient

from app.main import app
from app.services.authorization import NIE_OWNER_KEY


class TestDirectorSpeech(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.headers = {"Authorization": f"Bearer {NIE_OWNER_KEY}"}

    @patch.dict(os.environ, {"ELEVENLABS_API_KEY": "fake_key"})
    @patch("httpx.AsyncClient.post", new_callable=AsyncMock)
    def test_valid_audio_transcribes(self, mock_post):
        # httpx.AsyncClient.post is async, but httpx.Response.json() is synchronous.
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "text": "Approve the outreach.",
            "language_code": "en",
            "language_probability": 0.99,
            "words": [
                {"text": "Approve", "start": 0.0, "end": 0.35},
                {"text": "the", "start": 0.36, "end": 0.48},
                {"text": "outreach.", "start": 0.49, "end": 0.95},
            ],
        }
        mock_post.return_value = mock_response

        res = self.client.post(
            "/api/v1/director/voice/transcribe",
            files={"file": ("test.webm", b"fake_audio_content" * 10, "audio/webm")},
            headers=self.headers,
        )

        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["transcript"], "Approve the outreach.")
        self.assertEqual(body["language"], "en")
        self.assertAlmostEqual(body["confidence"], 0.99)
        self.assertEqual(body["duration_ms"], 950)
        self.assertNotIn("fake_key", res.text)

        _, kwargs = mock_post.call_args
        self.assertEqual(kwargs["data"]["model_id"], "scribe_v2")
        self.assertEqual(kwargs["data"]["language_code"], "en")
        self.assertIn("file", kwargs["files"])

    @patch.dict(os.environ, {"ELEVENLABS_API_KEY": "fake_key"})
    def test_empty_audio_returns_existing_safe_empty_transcript(self):
        res = self.client.post(
            "/api/v1/director/voice/transcribe",
            files={"file": ("test.webm", b"", "audio/webm")},
            headers=self.headers,
        )

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["transcript"], "")
        self.assertEqual(res.json()["confidence"], 0.0)

    @patch(
        "app.services.director_interaction_service."
        "director_interaction_service.state_service.list_pending_approvals"
    )
    def test_voice_command_resolves_to_proposal_not_authority(self, mock_pending):
        # Isolate this security test from mutable approval repository state.
        # The service only needs approval_id and resource_scope for this branch.
        mock_pending.return_value = [
            SimpleNamespace(
                approval_id="apr_test_outreach",
                resource_scope="Initiate automated outreach",
            )
        ]

        res = self.client.post(
            "/api/v1/director/interact",
            json={"message": "Approve the outreach."},
            headers=self.headers,
        )

        self.assertEqual(res.status_code, 200)
        body = res.json()

        self.assertIsNotNone(body["proposed_action"])
        self.assertEqual(
            body["proposed_action"]["action_type"],
            "approval_resolution",
        )
        self.assertEqual(
            body["proposed_action"]["resource_id"],
            "apr_test_outreach",
        )
        self.assertIn("I cannot self-authorize", body["message"])

        # Most important security invariant: conversation returns a proposal,
        # not a completed/approved mutation.
        self.assertTrue(body["proposed_action"]["requires_owner_action"])


if __name__ == "__main__":
    unittest.main()
