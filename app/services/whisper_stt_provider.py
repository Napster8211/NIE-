"""Bounded, process-local faster-whisper provider for Director speech input."""

from __future__ import annotations

import asyncio
import logging
import os
import threading
import time
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Callable, Optional, Protocol, Tuple


logger = logging.getLogger(__name__)


class STTProviderError(ValueError):
    """Safe provider-neutral failure exposed to the Director API layer."""


class STTReadinessState(str, Enum):
    NOT_STARTED = "not_started"
    LOADING = "loading"
    READY = "ready"
    FAILED = "failed"
    CLOSED = "closed"


@dataclass(frozen=True)
class WhisperSTTConfig:
    provider: str = "whisper"
    model_size: str = "tiny.en"
    device: str = "cpu"
    compute_type: str = "int8"
    language: Optional[str] = "en"
    model_cache_dir: Optional[str] = None
    local_files_only: bool = False
    initialization_timeout_seconds: float = 45.0
    transcription_timeout_seconds: float = 30.0
    max_audio_seconds: float = 20.0
    cpu_threads: int = 1
    beam_size: int = 1

    @classmethod
    def from_env(cls) -> "WhisperSTTConfig":
        language = os.getenv("WHISPER_LANGUAGE", "en").strip()
        if language.lower() in {"", "auto", "detect"}:
            language = None
        model_cache_dir = os.getenv("WHISPER_MODEL_CACHE_DIR", "").strip() or None
        local_files_only = os.getenv("WHISPER_LOCAL_FILES_ONLY", "false").strip().lower()
        if local_files_only not in {"true", "false"}:
            raise ValueError("WHISPER_LOCAL_FILES_ONLY must be true or false")
        return cls(
            provider=os.getenv("DIRECTOR_STT_PROVIDER", "whisper").strip().lower(),
            model_size=os.getenv("WHISPER_MODEL_SIZE", "tiny.en").strip() or "tiny.en",
            device=os.getenv("WHISPER_DEVICE", "cpu").strip() or "cpu",
            compute_type=os.getenv("WHISPER_COMPUTE_TYPE", "int8").strip() or "int8",
            language=language,
            model_cache_dir=model_cache_dir,
            local_files_only=local_files_only == "true",
            initialization_timeout_seconds=max(
                1.0, float(os.getenv("WHISPER_INIT_TIMEOUT_SECONDS", "45"))
            ),
            transcription_timeout_seconds=max(
                1.0, float(os.getenv("WHISPER_TRANSCRIPTION_TIMEOUT_SECONDS", "30"))
            ),
            max_audio_seconds=max(
                1.0, float(os.getenv("WHISPER_MAX_AUDIO_SECONDS", "20"))
            ),
            cpu_threads=max(1, int(os.getenv("WHISPER_CPU_THREADS", "1"))),
            beam_size=max(1, int(os.getenv("WHISPER_BEAM_SIZE", "1"))),
        )


@dataclass(frozen=True)
class WhisperTranscription:
    text: str
    language: str
    language_probability: Optional[float]
    duration_seconds: float


class WhisperModelProtocol(Protocol):
    def transcribe(self, audio: str, **kwargs): ...


ModelFactory = Callable[[WhisperSTTConfig], WhisperModelProtocol]
AudioProbe = Callable[[Path], float]


def _default_model_factory(config: WhisperSTTConfig) -> WhisperModelProtocol:
    # Lazy import keeps application import and health diagnostics fail-closed when
    # the optional native runtime cannot be initialized.
    from faster_whisper import WhisperModel

    options = {
        "device": config.device,
        "compute_type": config.compute_type,
        "cpu_threads": config.cpu_threads,
        "num_workers": 1,
        "local_files_only": config.local_files_only,
    }
    if config.model_cache_dir:
        options["download_root"] = config.model_cache_dir
    return WhisperModel(config.model_size, **options)


