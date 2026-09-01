"""Director speech-to-text service backed by self-hosted faster-whisper."""
from __future__ import annotations

import logging
import os
import tempfile
import time
import uuid
from pathlib import Path
from typing import Optional

from fastapi import UploadFile

from app.schemas.director_speech import DirectorTranscriptionResponse
from app.services.director_transcript_quality import assess_transcript_quality
from app.services.whisper_stt_provider import (
    STTProviderError,
    WhisperSTTProvider,
    whisper_stt_provider,
)

logger = logging.getLogger(__name__)

DEFAULT_MAX_AUDIO_BYTES = 5 * 1024 * 1024
ALLOWED_AUDIO_TYPES = {
    "audio/webm",
    "audio/webm;codecs=opus",
    "audio/mp4",
    "audio/ogg",
    "audio/ogg;codecs=opus",
    "audio/wav",
    "audio/x-wav",
    "audio/mpeg",
}
MIME_SUFFIXES = {
    "audio/webm": ".webm",
    "audio/webm;codecs=opus": ".webm",
    "audio/mp4": ".mp4",
    "audio/ogg": ".ogg",
    "audio/ogg;codecs=opus": ".ogg",
    "audio/wav": ".wav",
    "audio/x-wav": ".wav",
    "audio/mpeg": ".mp3",
}


def _normalized_content_type(file: UploadFile) -> str:
    return (file.content_type or "").strip().lower()


def _safe_suffix(file: UploadFile, content_type: str) -> str:
    suffix = Path(file.filename or "").suffix.lower()
    allowed_suffixes = {".webm", ".mp4", ".ogg", ".wav", ".mp3"}
    return suffix if suffix in allowed_suffixes else MIME_SUFFIXES[content_type]


class DirectorSpeechService:
    def __init__(self, provider: WhisperSTTProvider = whisper_stt_provider) -> None:
        self.provider = provider

    def startup(self) -> None:
        self.provider.start_loading()

    async def shutdown(self) -> None:
        await self.provider.shutdown()

    def readiness(self) -> dict:
        return self.provider.readiness()

    async def transcribe(
        self,
        file: UploadFile,
        correlation_id: Optional[str] = None,
    ) -> DirectorTranscriptionResponse:
        total_started_at = time.monotonic()
        safe_correlation_id = self._safe_correlation_id(correlation_id)
        content_type = _normalized_content_type(file)
        if content_type not in ALLOWED_AUDIO_TYPES:
            raise STTProviderError("STT_INVALID_AUDIO")

        try:
            max_audio_bytes = max(
                1,
                int(
                    os.getenv(
                        "DIRECTOR_STT_MAX_AUDIO_BYTES",
                        str(DEFAULT_MAX_AUDIO_BYTES),
                    )
                ),
            )
        except (TypeError, ValueError) as exc:
            raise STTProviderError("STT_NOT_READY") from exc
        validation_started_at = time.monotonic()
        audio_bytes = await file.read(max_audio_bytes + 1)
        audio_validation_ms = max(
            0,
            int((time.monotonic() - validation_started_at) * 1000),
        )
        if not audio_bytes or len(audio_bytes) < 100:
            return self._empty_response(
                self.provider.config.language,
                safe_correlation_id,
                audio_validation_ms,
            )
        if len(audio_bytes) > max_audio_bytes:
            raise STTProviderError("AUDIO_TOO_LARGE")

        temp_path: Optional[Path] = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="wb",
                prefix="director-stt-",
                suffix=_safe_suffix(file, content_type),
                delete=False,
            ) as temp_file:
                temp_file.write(audio_bytes)
                temp_file.flush()
                temp_path = Path(temp_file.name)

            result = await self.provider.transcribe(temp_path)
            quality = assess_transcript_quality(
                result.text,
                duration_seconds=result.duration_seconds,
                avg_logprob=result.avg_logprob,
                no_speech_probability=result.no_speech_probability,
                language_probability=result.language_probability,
            )
            total_ms = max(0, int((time.monotonic() - total_started_at) * 1000))
            timings = {
                "audio_validation_ms": audio_validation_ms,
                "whisper_queue_wait_ms": result.queue_wait_ms,
                "audio_decode_ms": result.audio_decode_ms,
                "whisper_inference_ms": result.inference_ms,
                "transcription_total_ms": total_ms,
            }
            logger.info(
                "[STT][%s] model=%s bytes=%d duration_ms=%d queue_wait_ms=%d "
                "decode_ms=%d inference_ms=%d total_ms=%d clarification=%s",
                safe_correlation_id,
                self.provider.config.model_size,
                len(audio_bytes),
                int(result.duration_seconds * 1000),
                result.queue_wait_ms,
                result.audio_decode_ms,
                result.inference_ms,
                total_ms,
                quality.clarification_required,
            )
            return DirectorTranscriptionResponse(
                request_id=f"req_{uuid.uuid4().hex[:12]}",
                correlation_id=safe_correlation_id,
                transcript=result.text,
                confidence=quality.confidence,
                language=result.language,
                language_probability=result.language_probability,
                duration_ms=int(result.duration_seconds * 1000),
                clarification_required=quality.clarification_required,
                requires_confirmation=quality.requires_confirmation,
                quality_reasons=list(quality.reasons),
                avg_logprob=result.avg_logprob,
                no_speech_probability=result.no_speech_probability,
                timings=timings,
            )
        except STTProviderError as exc:
            if (
                str(exc) == "STT_TIMEOUT"
                and temp_path is not None
                and self.provider.defer_cleanup_until_idle(temp_path)
            ):
                temp_path = None
            raise
        except Exception as exc:
            logger.exception("[DirectorSpeech] Unexpected local STT failure")
            raise STTProviderError("STT_TRANSCRIPTION_FAILED") from exc
        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    logger.warning(
                        "[DirectorSpeech] Temporary audio cleanup failed",
                        exc_info=True,
                    )

    @staticmethod
    def _safe_correlation_id(value: Optional[str]) -> str:
        candidate = (value or "").strip()
        if candidate and len(candidate) <= 64 and all(
            character.isalnum() or character in {"_", "-"}
            for character in candidate
        ):
            return candidate
        return f"vsi_{uuid.uuid4().hex[:12]}"

    def _empty_response(
        self,
        language: Optional[str],
        correlation_id: str,
        audio_validation_ms: int = 0,
    ) -> DirectorTranscriptionResponse:
        return DirectorTranscriptionResponse(
            request_id=f"req_{uuid.uuid4().hex[:12]}",
            correlation_id=correlation_id,
            transcript="",
            confidence=0.0,
            language=language or "en",
            duration_ms=0,
            clarification_required=True,
            quality_reasons=["EMPTY_TRANSCRIPT"],
            timings={
                "audio_validation_ms": audio_validation_ms,
                "whisper_queue_wait_ms": 0,
                "audio_decode_ms": 0,
                "whisper_inference_ms": 0,
                "transcription_total_ms": audio_validation_ms,
            },
        )

director_speech_service = DirectorSpeechService()
