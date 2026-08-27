import logging
import os
import re
from typing import Any, AsyncGenerator, Dict, List, Optional

from openai import AsyncOpenAI

from .config import openrouter_config
from .models import MODEL_REGISTRY, get_model_chain_for_capability
from app.providers.base import BaseProviderPlugin
from app.engine.models import Capability, ProviderHealth

logger = logging.getLogger(__name__)


class ProviderRateLimitError(Exception):
    def __init__(self, message: str, retry_after_seconds: float = 30.0):
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class OpenRouterProvider(BaseProviderPlugin):
    """
    Cost-aware OpenRouter integration for NIE.

    Routing policy:
    - low          -> free / ultra-cheap models first
    - balanced     -> reliable low-cost paid models first
    - performance  -> stronger models first

    OpenRouter model fallback is used in addition to OpenRouter's normal
    provider-level failover. This means NIE can survive both an unhealthy
    provider endpoint and an unavailable model without hard-coding retries
    across the rest of the agent system.
    """

    _OPENROUTER_PROVIDER_NAMES = frozenset(
        {"openrouter", "poolside", "google", "qwen", "deepseek", "openai"}
    )

    def __init__(self):
        api_key = os.getenv("OPENROUTER_API_KEY")
        if api_key:
            api_key = str(api_key).strip().strip("\"'")
        self._api_key = api_key or "UNSET"

        self.last_model_id: Optional[str] = None
        self.last_retry_after_seconds: Optional[float] = None
        self.last_cost_usd: float = 0.0
        self.last_usage: Dict[str, Any] = {}

        headers = {
            "HTTP-Referer": getattr(
                openrouter_config, "site_url", "https://localhost"
            ),
            "X-Title": getattr(
                openrouter_config, "site_name", "NIE Engine"
            ),
            "Authorization": f"Bearer {self._api_key}",
        }

        self.client = AsyncOpenAI(
            api_key=self._api_key,
            base_url=getattr(
                openrouter_config,
                "base_url",
                "https://openrouter.ai/api/v1",
            ),
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
        self,
        prompt: str,
        attachments: Optional[List[Any]] = None,
    ) -> List[Dict[str, Any]]:
        system_text = ""
        user_text = prompt

        if "[System Instruction]" in prompt:
            parts = prompt.split("[System Instruction]", 1)[1].split(
                "\n\n", 1
            )
            if len(parts) == 2:
                system_text, user_text = (
                    parts[0].strip(),
                    parts[1].strip(),
                )

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
        self,
        exc: Exception,
        default_seconds: float = 30.0,
    ) -> float:
        for attr in (
            "retry_after_seconds",
            "retry_after",
            "cooldown_seconds",
        ):
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

        if status_code == 429 or "429" in text:
            return True

        return (
            "rate limited" in text
            or "temporarily rate-limited" in text
            or "rate limit" in text
        )

    def _raise_rate_limit(
        self,
        model_id: str,
        exc: Exception,
    ) -> None:
        retry_after = self._extract_retry_after(
            exc,
            default_seconds=30.0,
        )
        self.last_retry_after_seconds = retry_after
        raise ProviderRateLimitError(
            f"OpenRouter model {model_id} rate limited",
            retry_after_seconds=retry_after,
        ) from exc

    def _resolve_model_chain(
        self,
        capability: str | Capability,
        attachments: Optional[List[Any]] = None,
        *,
        cost_preference: str = "balanced",
        reasoning_level: str = "medium",
        model_override: Optional[str] = None,
    ) -> List[str]:
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

        chain = get_model_chain_for_capability(
            effective_capability,
            cost_preference=cost_preference,
            reasoning_level=reasoning_level,
            require_vision=bool(attachments),
        )

        if model_override:
            model_override = str(model_override).strip()
            chain = [
                model_override,
                *[model for model in chain if model != model_override],
            ]

        return chain

    def _routing_extra_body(
        self,
        chain: List[str],
        cost_preference: str,
        existing: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        body = dict(existing or {})

        # OpenRouter model fallbacks. The first entry is also passed as `model`
        # for OpenAI-SDK compatibility.
        body["models"] = chain

        # Ask OpenRouter to include live cost/token accounting.
        usage = dict(body.get("usage") or {})
        usage["include"] = True
        body["usage"] = usage

        # For explicitly low-cost traffic, select the cheapest provider endpoint.
        # Balanced leaves OpenRouter's normal price/reliability load balancing intact.
        if (cost_preference or "balanced").casefold() == "low":
            provider = dict(body.get("provider") or {})
            provider.setdefault("sort", "price")
            body["provider"] = provider

        return body

    @staticmethod
    def _usage_to_dict(usage: Any) -> Dict[str, Any]:
        if usage is None:
            return {}

        if hasattr(usage, "model_dump"):
            try:
                data = usage.model_dump()
                extra = getattr(usage, "model_extra", None)
                if isinstance(extra, dict):
                    data.update(extra)
                return data
            except Exception:
                pass

        result = {}
        for key in (
            "prompt_tokens",
            "completion_tokens",
            "total_tokens",
            "cost",
            "prompt_tokens_details",
            "completion_tokens_details",
        ):
            value = getattr(usage, key, None)
            if value is not None:
                result[key] = value
        return result

    def _record_usage(self, usage: Any) -> Dict[str, Any]:
        data = self._usage_to_dict(usage)
        self.last_usage = data

        cost = data.get("cost", 0.0)
        try:
            self.last_cost_usd = float(cost or 0.0)
        except Exception:
            self.last_cost_usd = 0.0

        return data

    async def check_health(self) -> ProviderHealth:
        if self._api_key == "UNSET" or not self._api_key:
            return ProviderHealth.UNHEALTHY

        try:
            await self.client.models.list()
            return ProviderHealth.HEALTHY
        except Exception as exc:
            logger.error("[OpenRouter] Health check failed: %s", exc)
            return ProviderHealth.UNHEALTHY

    async def generate(
        self,
        prompt: str,
        capability: str = "chat",
        attachments: Optional[List[Any]] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        # NIE-only routing hints are removed before forwarding to OpenAI/OpenRouter.
        cost_preference = kwargs.pop("cost_preference", "balanced")
        reasoning_level = kwargs.pop("reasoning_level", "medium")
        model_override = kwargs.pop("model_override", None)
        soft_cost_limit = kwargs.pop(
            "max_model_cost_per_request_usd",
            None,
        )

        existing_extra_body = kwargs.pop("extra_body", None)

        chain = self._resolve_model_chain(
            capability,
            attachments=attachments,
            cost_preference=cost_preference,
            reasoning_level=reasoning_level,
            model_override=model_override,
        )
        model_id = chain[0]

        self.last_model_id = model_id
        self.last_retry_after_seconds = None
        self.last_cost_usd = 0.0
        self.last_usage = {}

        messages = self._build_messages(
            prompt,
            attachments=attachments,
        )
        extra_body = self._routing_extra_body(
            chain,
            cost_preference,
            existing=existing_extra_body,
        )

        try:
            response = await self.client.chat.completions.create(
                model=model_id,
                messages=messages,
                stream=False,
                extra_body=extra_body,
                **kwargs,
            )

            actual_model = getattr(response, "model", None) or model_id
            self.last_model_id = actual_model
            usage = self._record_usage(
                getattr(response, "usage", None)
            )

            if (
                soft_cost_limit is not None
                and self.last_cost_usd > float(soft_cost_limit)
            ):
                logger.warning(
                    "[OpenRouter] Request cost %.6f exceeded soft NIE limit %.6f "
                    "(model=%s)",
                    self.last_cost_usd,
                    float(soft_cost_limit),
                    actual_model,
                )

            return {
                "response": response.choices[0].message.content,
                "provider": self.name,
                "model_used": actual_model,
                "tokens_used": int(usage.get("total_tokens", 0) or 0),
                "prompt_tokens": int(
                    usage.get("prompt_tokens", 0) or 0
                ),
                "completion_tokens": int(
                    usage.get("completion_tokens", 0) or 0
                ),
                "cost_usd": self.last_cost_usd,
                "routing_profile": cost_preference,
                "fallback_chain": chain,
            }

        except Exception as exc:
            logger.error(
                "[OpenRouter] Generation failed on chain %s: %s",
                chain,
                exc,
            )
            if self._is_rate_limit_error(exc):
                self._raise_rate_limit(model_id, exc)
            raise Exception("ProviderException") from exc

    async def generate_stream(
        self,
        prompt: str,
        capability: str = "chat",
        attachments: Optional[List[Any]] = None,
        **kwargs,
    ) -> AsyncGenerator[str, None]:
        cost_preference = kwargs.pop("cost_preference", "balanced")
        reasoning_level = kwargs.pop("reasoning_level", "medium")
        model_override = kwargs.pop("model_override", None)
        kwargs.pop("max_model_cost_per_request_usd", None)

        existing_extra_body = kwargs.pop("extra_body", None)

        chain = self._resolve_model_chain(
            capability,
            attachments=attachments,
            cost_preference=cost_preference,
            reasoning_level=reasoning_level,
            model_override=model_override,
        )
        model_id = chain[0]

        self.last_model_id = model_id
        self.last_retry_after_seconds = None
        self.last_cost_usd = 0.0
        self.last_usage = {}

        messages = self._build_messages(
            prompt,
            attachments=attachments,
        )
        extra_body = self._routing_extra_body(
            chain,
            cost_preference,
            existing=existing_extra_body,
        )

        try:
            stream = await self.client.chat.completions.create(
                model=model_id,
                messages=messages,
                stream=True,
                extra_body=extra_body,
                **kwargs,
            )

            async for chunk in stream:
                chunk_model = getattr(chunk, "model", None)
                if chunk_model:
                    self.last_model_id = chunk_model

                chunk_usage = getattr(chunk, "usage", None)
                if chunk_usage is not None:
                    self._record_usage(chunk_usage)

                if (
                    chunk.choices
                    and chunk.choices[0].delta.content is not None
                ):
                    yield chunk.choices[0].delta.content

        except Exception as exc:
            logger.error(
                "[OpenRouter] Streaming failed on chain %s: %s",
                chain,
                exc,
            )
            if self._is_rate_limit_error(exc):
                self._raise_rate_limit(model_id, exc)
            raise Exception("ProviderException") from exc
