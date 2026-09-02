import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from app.services.director_interaction_service import DirectorInteractionService
from app.services.director_transcript_quality import (
    MIN_AVERAGE_LOGPROB,
    assess_transcript_quality,
)


class TestDirectorTranscriptQuality(unittest.TestCase):
    def test_average_logprob_threshold_is_explicit_and_fail_closed(self):
        self.assertEqual(MIN_AVERAGE_LOGPROB, -1.0)
        accepted = assess_transcript_quality(
            "Hello Director",
            duration_seconds=1.0,
            avg_logprob=-1.0,
            no_speech_probability=0.01,
        )
        rejected = assess_transcript_quality(
            "Hello Director",
            duration_seconds=1.0,
            avg_logprob=-1.01,
            no_speech_probability=0.01,
        )
        self.assertNotIn("LOW_AVERAGE_LOGPROB", accepted.reasons)
        self.assertIn("LOW_AVERAGE_LOGPROB", rejected.reasons)

    def test_high_confidence_transcript_passes(self):
        result = assess_transcript_quality(
            "What is your role in NapsterTec?",
            duration_seconds=2.0,
            avg_logprob=-0.18,
            no_speech_probability=0.02,
            language_probability=0.99,
        )
        self.assertFalse(result.clarification_required)
        self.assertFalse(result.requires_confirmation)
        self.assertGreater(result.confidence, 0.7)

    def test_corrupt_or_repetitive_transcript_requires_clarification(self):
        result = assess_transcript_quality(
            "the the the the the the the",
            duration_seconds=2.0,
            avg_logprob=-1.3,
            no_speech_probability=0.1,
        )
        self.assertTrue(result.clarification_required)
        self.assertIn("LOW_AVERAGE_LOGPROB", result.reasons)
        self.assertIn("EXCESSIVE_REPETITION", result.reasons)

    def test_high_impact_transcript_requires_manual_confirmation(self):
        result = assess_transcript_quality(
            "Deploy the application now",
            duration_seconds=1.5,
            avg_logprob=-0.1,
            no_speech_probability=0.01,
        )
        self.assertFalse(result.clarification_required)
        self.assertTrue(result.requires_confirmation)


class TestDirectorConversationalFastPath(unittest.IsolatedAsyncioTestCase):
    async def collect(self, service, message):
        return "".join([token async for token in service.stream_interaction(message)])

    async def test_greeting_and_identity_use_deterministic_fast_path(self):
        service = DirectorInteractionService()
        with patch(
            "app.services.director_interaction_service.capability_router.route_skill_execution",
            new=AsyncMock(),
        ) as route:
            greeting = await self.collect(service, "Hello, Director.")
            identity = await self.collect(service, "What is your role in NapsterTec?")
        self.assertIn("online and ready", greeting)
        self.assertIn("executive AI coordinator", identity)
        route.assert_not_called()

    async def test_operational_command_never_uses_fast_path(self):
        service = DirectorInteractionService()
        response = await self.collect(service, "Deploy the application now")
        self.assertIn("cannot self-authorize", response)

    async def test_complex_question_remains_on_full_llm_path(self):
        service = DirectorInteractionService()

        async def stream(**_kwargs):
            yield "full path"

        with patch.object(service.state_service, "get_bootstrap_state", return_value=object()), patch(
            "app.services.director_interaction_service.capability_router.route_skill_execution",
            side_effect=stream,
        ) as route:
            response = await self.collect(service, "Explain our current market positioning")
        self.assertEqual(response, "full path")
        route.assert_called_once()


if __name__ == "__main__":
    unittest.main()
