import os
import logging
import re
from typing import AsyncGenerator, Dict, Any, List, Optional

from openai import AsyncOpenAI

from .config import openrouter_config
from .models import MODEL_REGISTRY
from app.providers.base import BaseProviderPlugin
from app.engine.models import ProviderHealth, Capability

logger = logging.getLogger(__name__)


class ProviderRateLimitError(Exception):
    def __init__(self, message: str, retry_after_seconds: float = 30.0):
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class OpenRouterProvider(BaseProviderPlugin):
    """
    Production-ready OpenRouter integration.

    Native providers such as Groq and Kimi are deliberately excluded from
    model resolution. Poolside and Google remain accepted here because the
    current centralized registry stores their OpenRouter-routed model IDs
    under their upstream maker names.
    """

    _OPENROUTER_PROVIDER_NAMES = frozenset({"openrouter", "poolside", "google"})

    def __init__(self):
        api_key = os.getenv("OPENROUTER_API_KEY")
        if api_key:
            api_key = str(api_key).strip().strip("\"'")
        self._api_key = api_key or "UNSET"
        self.last_model_id: Optional[str] = None
        self.last_retry_after_seconds: Optional[float] = None

        headers = {
            "HTTP-Referer": getattr(openrouter_config, "site_url", "https://localhost"),
            "X-Title": getattr(openrouter_config, "site_name", "NIE Engine"),
            "Authorization": f"Bearer {self._api_key}",
        }

        self.client = AsyncOpenAI(
            api_key=self._api_key,
            base_url=getattr(openrouter_config, "base_url", "https://openrouter.ai/api/v1"),
            timeout=getattr(openrouter_config, "timeout_seconds", 60),
            max_retries=getattr(openrouter_config, "max_retries", 3),
            default_headers=headers,
        )

    @property
    def name(self) -> str:
        return "openrouter"

    def _is_openrouter_model(self, model: Any) -> bool:
        provider = str(getattr(model, "provider", "")).casefold()
        model_id = str(getattr(model, "model_id", ""))

        if provider == "openrouter":
            return True

        # Backward compatibility for the existing registry: Poolside and
        # Google entries contain OpenRouter-style provider/model identifiers.
        return (
            provider in self._OPENROUTER_PROVIDER_NAMES
            and "/" in model_id
        )

    @property
    def supported_capabilities(self) -> List[Capability]:
        capabilities = set()
        for model in MODEL_REGISTRY.values():
            if model.enabled and self._is_openrouter_model(model):
                capabilities.update(model.capabilities)
        return list(capabilities)

    def _build_messages(
        self, prompt: str, attachments: Optional[List[Any]] = None
    ) -> List[Dict[str, Any]]:
        system_text = ""
        user_text = prompt

        if "[System Instruction]" in prompt:
            parts = prompt.split("[System Instruction]", 1)[1].split("\n\n", 1)
            if len(parts) == 2:
                system_text, user_text = parts[0].strip(), parts[1].strip()

        if attachments:
            user_content = [{"type": "text", "text": user_text}]
            for att in attachments:
                url = getattr(att, "url", None) or (
                    att.get("url") if isinstance(att, dict) else None
                )
                if url:
                    user_content.append(
                        {
                            "type": "image_url",
                            "image_url": {"url": url},
                        }
                    )
        else:
            user_content = user_text

        messages = []
        if system_text:
            messages.append({"role": "system", "content": system_text})
        messages.append({"role": "user", "content": user_content})

        return messages

    def _extract_retry_after(
        self, exc: Exception, default_seconds: float = 30.0
    ) -> float:
        for attr in ("retry_after_seconds", "retry_after", "cooldown_seconds"):
            value = getattr(exc, attr, None)
            if isinstance(value, (int, float)) and value > 0:
                return float(value)

        text = str(exc)
        match = re.search(
            r"(retry after|retry-after|cooldown)\s*[:= ]\s*(\d+(?:\.\d+)?)",
            text,
            re.IGNORECASE,
        )
        if match:
            try:
                return float(match.group(2))
            except Exception:
                pass

        return default_seconds

    def _is_rate_limit_error(self, exc: Exception) -> bool:
        text = str(exc).lower()
        status_code = getattr(exc, "status_code", None)

        if status_code == 429:
            return True

        if "429" in text:
            return True

        if (
            "rate limited" in text
            or "temporarily rate-limited" in text
            or "rate limit" in text
        ):
            return True

        return False

    def _raise_rate_limit(self, model_id: str, exc: Exception) -> None:
        retry_after = self._extract_retry_after(exc, default_seconds=30.0)
        self.last_retry_after_seconds = retry_after
        raise ProviderRateLimitError(
            f"OpenRouter model {model_id} rate limited",
            retry_after_seconds=retry_after,
        ) from exc

    def _resolve_model_id(
        self,
        capability: str | Capability,
        attachments: Optional[List[Any]] = None,
    ) -> str:
        effective_capability = (
            Capability.VISION
            if attachments
            and (
                capability == Capability.CHAT
                or str(capability).casefold() == Capability.CHAT.value
            )
            else (
                Capability(capability)
                if isinstance(capability, str)
                else capability
            )
        )

        capable_models = [
            model
            for model in MODEL_REGISTRY.values()
            if (
                model.enabled
                and effective_capability in model.capabilities
                and self._is_openrouter_model(model)
            )
        ]

        if not capable_models:
            raise ValueError(
                "No enabled OpenRouter-routable model registered for "
                f"capability: {effective_capability}"
            )

        capable_models.sort(key=lambda model: model.priority, reverse=True)
        return capable_models[0].model_id

    async def check_health(self) -> ProviderHealth:
        if self._api_key == "UNSET" or not self._api_key:
            return ProviderHealth.UNHEALTHY
        try:
            await self.client.models.list()
            return ProviderHealth.HEALTHY
        except Exception as e:
            logger.error(f"[OpenRouter] Health check failed: {e}")
            return ProviderHealth.UNHEALTHY

    async def generate(
        self,
        prompt: str,
        capability: str = "chat",
        attachments: Optional[List[Any]] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        model_id = self._resolve_model_id(capability, attachments=attachments)
        self.last_model_id = model_id
        self.last_retry_after_seconds = None
        messages = self._build_messages(prompt, attachments=attachments)

        try:
            response = await self.client.chat.completions.create(
                model=model_id,
                messages=messages,
                stream=False,
                **kwargs,
            )
            return {
                "response": response.choices[0].message.content,
                "provider": self.name,
                "model_used": model_id,
                "tokens_used": response.usage.total_tokens if response.usage else 0,
            }
        except Exception as e:
            logger.error(f"[OpenRouter] Generation failed on {model_id}: {e}")
            if self._is_rate_limit_error(e):
                self._raise_rate_limit(model_id, e)
            raise Exception("ProviderException") from e

    async def generate_stream(
        self,
        prompt: str,
        capability: str = "chat",
        attachments: Optional[List[Any]] = None,
        **kwargs,
    ) -> AsyncGenerator[str, None]:
        model_id = self._resolve_model_id(capability, attachments=attachments)
        self.last_model_id = model_id
        self.last_retry_after_seconds = None
        messages = self._build_messages(prompt, attachments=attachments)

        try:
            stream = await self.client.chat.completions.create(
                model=model_id,
                messages=messages,
                stream=True,
                **kwargs,
            )

            async for chunk in stream:
                if chunk.choices and chunk.choices[0].delta.content is not None:
                    yield chunk.choices[0].delta.content

        except Exception as e:
            logger.error(f"[OpenRouter] Streaming failed on {model_id}: {e}")
            if self._is_rate_limit_error(e):
                self._raise_rate_limit(model_id, e)
            raise Exception("ProviderException") from e
