"""Moonshot/Kimi provider for the NapsterTec Intelligence Engine.

The provider uses Moonshot's OpenAI-compatible chat-completions API and keeps
all credentials and deployment-specific defaults in environment variables.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncGenerator, Mapping, Sequence
from typing import Any

from openai import (
    APIConnectionError,
    APIStatusError,
    APITimeoutError,
    AsyncOpenAI,
    AuthenticationError,
    BadRequestError,
    RateLimitError,
)

from app.providers.base import BaseProviderPlugin
from app.engine.models import Capability, ProviderHealth

logger = logging.getLogger(__name__)


DEFAULT_KIMI_BASE_URL = "https://api.moonshot.ai/v1"
DEFAULT_KIMI_MODEL = "kimi-k2.5"
DEFAULT_TIMEOUT_SECONDS = 120.0


class KimiProviderError(RuntimeError):
    """A safe, provider-level error suitable for router failover."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        retry_after: float | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after


class KimiRateLimitError(KimiProviderError):
    """Kimi rejected a request because its quota or rate limit was reached."""


class KimiProvider(BaseProviderPlugin):
    """NIE provider plugin backed by Moonshot/Kimi."""

    _CAPABILITIES = frozenset(
        {
            Capability.CHAT,
            Capability.CODING,
            Capability.SYSTEM_INSPECTION,
            Capability.RESEARCH,
            Capability.ROUTING,
            Capability.ANALYTICS,
        }
    )

    def __init__(
        self,
        api_key: str | None = None,
        base_url: str | None = None,
        default_model: str | None = None,
        timeout: float | None = None,
    ) -> None:
        self.api_key = api_key or os.getenv("KIMI_API_KEY") or os.getenv(
            "MOONSHOT_API_KEY"
        )
        self.base_url = (
            base_url
            or os.getenv("KIMI_BASE_URL")
            or os.getenv("MOONSHOT_BASE_URL")
            or DEFAULT_KIMI_BASE_URL
        ).rstrip("/")
        self.default_model = (
            default_model
            or os.getenv("KIMI_MODEL")
            or os.getenv("MOONSHOT_MODEL")
            or DEFAULT_KIMI_MODEL
        )
        self.timeout = timeout or self._env_float(
            "KIMI_TIMEOUT_SECONDS", DEFAULT_TIMEOUT_SECONDS
        )
        self._client: AsyncOpenAI | None = None

    @property
    def name(self) -> str:
        return "kimi"

    @property
    def provider_name(self) -> str:
        """Compatibility alias for health monitors using ``provider_name``."""
        return self.name

    @property
    def capabilities(self) -> set[str]:
        return {capability.value for capability in self._CAPABILITIES}

    @property
    def supported_capabilities(self) -> list[Capability]:
        """Capabilities consumed by NIE's capability router."""
        return list(self._CAPABILITIES)

    @property
    def client(self) -> AsyncOpenAI:
        if not self.api_key:
            raise KimiProviderError(
                "Kimi is not configured: set KIMI_API_KEY or MOONSHOT_API_KEY."
            )
        if self._client is None:
            self._client = AsyncOpenAI(
                api_key=self.api_key,
                base_url=self.base_url,
                timeout=self.timeout,
                max_retries=0,
            )
        return self._client

    async def check_health(self) -> ProviderHealth:
        """Return Kimi health using NIE's required provider contract."""
        if not self.api_key:
            return ProviderHealth.UNHEALTHY
        try:
            await self.client.models.list()
            return ProviderHealth.HEALTHY
        except Exception as exc:  # Health checks must never break provider discovery.
            logger.warning("Kimi health check failed: %s", type(exc).__name__)
            return ProviderHealth.UNHEALTHY

    async def health_check(self) -> bool:
        """Boolean compatibility wrapper for older health monitors."""
        return await self.check_health() == ProviderHealth.HEALTHY

    async def generate(
        self,
        prompt: str,
        capability: Any | None = None,
        *,
        system_prompt: str | None = None,
        messages: Sequence[Mapping[str, Any]] | None = None,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> str:
        request = self._request_args(
            prompt=prompt,
            capability=capability,
            system_prompt=system_prompt,
            messages=messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=False,
            extra=kwargs,
        )
        try:
            response = await self.client.chat.completions.create(**request)
            content = response.choices[0].message.content if response.choices else None
            if not content:
                raise KimiProviderError("Kimi returned an empty response.")
            return content
        except KimiProviderError:
            raise
        except Exception as exc:
            raise self._safe_error(exc) from exc

    async def generate_stream(
        self,
        prompt: str,
        capability: Any | None = None,
        *,
        system_prompt: str | None = None,
        messages: Sequence[Mapping[str, Any]] | None = None,
        model: str | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        **kwargs: Any,
    ) -> AsyncGenerator[str, None]:
        request = self._request_args(
            prompt=prompt,
            capability=capability,
            system_prompt=system_prompt,
            messages=messages,
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=True,
            extra=kwargs,
        )
        try:
            stream = await self.client.chat.completions.create(**request)
            async for chunk in stream:
                if not chunk.choices:
                    continue
                content = chunk.choices[0].delta.content
                if content:
                    yield content
        except Exception as exc:
            raise self._safe_error(exc) from exc

    async def close(self) -> None:
        if self._client is not None:
            await self._client.close()
            self._client = None

    def _request_args(
        self,
        *,
        prompt: str,
        capability: Any | None,
        system_prompt: str | None,
        messages: Sequence[Mapping[str, Any]] | None,
        model: str | None,
        temperature: float | None,
        max_tokens: int | None,
        stream: bool,
        extra: Mapping[str, Any],
    ) -> dict[str, Any]:
        if not isinstance(prompt, str):
            raise KimiProviderError("Kimi prompt must be a string.")

        payload: list[dict[str, Any]] = []
        if messages:
            payload.extend(dict(message) for message in messages)
        else:
            if system_prompt:
                payload.append({"role": "system", "content": system_prompt})
            payload.append({"role": "user", "content": prompt})

        request: dict[str, Any] = {
            "model": model or self._resolve_model(capability),
            "messages": payload,
            "stream": stream,
        }
        if temperature is not None:
            request["temperature"] = temperature
        if max_tokens is not None:
            request["max_tokens"] = max_tokens

        # Only documented OpenAI-compatible request fields are forwarded.
        allowed = {
            "frequency_penalty",
            "presence_penalty",
            "response_format",
            "seed",
            "stop",
            "tools",
            "tool_choice",
            "top_p",
            "user",
        }
        request.update({key: value for key, value in extra.items() if key in allowed})
        return request

    def _resolve_model(self, capability: Any | None) -> str:
        """Use a Kimi-owned registry model when available, otherwise env default."""
        if capability is None:
            return self.default_model
        try:
            from app.providers.openrouter.models import get_model_for_capability

            candidate = get_model_for_capability(capability, provider="kimi")
            if isinstance(candidate, str) and candidate:
                return candidate

            provider = str(getattr(candidate, "provider", "")).lower()
            model_id = getattr(candidate, "model_id", None) or getattr(
                candidate, "id", None
            )
            if provider in {"kimi", "moonshot"} and model_id:
                return str(model_id)
        except (ImportError, LookupError, TypeError, ValueError):
            # The first integration sprint may precede provider-aware registry
            # support. Falling back prevents cross-provider model selection.
            pass
        return self.default_model

    @staticmethod
    def _retry_after(exc: BaseException) -> float | None:
        response = getattr(exc, "response", None)
        headers = getattr(response, "headers", {}) or {}
        value = headers.get("retry-after") or headers.get("Retry-After")
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    def _safe_error(self, exc: Exception) -> KimiProviderError:
        if isinstance(exc, RateLimitError):
            return KimiRateLimitError(
                "Kimi rate limit or quota was exceeded; router failover is allowed.",
                status_code=429,
                retry_after=self._retry_after(exc),
            )
        if isinstance(exc, AuthenticationError):
            return KimiProviderError(
                "Kimi authentication failed; check the configured API key.",
                status_code=401,
            )
        if isinstance(exc, BadRequestError):
            return KimiProviderError(
                "Kimi rejected the request as invalid.",
                status_code=getattr(exc, "status_code", 400),
            )
        if isinstance(exc, (APITimeoutError, APIConnectionError)):
            return KimiProviderError("Kimi is temporarily unreachable.")
        if isinstance(exc, APIStatusError):
            status = getattr(exc, "status_code", None)
            return KimiProviderError(
                f"Kimi request failed with HTTP status {status or 'unknown'}.",
                status_code=status,
            )
        return KimiProviderError("Kimi generation failed unexpectedly.")

    @staticmethod
    def _env_float(name: str, default: float) -> float:
        value = os.getenv(name)
        if value is None:
            return default
        try:
            return float(value)
        except ValueError:
            logger.warning("Ignoring invalid %s value; using %.1f", name, default)
            return default


__all__ = ["KimiProvider", "KimiProviderError", "KimiRateLimitError"]
