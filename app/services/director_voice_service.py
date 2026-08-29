"""
NapsterTec AI - Director Voice Synthesis Service
Module: app/services/director_voice_service.py
"""
from dataclasses import dataclass
import logging
import os
from typing import Optional

import httpx

from app.schemas.director_voice import DirectorVoiceRequest
from app.services.executive_briefing_service import executive_briefing_service

logger = logging.getLogger(__name__)

DEFAULT_VOICE_GATEWAY_TIMEOUT_SECONDS = 45.0
DEFAULT_DIRECTOR_PIPER_SAMPLE_RATE = 16000
DEFAULT_MAX_DIRECTOR_SPEECH_CHARS = 5000


@dataclass(frozen=True)
class DirectorAudioResult:
    """Audio and playback metadata returned by the internal voice gateway."""

    audio_bytes: bytes
    audio_format: str = "wav"
    sample_rate: int = DEFAULT_DIRECTOR_PIPER_SAMPLE_RATE
    channels: int = 1


class DirectorVoiceService:
    """Generate Director audio through NapsterTec's internal Piper gateway."""

    def __init__(self) -> None:
        self._http_client: Optional[httpx.AsyncClient] = None

    def _get_http_client(self) -> httpx.AsyncClient:
        """Maintain one connection pool while reading request config at runtime."""
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(
                limits=httpx.Limits(
                    max_keepalive_connections=10,
                    max_connections=20,
                ),
            )
        return self._http_client

    async def aclose(self) -> None:
        if self._http_client is not None and not self._http_client.is_closed:
            await self._http_client.aclose()

    @staticmethod
    def _positive_float_from_env(name: str, default: float) -> float:
        try:
            value = float(os.getenv(name, str(default)))
        except (TypeError, ValueError):
            raise ValueError("VOICE_NOT_CONFIGURED")
        if value <= 0:
            raise ValueError("VOICE_NOT_CONFIGURED")
        return value

    @staticmethod
    def _positive_int_from_env(name: str, default: int) -> int:
        try:
            value = int(os.getenv(name, str(default)))
        except (TypeError, ValueError):
            raise ValueError("VOICE_NOT_CONFIGURED")
        if value <= 0:
            raise ValueError("VOICE_NOT_CONFIGURED")
        return value

    @staticmethod
    def _raise_for_gateway_error(response: httpx.Response) -> None:
        status = response.status_code
        if status in (401, 403):
            logger.warning("[DirectorVoice] Voice gateway authorization failed status=%s", status)
            raise ValueError("VOICE_GATEWAY_UNAUTHORIZED")
        if status == 429:
            logger.warning("[DirectorVoice] Voice gateway rate limited the request")
            raise ValueError("VOICE_GATEWAY_RATE_LIMITED")
        if 500 <= status <= 599:
            logger.warning("[DirectorVoice] Voice gateway unavailable status=%s", status)
            raise ValueError("VOICE_GATEWAY_UNAVAILABLE")
        raise ValueError("VOICE_GENERATION_FAILED")

    async def synthesize_text(self, text: str) -> DirectorAudioResult:
        """Synthesize normalized text through the private Piper WAV endpoint."""
        gateway_url = os.getenv("VOICE_GATEWAY_URL", "").strip()
        gateway_key = os.getenv("VOICE_GATEWAY_API_KEY", "").strip()
        if not gateway_url or not gateway_key:
            raise ValueError("VOICE_NOT_CONFIGURED")

        timeout_seconds = self._positive_float_from_env(
            "VOICE_GATEWAY_TIMEOUT_SECONDS",
            DEFAULT_VOICE_GATEWAY_TIMEOUT_SECONDS,
        )
        fallback_sample_rate = self._positive_int_from_env(
            "DIRECTOR_PIPER_SAMPLE_RATE",
            DEFAULT_DIRECTOR_PIPER_SAMPLE_RATE,
        )
        max_chars = self._positive_int_from_env(
            "MAX_DIRECTOR_SPEECH_CHARS",
            DEFAULT_MAX_DIRECTOR_SPEECH_CHARS,
        )

        speech_text = " ".join((text or "").split())
        if not speech_text:
            raise ValueError("EMPTY_SPEECH_TEXT")
        if len(speech_text) > max_chars:
            raise ValueError("SPEECH_TOO_LONG")

        url = f"{gateway_url.rstrip('/')}/api/v1/tts/wav"
        headers = {
            "Accept": "audio/wav",
            "Content-Type": "application/json",
            "X-NapsterTec-Key": gateway_key,
        }

        try:
            response = await self._get_http_client().post(
                url,
                json={"text": speech_text},
                headers=headers,
                timeout=timeout_seconds,
            )
        except httpx.TimeoutException:
            raise ValueError("VOICE_GATEWAY_TIMEOUT")
        except httpx.RequestError:
            logger.warning("[DirectorVoice] Voice gateway transport unavailable")
            raise ValueError("VOICE_GATEWAY_UNAVAILABLE")
        except ValueError:
            raise
        except Exception:
            logger.exception("[DirectorVoice] Unexpected voice gateway transport failure")
            raise ValueError("VOICE_GATEWAY_UNAVAILABLE")

        if response.status_code != 200:
            self._raise_for_gateway_error(response)
        if not response.content:
            raise ValueError("VOICE_GENERATION_FAILED")

        sample_rate = fallback_sample_rate
        sample_rate_header = response.headers.get("X-Sample-Rate")
        if sample_rate_header:
            try:
                parsed_sample_rate = int(sample_rate_header)
                if parsed_sample_rate > 0:
                    sample_rate = parsed_sample_rate
            except (TypeError, ValueError):
                pass

        return DirectorAudioResult(
            audio_bytes=response.content,
            audio_format="wav",
            sample_rate=sample_rate,
            channels=1,
        )

    async def generate_briefing_audio(self, request: DirectorVoiceRequest) -> bytes:
        """Resolve a canonical briefing and return its WAV bytes."""

        speech_text = ""

        try:
            if request.briefing_type == "RAW":
                speech_text = request.text or ""
            elif request.briefing_type == "COMPANY_STATUS":
                briefing = executive_briefing_service.generate_company_status_briefing()
                speech_text = getattr(briefing, "speech_text", "")
            elif request.briefing_type == "DAILY":
                briefing = executive_briefing_service.generate_daily_briefing()
                speech_text = getattr(briefing, "speech_text", "")
            elif request.briefing_type == "OBJECTIVE" and request.target_id:
                briefing = executive_briefing_service.generate_objective_briefing(request.target_id)
                speech_text = getattr(briefing, "speech_text", "")
            elif request.briefing_type == "DEPARTMENT" and request.target_id:
                briefing = executive_briefing_service.generate_department_briefing(request.target_id)
                speech_text = getattr(briefing, "speech_text", "")
            elif request.briefing_type == "FINANCE":
                briefing = executive_briefing_service.generate_finance_briefing()
                speech_text = getattr(briefing, "speech_text", "")
            else:
                raise ValueError("INVALID_BRIEFING_TYPE")
        except ValueError:
            raise
        except Exception:
            logger.exception("[DirectorVoice] Briefing text resolution failed")
            raise ValueError("BRIEFING_GENERATION_FAILED")

        result = await self.synthesize_text(speech_text)
        return result.audio_bytes

director_voice_service = DirectorVoiceService()
