"""
NapsterTec AI - Director Voice Synthesis Service
Module: app/services/director_voice_service.py
"""
import asyncio
from dataclasses import dataclass
import io
import logging
import os
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse

import httpx

from app.schemas.director_voice import DirectorVoiceRequest
from app.services.executive_briefing_service import executive_briefing_service

logger = logging.getLogger(__name__)

DEFAULT_ELEVENLABS_TIMEOUT_SECONDS = 30.0
DEFAULT_MAX_DIRECTOR_SPEECH_CHARS = 5000
DEFAULT_SAMPLE_RATE = 44100


@dataclass(frozen=True)
class DirectorAudioResult:
    """Audio and playback metadata returned by the voice service."""

    audio_bytes: bytes
    audio_format: str = "mp3"
    sample_rate: int = DEFAULT_SAMPLE_RATE
    channels: int = 1


class DirectorVoiceService:
    """Generate Director audio with resilient multi-tier synthesis:
    1. ElevenLabs (if configured and quota available)
    2. Piper Voice Gateway (if configured via VOICE_GATEWAY_URL)
    3. Sovereign Cloud Neural TTS via gTTS (zero-cost, zero-key, continuous uptime fallback)
    """

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

    @staticmethod
    def readiness() -> dict:
        """Expose safe TTS configuration diagnostics without returning secrets."""
        api_key = os.getenv("ELEVENLABS_API_KEY", "").strip()
        voice_id = os.getenv("ELEVENLABS_VOICE_ID", "").strip()
        voice_gateway_url = os.getenv("VOICE_GATEWAY_URL", "").strip()
        return {
            "configured": True,
            "elevenlabs_configured": bool(api_key and voice_id),
            "voice_gateway_configured": bool(voice_gateway_url),
            "fallback_available": True,
            "provider": "elevenlabs" if (api_key and voice_id) else ("piper" if voice_gateway_url else "gtts"),
            "model_id": os.getenv("ELEVENLABS_MODEL_ID", "eleven_multilingual_v2").strip(),
        }

    async def aclose(self) -> None:
        if self._http_client is not None and not self._http_client.is_closed:
            await self._http_client.aclose()

    @staticmethod
    def _extract_upstream_error(response: httpx.Response) -> Tuple[Optional[str], Optional[str], Optional[str]]:
        try:
            body: Dict[str, Any] = response.json()
        except Exception:
            return None, None, None

        detail = body.get("detail")
        if isinstance(detail, dict):
            code = detail.get("code") or detail.get("type")
            message = detail.get("message")
            request_id = detail.get("request_id")
            return code, message, request_id

        if isinstance(detail, str):
            return None, detail, None

        return None, None, None

    @staticmethod
    def _raise_for_upstream_error(response: httpx.Response) -> None:
        code, _message, _request_id = DirectorVoiceService._extract_upstream_error(response)

        if response.status_code == 402 or code == "paid_plan_required":
            logger.warning("[DirectorVoice] ElevenLabs plan restriction status=%s", response.status_code)
            raise ValueError("ELEVENLABS_PAID_PLAN_REQUIRED")

        if response.status_code in (401, 403):
            logger.warning("[DirectorVoice] ElevenLabs authorization failure status=%s", response.status_code)
            raise ValueError("ELEVENLABS_UNAUTHORIZED")

        if response.status_code == 429:
            logger.warning("[DirectorVoice] ElevenLabs rate limit status=%s", response.status_code)
            raise ValueError("ELEVENLABS_RATE_LIMITED")

        if 500 <= response.status_code <= 599:
            logger.warning("[DirectorVoice] ElevenLabs unavailable status=%s", response.status_code)
            raise ValueError("ELEVENLABS_UNAVAILABLE")

        raise ValueError("VOICE_GENERATION_FAILED")

    async def _synthesize_elevenlabs(self, speech_text: str) -> Optional[DirectorAudioResult]:
        api_key = os.getenv("ELEVENLABS_API_KEY", "").strip()
        voice_id = os.getenv("ELEVENLABS_VOICE_ID", "").strip()
        model_id = os.getenv("ELEVENLABS_MODEL_ID", "eleven_multilingual_v2").strip()

        if not api_key or not voice_id:
            return None

        try:
            timeout_seconds = float(os.getenv("ELEVENLABS_TIMEOUT_SECONDS", str(DEFAULT_ELEVENLABS_TIMEOUT_SECONDS)))
            if timeout_seconds <= 0:
                timeout_seconds = DEFAULT_ELEVENLABS_TIMEOUT_SECONDS
        except (TypeError, ValueError):
            timeout_seconds = DEFAULT_ELEVENLABS_TIMEOUT_SECONDS

        url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
        headers = {
            "Accept": "audio/mpeg",
            "Content-Type": "application/json",
            "xi-api-key": api_key,
        }
        payload = {
            "text": speech_text,
            "model_id": model_id,
            "voice_settings": {
                "stability": 0.5,
                "similarity_boost": 0.8,
                "style": 0.0,
                "use_speaker_boost": True,
            },
        }

        try:
            response = await self._get_http_client().post(
                url,
                json=payload,
                headers=headers,
                timeout=timeout_seconds,
            )
            if response.status_code == 200 and response.content:
                return DirectorAudioResult(
                    audio_bytes=response.content,
                    audio_format="mp3",
                    sample_rate=DEFAULT_SAMPLE_RATE,
                    channels=1,
                )
            logger.warning("[DirectorVoice] ElevenLabs HTTP %s (likely quota exhausted). Falling back...", response.status_code)
        except Exception as exc:
            logger.warning("[DirectorVoice] ElevenLabs request failed (%s). Falling back...", exc)

        return None

    async def _synthesize_piper_gateway(self, speech_text: str) -> Optional[DirectorAudioResult]:
        gateway_url = os.getenv("VOICE_GATEWAY_URL", "").strip().rstrip("/")
        if not gateway_url:
            return None

        key = os.getenv("VOICE_GATEWAY_API_KEY", "").strip()
        headers = {"Content-Type": "application/json"}
        if key:
            headers["X-NapsterTec-Key"] = key

        try:
            response = await self._get_http_client().post(
                f"{gateway_url}/api/v1/tts/wav",
                json={"text": speech_text},
                headers=headers,
                timeout=15.0,
            )
            if response.status_code == 200 and response.content:
                sample_rate = int(response.headers.get("X-Sample-Rate", "22050"))
                return DirectorAudioResult(
                    audio_bytes=response.content,
                    audio_format="wav",
                    sample_rate=sample_rate,
                    channels=1,
                )
            logger.warning("[DirectorVoice] Piper Gateway returned status %s", response.status_code)
        except Exception as exc:
            logger.warning("[DirectorVoice] Piper Gateway request failed: %s", exc)

        return None

    async def _synthesize_gtts(self, speech_text: str) -> DirectorAudioResult:
        def _generate():
            from gtts import gTTS
            fp = io.BytesIO()
            tts = gTTS(text=speech_text, lang="en", tld="com")
            tts.write_to_fp(fp)
            return fp.getvalue()

        audio_bytes = await asyncio.to_thread(_generate)
        return DirectorAudioResult(
            audio_bytes=audio_bytes,
            audio_format="mp3",
            sample_rate=24000,
            channels=1,
        )

    async def synthesize_text(self, text: str) -> DirectorAudioResult:
        """Synthesize normalized text through resilient multi-tier voice pipeline."""
        try:
            max_chars = int(os.getenv("MAX_DIRECTOR_SPEECH_CHARS", str(DEFAULT_MAX_DIRECTOR_SPEECH_CHARS)))
            if max_chars <= 0:
                max_chars = DEFAULT_MAX_DIRECTOR_SPEECH_CHARS
        except (TypeError, ValueError):
            max_chars = DEFAULT_MAX_DIRECTOR_SPEECH_CHARS

        speech_text = " ".join((text or "").split())
        if not speech_text:
            raise ValueError("EMPTY_SPEECH_TEXT")
        if len(speech_text) > max_chars:
            raise ValueError("SPEECH_TOO_LONG")

        # Tier 1: ElevenLabs (High fidelity)
        result = await self._synthesize_elevenlabs(speech_text)
        if result is not None:
            return result

        # Tier 2: Piper Voice Gateway (Self-hosted sovereign voice)
        result = await self._synthesize_piper_gateway(speech_text)
        if result is not None:
            return result

        # Tier 3: Sovereign Cloud Fallback (gTTS - 100% Free, zero-key, continuous uptime)
        try:
            return await self._synthesize_gtts(speech_text)
        except Exception as exc:
            logger.exception("[DirectorVoice] All voice synthesis tiers failed: %s", exc)
            raise ValueError("VOICE_GENERATION_FAILED")

    async def generate_briefing_audio(self, request: DirectorVoiceRequest) -> bytes:
        """Resolve a canonical briefing and return its audio bytes."""
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
