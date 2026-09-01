"""Director speech-to-text service backed by self-hosted faster-whisper."""
from __future__ import annotations

import logging
import os
import tempfile
import uuid
from pathlib import Path
from typing import Optional

from fastapi import UploadFile

from app.schemas.director_speech import DirectorTranscriptionResponse
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

    async def transcribe(self, file: UploadFile) -> DirectorTranscriptionResponse:
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
        audio_bytes = await file.read(max_audio_bytes + 1)
        if not audio_bytes or len(audio_bytes) < 100:
            return self._empty_response(self.provider.config.language)
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
            if not result.text:
                return self._empty_response(result.language)
            return DirectorTranscriptionResponse(
                request_id=f"req_{uuid.uuid4().hex[:12]}",
                transcript=result.text,
                confidence=result.language_probability,
                language=result.language,
                duration_ms=int(result.duration_seconds * 1000),
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

    def _empty_response(self, language: Optional[str]) -> DirectorTranscriptionResponse:
        return DirectorTranscriptionResponse(
            request_id=f"req_{uuid.uuid4().hex[:12]}",
            transcript="",
            confidence=0.0,
            language=language or "en",
            duration_ms=0,
        )

director_speech_service = DirectorSpeechService()
