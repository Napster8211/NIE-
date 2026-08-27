"""
NapsterTec AI - Director Voice Synthesis Service
Module: app/services/director_voice_service.py
"""
import logging
import os
from typing import Any, Dict, Optional, Tuple

import httpx

from app.schemas.director_voice import DirectorVoiceRequest
from app.services.executive_briefing_service import executive_briefing_service

logger = logging.getLogger(__name__)

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID")
ELEVENLABS_MODEL_ID = os.getenv("ELEVENLABS_MODEL_ID", "eleven_multilingual_v2")

MAX_DIRECTOR_SPEECH_CHARS = int(os.getenv("MAX_DIRECTOR_SPEECH_CHARS", "5000"))
ELEVENLABS_TIMEOUT_SECONDS = float(os.getenv("ELEVENLABS_TIMEOUT_SECONDS", "30"))


class DirectorVoiceService:
    """Generate Director briefing and conversational audio through ElevenLabs."""

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
        code, _message, request_id = DirectorVoiceService._extract_upstream_error(response)

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

    async def generate_briefing_audio(self, request: DirectorVoiceRequest) -> bytes:
        api_key = ELEVENLABS_API_KEY
        voice_id = ELEVENLABS_VOICE_ID
        model_id = ELEVENLABS_MODEL_ID

        if not api_key or not voice_id:
            logger.error(f"[DirectorVoice] Missing keys! API Key exists: {bool(api_key)}, Voice ID exists: {bool(voice_id)}")
            raise ValueError("VOICE_NOT_CONFIGURED")

        speech_text = ""

        try:
            if request.briefing_type == "RAW" and request.text:
                speech_text = request.text
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
            logger.exception("[DirectorVoice] Text resolution failed")
            raise ValueError("BRIEFING_GENERATION_FAILED")

        if not speech_text or not speech_text.strip():
            raise ValueError("EMPTY_SPEECH_TEXT")

        speech_text = " ".join(speech_text.split())
        if len(speech_text) > MAX_DIRECTOR_SPEECH_CHARS:
            raise ValueError("SPEECH_TOO_LONG")

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
                "stability": 0.7,
                "similarity_boost": 0.8,
                "style": 0.0,
                "use_speaker_boost": True,
            },
        }

        try:
            async with httpx.AsyncClient(timeout=ELEVENLABS_TIMEOUT_SECONDS) as client:
                response = await client.post(url, json=payload, headers=headers)

            if response.status_code != 200:
                self._raise_for_upstream_error(response)

            if not response.content:
                raise ValueError("VOICE_GENERATION_FAILED")

            return response.content
        except httpx.TimeoutException:
            raise ValueError("ELEVENLABS_TIMEOUT")
        except httpx.RequestError:
            logger.warning("[DirectorVoice] ElevenLabs network request failed", exc_info=True)
            raise ValueError("ELEVENLABS_UNAVAILABLE")
        except ValueError:
            raise
        except Exception:
            logger.exception("[DirectorVoice] Unexpected TTS exception")
            raise ValueError("ELEVENLABS_UNAVAILABLE")

director_voice_service = DirectorVoiceService()