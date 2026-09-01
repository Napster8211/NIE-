import asyncio
import os
import sys
import threading
import time
import unittest
from pathlib import Path
from tempfile import NamedTemporaryFile
from types import SimpleNamespace
from unittest.mock import patch

from app.services.whisper_stt_provider import (
    STTProviderError,
    WhisperSTTConfig,
    WhisperSTTProvider,
    _default_model_factory,
)


class FakeWhisperModel:
    def __init__(self):
        self.calls = 0

    def transcribe(self, audio, **kwargs):
        self.calls += 1
        return (
            [SimpleNamespace(text=" Director"), SimpleNamespace(text=" ready. ")],
            SimpleNamespace(
                language="en",
                language_probability=0.98,
                duration_after_vad=1.25,
            ),
        )


class TestWhisperSTTProvider(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        with NamedTemporaryFile(suffix=".webm", delete=False) as audio:
            audio.write(b"test audio container")
            self.audio_path = Path(audio.name)
        self.providers = []

    async def asyncTearDown(self):
        for provider in self.providers:
            await provider.shutdown()
        self.audio_path.unlink(missing_ok=True)

    def make_provider(self, **kwargs):
        config = WhisperSTTConfig(
            model_size="base",
            device="cpu",
            compute_type="int8",
            language="en",
            initialization_timeout_seconds=kwargs.pop("init_timeout", 0.25),
            transcription_timeout_seconds=kwargs.pop("timeout", 0.25),
            max_audio_seconds=30,
            cpu_threads=1,
            beam_size=1,
        )
        provider = WhisperSTTProvider(
            config,
            model_factory=kwargs.pop("model_factory", lambda _config: FakeWhisperModel()),
            audio_probe=kwargs.pop("audio_probe", lambda _path: 1.5),
        )
        self.providers.append(provider)
        return provider

    async def test_model_loads_once_and_valid_audio_transcribes(self):
        loads = []
        model = FakeWhisperModel()
        provider = self.make_provider(
            model_factory=lambda config: loads.append(config.model_size) or model
        )

        first = await provider.transcribe(self.audio_path)
        second = await provider.transcribe(self.audio_path)

        self.assertEqual(loads, ["base"])
        self.assertEqual(model.calls, 2)
        self.assertEqual(first.text, "Director ready.")
        self.assertEqual(first.language, "en")
        self.assertAlmostEqual(first.language_probability, 0.98)
        self.assertAlmostEqual(first.duration_seconds, 1.25)
        self.assertEqual(second.text, first.text)
        readiness = provider.readiness()
        self.assertTrue(readiness["loaded"])
        self.assertIsNotNone(readiness["load_started_at"])
        self.assertIsNotNone(readiness["load_completed_at"])
        self.assertIsInstance(readiness["load_duration_ms"], int)
        self.assertIsNone(readiness["error"])

    async def test_model_initialization_failure_is_safe(self):
        def fail(_config):
            raise RuntimeError("native model failure")

        provider = self.make_provider(model_factory=fail)
        with self.assertRaisesRegex(STTProviderError, "STT_MODEL_LOAD_FAILED"):
            await provider.transcribe(self.audio_path)
        readiness = provider.readiness()
        self.assertEqual(readiness["state"], "failed")
        self.assertEqual(readiness["error"], "STT_MODEL_LOAD_FAILED")

    async def test_corrupt_audio_is_rejected_before_model_load(self):
        def reject(_path):
            raise STTProviderError("STT_INVALID_AUDIO")

        provider = self.make_provider(audio_probe=reject)
        with self.assertRaisesRegex(STTProviderError, "STT_INVALID_AUDIO"):
            await provider.transcribe(self.audio_path)
        self.assertEqual(provider.readiness()["state"], "ready")

    async def test_audio_duration_bound_is_enforced(self):
        provider = self.make_provider(audio_probe=lambda _path: 31.0)
        with self.assertRaisesRegex(STTProviderError, "STT_INVALID_AUDIO"):
            await provider.transcribe(self.audio_path)

    async def test_transcription_timeout_is_bounded(self):
        class SlowModel(FakeWhisperModel):
            def transcribe(self, audio, **kwargs):
                time.sleep(0.08)
                return super().transcribe(audio, **kwargs)

        provider = self.make_provider(
            model_factory=lambda _config: SlowModel(), timeout=0.01
        )
        with self.assertRaisesRegex(STTProviderError, "STT_TIMEOUT"):
            await provider.transcribe(self.audio_path)
        await asyncio.sleep(0.1)

    async def test_concurrent_transcription_is_rejected(self):
        started = threading.Event()
        release = threading.Event()

        class BlockingModel(FakeWhisperModel):
            def transcribe(self, audio, **kwargs):
                started.set()
                release.wait(timeout=1)
                return super().transcribe(audio, **kwargs)

        provider = self.make_provider(
            model_factory=lambda _config: BlockingModel(), timeout=1.0
        )
        first = asyncio.create_task(provider.transcribe(self.audio_path))
        try:
            self.assertTrue(await asyncio.to_thread(started.wait, 3.0))
            with self.assertRaisesRegex(STTProviderError, "STT_NOT_READY"):
                await provider.transcribe(self.audio_path)
        finally:
            release.set()
        result = await first
        self.assertEqual(result.text, "Director ready.")

    async def test_non_whisper_configuration_fails_closed(self):
        provider = self.make_provider()
        object.__setattr__(provider.config, "provider", "unsupported")
        with self.assertRaisesRegex(STTProviderError, "STT_NOT_READY"):
            await provider.transcribe(self.audio_path)
        self.assertFalse(provider.readiness()["loaded"])

    def test_free_tier_defaults_are_tiny_cpu_int8_single_thread(self):
        whisper_names = {
            "DIRECTOR_STT_PROVIDER",
            "WHISPER_MODEL_SIZE",
            "WHISPER_DEVICE",
            "WHISPER_COMPUTE_TYPE",
            "WHISPER_LANGUAGE",
            "WHISPER_MODEL_CACHE_DIR",
            "WHISPER_LOCAL_FILES_ONLY",
            "WHISPER_INIT_TIMEOUT_SECONDS",
            "WHISPER_TRANSCRIPTION_TIMEOUT_SECONDS",
            "WHISPER_MAX_AUDIO_SECONDS",
            "WHISPER_CPU_THREADS",
            "WHISPER_BEAM_SIZE",
        }
        clean_environment = {
            key: value for key, value in os.environ.items() if key not in whisper_names
        }
        with patch.dict(os.environ, clean_environment, clear=True):
            config = WhisperSTTConfig.from_env()

        self.assertEqual(config.model_size, "tiny.en")
        self.assertEqual(config.device, "cpu")
        self.assertEqual(config.compute_type, "int8")
        self.assertEqual(config.cpu_threads, 1)
        self.assertEqual(config.beam_size, 1)
        self.assertEqual(config.max_audio_seconds, 20.0)
        self.assertFalse(config.local_files_only)

    def test_model_factory_enforces_single_worker_and_local_cache_only(self):
        captured = {}

        class CapturingWhisperModel:
            def __init__(self, model_size, **options):
                captured["model_size"] = model_size
                captured["options"] = options

        config = WhisperSTTConfig(
            model_cache_dir=".cache/whisper",
            local_files_only=True,
        )
        fake_module = SimpleNamespace(WhisperModel=CapturingWhisperModel)
        with patch.dict(sys.modules, {"faster_whisper": fake_module}):
            _default_model_factory(config)

        self.assertEqual(captured["model_size"], "tiny.en")
        self.assertEqual(captured["options"]["cpu_threads"], 1)
        self.assertEqual(captured["options"]["num_workers"], 1)
        self.assertTrue(captured["options"]["local_files_only"])
        self.assertEqual(captured["options"]["download_root"], ".cache/whisper")

    def test_invalid_local_files_only_setting_fails_closed(self):
        with patch.dict(os.environ, {"WHISPER_LOCAL_FILES_ONLY": "sometimes"}):
            with self.assertRaisesRegex(ValueError, "must be true or false"):
                WhisperSTTConfig.from_env()


if __name__ == "__main__":
    unittest.main()
