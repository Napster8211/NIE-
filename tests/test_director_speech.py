import os
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

TEST_NIE_OWNER_KEY = "explicit-test-only-owner-key"
os.environ.setdefault("NIE_ENV", "test")
os.environ["NIE_OWNER_KEY"] = TEST_NIE_OWNER_KEY

from app.main import app
from app.services.director_speech_service import director_speech_service
from app.services.whisper_stt_provider import STTProviderError, WhisperTranscription


class TestDirectorSpeech(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self.headers = {"Authorization": f"Bearer {TEST_NIE_OWNER_KEY}"}

    @patch.object(director_speech_service.provider, "transcribe", new_callable=AsyncMock)
    def test_valid_audio_transcribes(self, mock_transcribe):
        captured_path = None

        async def transcribe(path):
            nonlocal captured_path
            captured_path = Path(path)
            self.assertTrue(captured_path.exists())
            self.assertEqual(captured_path.suffix, ".webm")
            return WhisperTranscription(
                text="Approve the outreach.",
                language="en",
                language_probability=0.99,
                duration_seconds=0.95,
                avg_logprob=-0.22,
                no_speech_probability=0.04,
                compression_ratio=1.12,
            )

        mock_transcribe.side_effect = transcribe

        res = self.client.post(
            "/api/v1/director/voice/transcribe",
            files={"file": ("test.webm", b"fake_audio_content" * 10, "audio/webm")},
            data={"correlation_id": "vsi_secure_test"},
            headers=self.headers,
        )

        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["transcript"], "Approve the outreach.")
        self.assertEqual(body["language"], "en")
        self.assertGreater(body["confidence"], 0.7)
        self.assertEqual(body["duration_ms"], 950)
        self.assertEqual(body["correlation_id"], "vsi_secure_test")
        self.assertEqual(body["audio_bytes"], len(b"fake_audio_content" * 10))
        self.assertEqual(body["media_type"], "audio/webm")
        self.assertEqual(body["word_count"], 3)
        self.assertAlmostEqual(body["avg_logprob"], -0.22)
        self.assertAlmostEqual(body["no_speech_probability"], 0.04)
        self.assertAlmostEqual(body["compression_ratio"], 1.12)
        self.assertTrue(body["requires_confirmation"])
        self.assertIn("transcription_total_ms", body["timings"])
        mock_transcribe.assert_awaited_once()
        self.assertIsNotNone(captured_path)
        self.assertFalse(captured_path.exists())

    @patch("app.services.director_speech_service.director_voice_diagnostics_enabled", return_value=True)
    @patch("app.services.director_speech_service.analyze_audio_file")
    @patch.object(director_speech_service.provider, "transcribe", new_callable=AsyncMock)
    def test_opt_in_diagnostics_return_numeric_metrics_without_retaining_audio(
        self,
        mock_transcribe,
        mock_analyze,
        _mock_enabled,
    ):
        captured_path = None

        async def transcribe(path):
            nonlocal captured_path
            captured_path = Path(path)
            return WhisperTranscription("Hello Director", "en", 0.99, 1.0, avg_logprob=-0.2)

        mock_transcribe.side_effect = transcribe
        mock_analyze.return_value.to_safe_dict.return_value = {
            "available": True,
            "decoded_duration_ms": 1_000,
            "sample_rate": 16_000,
            "channels": 1,
            "peak_amplitude": 0.5,
            "rms_amplitude": 0.1,
            "leading_silence_ms": 40,
            "trailing_silence_ms": 100,
            "clipping_ratio": 0.0,
            "analysis_ms": 2,
        }
        res = self.client.post(
            "/api/v1/director/voice/transcribe",
            files={"file": ("test.webm", b"fake_audio_content" * 10, "audio/webm")},
            data={"correlation_id": "vsi_diagnostics"},
            headers=self.headers,
        )
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertTrue(body["diagnostics_enabled"])
        self.assertEqual(body["audio_quality"]["sample_rate"], 16_000)
        self.assertNotIn("audio", body["audio_quality"])
        self.assertIsNotNone(captured_path)
        self.assertFalse(captured_path.exists())

    @patch.object(director_speech_service.provider, "transcribe", new_callable=AsyncMock)
    def test_empty_audio_returns_existing_safe_empty_transcript(self, mock_transcribe):
        res = self.client.post(
            "/api/v1/director/voice/transcribe",
            files={"file": ("test.webm", b"", "audio/webm")},
            headers=self.headers,
        )

        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["transcript"], "")
        self.assertEqual(res.json()["confidence"], 0.0)
        mock_transcribe.assert_not_awaited()

    def test_unsupported_audio_type_is_rejected(self):
        res = self.client.post(
            "/api/v1/director/voice/transcribe",
            files={"file": ("test.txt", b"not_audio" * 20, "text/plain")},
            headers=self.headers,
        )
        self.assertEqual(res.status_code, 422)
        self.assertEqual(res.json()["detail"], "STT_INVALID_AUDIO")

    @patch.object(director_speech_service.provider, "transcribe", new_callable=AsyncMock)
    def test_supported_browser_audio_types_preserve_container_suffix(self, mock_transcribe):
        seen_suffixes = []

        async def transcribe(path):
            seen_suffixes.append(Path(path).suffix)
            return WhisperTranscription("hello", "en", 0.9, 0.5)

        mock_transcribe.side_effect = transcribe
        cases = (
            ("voice.webm", "audio/webm;codecs=opus", ".webm"),
            ("voice.webm", "audio/webm", ".webm"),
            ("voice.mp4", "audio/mp4", ".mp4"),
            ("voice.ogg", "audio/ogg;codecs=opus", ".ogg"),
            ("voice.ogg", "audio/ogg", ".ogg"),
        )
        for filename, mime_type, suffix in cases:
            with self.subTest(mime_type=mime_type):
                res = self.client.post(
                    "/api/v1/director/voice/transcribe",
                    files={"file": (filename, b"fake_audio_content" * 10, mime_type)},
                    headers=self.headers,
                )
                self.assertEqual(res.status_code, 200)
                self.assertEqual(seen_suffixes[-1], suffix)
        self.assertEqual(mock_transcribe.await_count, len(cases))

    def test_provider_neutral_error_mapping(self):
        expected = {
            "STT_NOT_READY": 503,
            "STT_MODEL_LOAD_FAILED": 503,
            "STT_INVALID_AUDIO": 422,
            "STT_TIMEOUT": 504,
            "STT_TRANSCRIPTION_FAILED": 502,
        }
        for code, status in expected.items():
            with self.subTest(code=code), patch.object(
                director_speech_service.provider,
                "transcribe",
                new=AsyncMock(side_effect=STTProviderError(code)),
            ):
                res = self.client.post(
                    "/api/v1/director/voice/transcribe",
                    files={
                        "file": ("test.webm", b"fake_audio_content" * 10, "audio/webm")
                    },
                    headers=self.headers,
                )
                self.assertEqual(res.status_code, status)
                self.assertEqual(res.json()["detail"], code)

    def test_health_exposes_provider_neutral_stt_readiness(self):
        res = self.client.get("/health")
        self.assertEqual(res.status_code, 200)
        readiness = res.json()["director_stt"]
        self.assertEqual(readiness["provider"], "whisper")
        self.assertIn("loaded", readiness)
        self.assertIn("state", readiness)
        self.assertIn("load_duration_ms", readiness)
        self.assertIn("error", readiness)

    def test_director_stt_source_no_longer_calls_elevenlabs(self):
        service_source = (
            Path(__file__).parents[1] / "app" / "services" / "director_speech_service.py"
        ).read_text(encoding="utf-8")
        self.assertNotIn("api.elevenlabs.io/v1/speech-to-text", service_source)
        self.assertNotIn("ELEVENLABS_STT_MODEL_ID", service_source)
        self.assertNotIn("ELEVENLABS_STT_LANGUAGE", service_source)

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
