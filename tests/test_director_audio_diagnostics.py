import math
import importlib.util
import struct
import tempfile
import unittest
import wave
from pathlib import Path

from app.services.director_audio_diagnostics import (
    analyze_audio_file,
    calculate_audio_metrics,
    director_voice_diagnostics_enabled,
)


class TestDirectorAudioDiagnostics(unittest.TestCase):
    def test_diagnostics_are_disabled_by_default(self):
        self.assertFalse(director_voice_diagnostics_enabled({}))
        self.assertFalse(director_voice_diagnostics_enabled({
            "DIRECTOR_VOICE_DIAGNOSTICS": "false",
        }))
        self.assertTrue(director_voice_diagnostics_enabled({
            "DIRECTOR_VOICE_DIAGNOSTICS": " true ",
        }))

    def test_numeric_metrics_capture_silence_amplitude_and_clipping(self):
        sample_rate = 1_000
        samples = (
            [0.0] * 100
            + [0.5 * math.sin(index / 4) for index in range(200)]
            + [1.0, -1.0]
            + [0.0] * 80
        )
        metrics = calculate_audio_metrics(
            samples,
            sample_rate=sample_rate,
            channels=1,
        )
        self.assertTrue(metrics.available)
        self.assertEqual(metrics.sample_rate, sample_rate)
        self.assertEqual(metrics.channels, 1)
        self.assertEqual(metrics.decoded_duration_ms, 382)
        self.assertGreaterEqual(metrics.leading_silence_ms, 80)
        self.assertGreaterEqual(metrics.trailing_silence_ms, 60)
        self.assertEqual(metrics.peak_amplitude, 1.0)
        self.assertGreater(metrics.rms_amplitude, 0.1)
        self.assertGreater(metrics.clipping_ratio, 0)

    def test_invalid_pcm_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "AUDIO_DIAGNOSTIC_INVALID_PCM"):
            calculate_audio_metrics([], sample_rate=16_000)

    @unittest.skipUnless(importlib.util.find_spec("av"), "PyAV not installed in local venv")
    def test_audio_file_is_decoded_without_being_modified_or_retained(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "diagnostic.wav"
            samples = [
                round(math.sin((2 * math.pi * 440 * index) / 16_000) * 12_000)
                for index in range(1_600)
            ]
            with wave.open(str(path), "wb") as output:
                output.setnchannels(1)
                output.setsampwidth(2)
                output.setframerate(16_000)
                output.writeframes(b"".join(struct.pack("<h", value) for value in samples))
            original_bytes = path.read_bytes()
            metrics = analyze_audio_file(path)
            self.assertTrue(metrics.available)
            self.assertEqual(metrics.sample_rate, 16_000)
            self.assertEqual(metrics.channels, 1)
            self.assertEqual(metrics.decoded_duration_ms, 100)
            self.assertGreater(metrics.peak_amplitude, 0.3)
            self.assertEqual(path.read_bytes(), original_bytes)


if __name__ == "__main__":
    unittest.main()
