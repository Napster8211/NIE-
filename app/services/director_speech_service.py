"""
NapsterTec AI - Director Speech-to-Text Service
Module: app/services/director_speech_service.py
"""
import logging
import os
import uuid
from typing import Optional

import httpx
from fastapi import UploadFile

from app.schemas.director_speech import DirectorTranscriptionResponse

logger = logging.getLogger(__name__)

MAX_AUDIO_BYTES = 25 * 1024 * 1024
STT_URL = "https://api.elevenlabs.io/v1/speech-to-text"


def _safe_content_type(file: UploadFile) -> str:
    content_type = (file.content_type or "").strip().lower()
    if content_type.startswith("audio/") or content_type.startswith("video/"):
        return file.content_type or "audio/webm"
    return "audio/webm"


def _duration_from_words(body: dict) -> int:
    words = body.get("words")
    if not isinstance(words, list) or not words:
        return 0

    end_values = []
    for word in words:
        if isinstance(word, dict):
            end = word.get("end")
            if isinstance(end, (int, float)):
                end_values.append(float(end))

    return int(max(end_values) * 1000) if end_values else 0


class DirectorSpeechService:
    async def transcribe(self, file: UploadFile) -> DirectorTranscriptionResponse:
        api_key = os.getenv("ELEVENLABS_API_KEY")
        model_id = os.getenv("ELEVENLABS_STT_MODEL_ID", "scribe_v2")
        lang = os.getenv("ELEVENLABS_STT_LANGUAGE", "en")

        if not api_key:
            logger.error("[DirectorSpeech] ELEVENLABS_API_KEY is missing from environment!")
            raise ValueError("STT_NOT_CONFIGURED")

        audio_bytes = await file.read()
        
        # CRITICAL FIX 1: If audio is completely empty/too short, gracefully return empty transcript
        if not audio_bytes or len(audio_bytes) < 100:
            return self._empty_response(lang)

        if len(audio_bytes) > MAX_AUDIO_BYTES:
            raise ValueError("AUDIO_TOO_LARGE")

        filename = file.filename or "audio.webm"
        content_type = _safe_content_type(file)

        files = {
            "file": (filename, audio_bytes, content_type),
        }
        data = {
            "model_id": model_id,
            "tag_audio_events": "false",
        }

        if lang:
            data["language_code"] = lang

        headers = {
            "xi-api-key": api_key,
            "Accept": "application/json",
        }

        try:
            async with httpx.AsyncClient(timeout=45.0) as client:
                response = await client.post(
                    STT_URL,
                    headers=headers,
                    files=files,
                    data=data,
                )

            if response.status_code in (401, 403):
                raise ValueError("STT_UNAUTHORIZED")
            if response.status_code == 402:
                raise ValueError("STT_PAYMENT_REQUIRED")
            if response.status_code == 413:
                raise ValueError("AUDIO_TOO_LARGE")
            if response.status_code == 429:
                raise ValueError("STT_RATE_LIMITED")
            if response.status_code >= 500:
                raise ValueError("STT_UNAVAILABLE")
                
            # CRITICAL FIX 2: Gracefully swallow 422 errors (silence/noise)
            if response.status_code == 422:
                return self._empty_response(lang)

            if response.status_code != 200:
                logger.warning(
                    "[DirectorSpeech] ElevenLabs STT failed with status %s",
                    response.status_code,
                )
                raise ValueError("TRANSCRIPTION_FAILED")

            try:
                body = response.json()
            except ValueError:
                raise ValueError("TRANSCRIPTION_FAILED")

            transcript = str(body.get("text") or "").strip()
            
            language = str(
                body.get("language_code")
                or lang
                or "unknown"
            )

            probability = body.get("language_probability")
            confidence: Optional[float] = (
                float(probability)
                if isinstance(probability, (int, float))
                else None
            )

            # CRITICAL FIX 3: Gracefully return empty transcript if no words were detected
            if not transcript:
                return self._empty_response(language)

            return DirectorTranscriptionResponse(
                request_id=f"req_{uuid.uuid4().hex[:12]}",
                transcript=transcript,
                confidence=confidence,
                language=language,
                duration_ms=_duration_from_words(body),
            )

        except httpx.TimeoutException:
            raise ValueError("STT_TIMEOUT")
        except httpx.RequestError:
            raise ValueError("STT_UNAVAILABLE")
        except ValueError:
            raise
        except Exception:
            logger.exception(
                "[DirectorSpeech] Unexpected STT failure (credentials omitted)"
            )
            raise ValueError("STT_UNAVAILABLE")

    def _empty_response(self, lang: Optional[str]) -> DirectorTranscriptionResponse:
        """Returns a clean 200 OK empty transcript to satisfy the browser."""
        return DirectorTranscriptionResponse(
            request_id=f"req_{uuid.uuid4().hex[:12]}",
            transcript="",
            confidence=0.0,
            language=lang or "en",
            duration_ms=0,
        )

director_speech_service = DirectorSpeechService()