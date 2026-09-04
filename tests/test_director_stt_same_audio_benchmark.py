import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from scripts import benchmark_director_stt_same_audio as benchmark


class TestSameAudioBenchmark(unittest.IsolatedAsyncioTestCase):
    async def test_every_comparison_uses_the_identical_audio_path(self):
        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / "owner-recording.webm"
            audio.write_bytes(b"same-audio")
            args = SimpleNamespace(
                audio=audio,
                expected="Hello, Director.",
                models=["tiny.en", "base.en"],
                beam_sizes=[1, 5],
                compare_domain_prompt=True,
            )
            measured = AsyncMock(return_value={"status": "MEASURED"})
            with patch.object(benchmark, "_measure", new=measured):
                results = await benchmark.run(args)

            self.assertEqual(len(results), 8)
            self.assertEqual(measured.await_count, 8)
            self.assertTrue(all(call.args[0] == audio for call in measured.await_args_list))

    async def test_missing_audio_is_never_fabricated(self):
        args = SimpleNamespace(
            audio=Path("missing-owner-recording.webm"),
            expected="Hello, Director.",
            models=["tiny.en"],
            beam_sizes=[1],
            compare_domain_prompt=False,
        )
        with self.assertRaisesRegex(FileNotFoundError, "AUDIO_FILE_NOT_FOUND"):
            await benchmark.run(args)


if __name__ == "__main__":
    unittest.main()
