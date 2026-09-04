"""Opt-in, numeric-only diagnostics for Director microphone audio."""
from __future__ import annotations

import math
import os
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Mapping, Sequence


DIAGNOSTIC_ENV_NAME = "DIRECTOR_VOICE_DIAGNOSTICS"
SILENCE_RMS_THRESHOLD = 0.012
CLIPPING_AMPLITUDE = 0.999
ANALYSIS_SAMPLE_RATE = 16_000


def director_voice_diagnostics_enabled(
    environ: Mapping[str, str] | None = None,
) -> bool:
    """Return true only for an explicit opt-in; diagnostics are off by default."""
    source = os.environ if environ is None else environ
    return source.get(DIAGNOSTIC_ENV_NAME, "false").strip().lower() == "true"


@dataclass(frozen=True)
class DirectorAudioMetrics:
    available: bool
    decoded_duration_ms: int
    sample_rate: int
    channels: int
    peak_amplitude: float
    rms_amplitude: float
    leading_silence_ms: int
    trailing_silence_ms: int
    clipping_ratio: float
    analysis_ms: int

    def to_safe_dict(self) -> dict:
        return asdict(self)


def calculate_audio_metrics(
    samples: Sequence[float],
    *,
    sample_rate: int,
    channels: int = 1,
    analysis_ms: int = 0,
) -> DirectorAudioMetrics:
    """Calculate bounded signal metrics from normalized mono float samples."""
    if sample_rate <= 0 or channels <= 0 or not samples:
        raise ValueError("AUDIO_DIAGNOSTIC_INVALID_PCM")

    sample_count = len(samples)
    peak = 0.0
    sum_squares = 0.0
    clipped = 0
    for raw_sample in samples:
        sample = max(-1.0, min(1.0, float(raw_sample)))
        magnitude = abs(sample)
        peak = max(peak, magnitude)
        sum_squares += sample * sample
        if magnitude >= CLIPPING_AMPLITUDE:
            clipped += 1

    rms = math.sqrt(sum_squares / sample_count)
    frame_samples = max(1, round(sample_rate * 0.02))
    frame_rms: list[float] = []
    for start in range(0, sample_count, frame_samples):
        frame = samples[start:start + frame_samples]
        if not frame:
            continue
        frame_rms.append(math.sqrt(sum(float(value) ** 2 for value in frame) / len(frame)))

    first_speech_frame = next(
        (index for index, value in enumerate(frame_rms) if value >= SILENCE_RMS_THRESHOLD),
        len(frame_rms),
    )
    last_speech_frame = next(
        (
            len(frame_rms) - index - 1
            for index, value in enumerate(reversed(frame_rms))
            if value >= SILENCE_RMS_THRESHOLD
        ),
        -1,
    )
    frame_ms = (frame_samples / sample_rate) * 1000
    leading_silence_ms = round(first_speech_frame * frame_ms)
    trailing_frames = (
        len(frame_rms)
        if last_speech_frame < 0
        else max(0, len(frame_rms) - last_speech_frame - 1)
    )

    return DirectorAudioMetrics(
        available=True,
        decoded_duration_ms=round((sample_count / sample_rate) * 1000),
        sample_rate=sample_rate,
        channels=channels,
        peak_amplitude=round(peak, 6),
        rms_amplitude=round(rms, 6),
        leading_silence_ms=leading_silence_ms,
        trailing_silence_ms=round(trailing_frames * frame_ms),
        clipping_ratio=round(clipped / sample_count, 8),
        analysis_ms=max(0, int(analysis_ms)),
    )


def analyze_audio_file(path: Path) -> DirectorAudioMetrics:
    """Decode one temporary upload with PyAV; no audio is retained or returned."""
    started_at = time.monotonic()
    try:
        import av

        resampler = av.AudioResampler(
            format="fltp",
            layout="mono",
            rate=ANALYSIS_SAMPLE_RATE,
        )
        decoded_samples: list[float] = []
        with av.open(str(path)) as container:
            audio_streams = [stream for stream in container.streams if stream.type == "audio"]
            if not audio_streams:
                raise ValueError("AUDIO_DIAGNOSTIC_NO_AUDIO_STREAM")
            for frame in container.decode(audio=0):
                converted_frames = resampler.resample(frame)
                if converted_frames is None:
                    continue
                if not isinstance(converted_frames, list):
                    converted_frames = [converted_frames]
                for converted in converted_frames:
                    values = converted.to_ndarray()
                    decoded_samples.extend(float(value) for value in values.reshape(-1))
            flushed_frames = resampler.resample(None) or []
            if not isinstance(flushed_frames, list):
                flushed_frames = [flushed_frames]
            for converted in flushed_frames:
                values = converted.to_ndarray()
                decoded_samples.extend(float(value) for value in values.reshape(-1))

        if not decoded_samples:
            raise ValueError("AUDIO_DIAGNOSTIC_EMPTY_DECODE")
        return calculate_audio_metrics(
            decoded_samples,
            sample_rate=ANALYSIS_SAMPLE_RATE,
            channels=1,
            analysis_ms=round((time.monotonic() - started_at) * 1000),
        )
    except ValueError:
        raise
    except Exception as exc:
        raise ValueError("AUDIO_DIAGNOSTIC_DECODE_FAILED") from exc