def _default_audio_probe(path: Path) -> float:
    """Validate/decode the container with PyAV and return its duration."""
    try:
        import av

        with av.open(str(path)) as container:
            if not any(stream.type == "audio" for stream in container.streams):
                raise STTProviderError("STT_INVALID_AUDIO")
            if container.duration is not None:
                duration = float(container.duration) / float(av.time_base)
            else:
                duration = 0.0
                frame_count = 0
                for frame in container.decode(audio=0):
                    frame_count += 1
                    if frame.sample_rate:
                        duration += float(frame.samples) / float(frame.sample_rate)
                if frame_count == 0:
                    raise STTProviderError("STT_INVALID_AUDIO")
    except STTProviderError:
        raise
    except Exception as exc:
        raise STTProviderError("STT_INVALID_AUDIO") from exc

    if duration <= 0:
        raise STTProviderError("STT_INVALID_AUDIO")
    return duration


class WhisperSTTProvider:
    """One model and one inference lane per application process."""

    def __init__(
        self,
        config: Optional[WhisperSTTConfig] = None,
        *,
        model_factory: ModelFactory = _default_model_factory,
        audio_probe: AudioProbe = _default_audio_probe,
    ) -> None:
        self.config = config or WhisperSTTConfig.from_env()
        self._model_factory = model_factory
        self._audio_probe = audio_probe
        self._executor = ThreadPoolExecutor(
            max_workers=1,
            thread_name_prefix="director-whisper",
        )
        self._lock = threading.RLock()
        self._model: Optional[WhisperModelProtocol] = None
        self._load_future: Optional[Future] = None
        self._transcription_future: Optional[Future] = None
        self._state = STTReadinessState.NOT_STARTED
        self._load_started_at: Optional[str] = None
        self._load_started_monotonic: Optional[float] = None
        self._load_completed_at: Optional[str] = None
        self._load_duration_ms: Optional[int] = None
        self._last_error: Optional[str] = None

    def readiness(self) -> dict:
        with self._lock:
            return {
                "provider": self.config.provider,
                "model": self.config.model_size,
                "device": self.config.device,
                "compute_type": self.config.compute_type,
                "loaded": self._state == STTReadinessState.READY,
                "state": self._state.value,
                "cache_configured": bool(self.config.model_cache_dir),
                "local_files_only": self.config.local_files_only,
                "load_started_at": self._load_started_at,
                "load_completed_at": self._load_completed_at,
                "load_duration_ms": self._load_duration_ms,
                "error": self._last_error,
            }

    def start_loading(self) -> None:
        """Start one background model load without blocking application startup."""
        with self._lock:
            if self.config.provider != "whisper":
                self._state = STTReadinessState.FAILED
                self._last_error = "STT_NOT_READY"
                return
            if self._state == STTReadinessState.CLOSED:
                return
            if self._model is not None or self._load_future is not None:
                return
            self._state = STTReadinessState.LOADING
            self._last_error = None
            self._load_started_at = datetime.now(timezone.utc).isoformat()
            self._load_started_monotonic = time.monotonic()
            self._load_future = self._executor.submit(self._load_model)
            self._load_future.add_done_callback(self._finish_loading)

    def _load_model(self) -> WhisperModelProtocol:
        return self._model_factory(self.config)

    def _finish_loading(self, future: Future) -> None:
        try:
            model = future.result()
        except Exception as exc:
            logger.error(
                "[DirectorSTT] Whisper model initialization failed (%s)",
                type(exc).__name__,
            )
            with self._lock:
                self._state = STTReadinessState.FAILED
                self._last_error = "STT_MODEL_LOAD_FAILED"
                self._record_load_completion()
            return
        with self._lock:
            self._model = model
            self._state = STTReadinessState.READY
            self._last_error = None
            self._record_load_completion()

    def _record_load_completion(self) -> None:
        self._load_completed_at = datetime.now(timezone.utc).isoformat()
        if self._load_started_monotonic is not None:
            self._load_duration_ms = max(
                0,
                int((time.monotonic() - self._load_started_monotonic) * 1000),
            )

    async def _ensure_loaded(self) -> WhisperModelProtocol:
        self.start_loading()
        with self._lock:
            if self._model is not None:
                return self._model
            future = self._load_future
            state = self._state

        if self.config.provider != "whisper" or future is None:
            raise STTProviderError("STT_NOT_READY")
        if state == STTReadinessState.FAILED:
            raise STTProviderError("STT_MODEL_LOAD_FAILED")

        try:
            model = await asyncio.wait_for(
                asyncio.shield(asyncio.wrap_future(future)),
                timeout=self.config.initialization_timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            raise STTProviderError("STT_NOT_READY") from exc
        except Exception as exc:
            raise STTProviderError("STT_MODEL_LOAD_FAILED") from exc

        with self._lock:
            self._model = model
            self._state = STTReadinessState.READY
        return model

    async def transcribe(self, audio_path: Path) -> WhisperTranscription:
        model = await self._ensure_loaded()
        with self._lock:
            active = self._transcription_future
            if active is not None and not active.done():
                raise STTProviderError("STT_NOT_READY")
            future = self._executor.submit(self._transcribe_sync, model, audio_path)
            self._transcription_future = future
            future.add_done_callback(self._finish_transcription)

        try:
            return await asyncio.wait_for(
                asyncio.shield(asyncio.wrap_future(future)),
                timeout=self.config.transcription_timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            # Native inference cannot be safely interrupted. The single lane stays
            # occupied and rejects new work until this bounded task actually exits.
            raise STTProviderError("STT_TIMEOUT") from exc
        except STTProviderError:
            raise
        except Exception as exc:
            logger.exception("[DirectorSTT] Whisper transcription failed")
            raise STTProviderError("STT_TRANSCRIPTION_FAILED") from exc

    def _finish_transcription(self, future: Future) -> None:
        with self._lock:
            if self._transcription_future is future:
                self._transcription_future = None

    def defer_cleanup_until_idle(self, path: Path) -> bool:
        """Delete a timed-out request file only after native inference releases it."""
        with self._lock:
            future = self._transcription_future
        if future is None:
            return False

        def cleanup(_future: Future) -> None:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                logger.warning(
                    "[DirectorSTT] Deferred temporary audio cleanup failed",
                    exc_info=True,
                )

        future.add_done_callback(cleanup)
        return True

    def _transcribe_sync(
        self,
        model: WhisperModelProtocol,
        audio_path: Path,
    ) -> WhisperTranscription:
        duration = self._audio_probe(audio_path)
        if duration > self.config.max_audio_seconds:
            raise STTProviderError("STT_INVALID_AUDIO")
        segments, info = model.transcribe(
            str(audio_path),
            language=self.config.language,
            beam_size=self.config.beam_size,
            vad_filter=True,
            condition_on_previous_text=False,
        )
        text = " ".join(
            str(getattr(segment, "text", "")).strip()
            for segment in segments
            if str(getattr(segment, "text", "")).strip()
        ).strip()
        language = str(
            getattr(info, "language", None)
            or self.config.language
            or "unknown"
        )
        probability = getattr(info, "language_probability", None)
        confidence = (
            float(probability) if isinstance(probability, (int, float)) else None
        )
        duration = getattr(info, "duration_after_vad", None)
        if not isinstance(duration, (int, float)):
            duration = getattr(info, "duration", 0.0)
        return WhisperTranscription(
            text=text,
            language=language,
            language_probability=confidence,
            duration_seconds=max(0.0, float(duration or 0.0)),
        )

    async def shutdown(self) -> None:
        with self._lock:
            self._state = STTReadinessState.CLOSED
        self._executor.shutdown(wait=False, cancel_futures=True)


try:
    whisper_stt_provider = WhisperSTTProvider()
except (TypeError, ValueError):
    # Invalid non-secret runtime settings must not crash import or silently use
    # different settings. Readiness reports failure and requests fail closed.
    logger.error("[DirectorSTT] Whisper configuration is invalid")
    whisper_stt_provider = WhisperSTTProvider(
        WhisperSTTConfig(provider="invalid")
    )
