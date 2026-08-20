import inspect
import logging
import re
import time
from typing import List, AsyncGenerator, Dict, Any, Optional, Tuple

from app.engine.models import Capability, ProviderHealth
from app.providers.base import BaseProviderPlugin

logger = logging.getLogger(__name__)

# Router-only metadata understood by Groq's workload selector. These hints must
# never reach OpenAI-compatible or other provider SDK request payloads.
_GROQ_ROUTING_HINTS = {"is_structured", "retry_attempt"}


def _cap_value(cap: Any) -> str:
    return cap.value if hasattr(cap, "value") else str(cap)


def _exc_text(exc: Exception) -> str:
    return f"{type(exc).__name__}: {exc}"


class CapabilityRouter:
    def __init__(self, provider_registry=None):
        self._providers: Dict[str, BaseProviderPlugin] = {}
        self._rate_limited_until: Dict[Tuple[str, str], float] = {}
        if provider_registry:
            for _, provider in getattr(provider_registry, "providers", {}).items():
                self.register_provider(provider)

    def register_provider(self, provider: BaseProviderPlugin) -> None:
        self._providers[provider.name] = provider
        logger.info(f"[CapabilityRouter] Registered provider: {provider.name}")

    def _provider_key(self, provider_name: str, model_id: Optional[str] = None) -> Tuple[str, str]:
        return (provider_name, model_id or "unknown")

    def _is_rate_limited(self, exc: Exception) -> bool:
        text = _exc_text(exc).lower()
        status_code = getattr(exc, "status_code", None)

        if status_code == 429:
            return True

        if getattr(exc, "retry_after_seconds", None) is not None:
            return True

        if "rate_limit" in text or "rate limited" in text or "temporarily rate-limited" in text:
            return True

        if "429" in text:
            return True

        return False

    def _extract_retry_after(self, exc: Exception, default_seconds: float = 30.0) -> float:
        for attr in ("retry_after_seconds", "retry_after", "cooldown_seconds"):
            value = getattr(exc, attr, None)
            if isinstance(value, (int, float)) and value > 0:
                return float(value)

        text = str(exc)

        # Try to parse "retry after X" / "cooldown X"
        m = re.search(r"(retry after|cooldown)\s*[:= ]\s*(\d+(?:\.\d+)?)", text, re.IGNORECASE)
        if m:
            try:
                return float(m.group(2))
            except Exception:
                pass

        return default_seconds

    def _is_in_cooldown(self, provider_name: str, model_id: Optional[str] = None) -> bool:
        now = time.time()
        key = self._provider_key(provider_name, model_id)
        until = self._rate_limited_until.get(key, 0.0)

        if now < until:
            return True

        provider_key = self._provider_key(provider_name, None)
        until_provider = self._rate_limited_until.get(provider_key, 0.0)
        return now < until_provider

    def _mark_cooldown(self, provider_name: str, model_id: Optional[str], seconds: float) -> None:
        until = time.time() + max(1.0, float(seconds))
        self._rate_limited_until[self._provider_key(provider_name, model_id)] = until
        self._rate_limited_until[self._provider_key(provider_name, None)] = until
        logger.warning(
            f"[CapabilityRouter] Cooldown set for provider={provider_name} model={model_id or 'unknown'} "
            f"for {seconds:.1f}s"
        )

    async def _get_eligible_providers(
        self,
        required_capabilities: List[Capability],
        preferences: List[str],
    ) -> List[BaseProviderPlugin]:
        eligible: List[BaseProviderPlugin] = []

        required_values = {_cap_value(cap) for cap in required_capabilities}

        for _, provider in self._providers.items():
            try:
                if hasattr(provider, "supported_capabilities"):
                    provider_values = {
                        _cap_value(cap)
                        for cap in getattr(provider, "supported_capabilities", [])
                    }

                    if required_values.issubset(provider_values):
                        eligible.append(provider)
                else:
                    eligible.append(provider)
            except Exception as e:
                logger.warning(
                    f"[CapabilityRouter] Skipping provider {provider.name} while checking capabilities: {e}"
                )

        # FIXED: Case-insensitive and partial matching for provider names
        def preference_sorter(p: BaseProviderPlugin) -> int:
            p_name_lower = p.name.lower()
            prefs_lower = [pref.lower() for pref in preferences]

            for idx, pref in enumerate(prefs_lower):
                if pref == p_name_lower or pref in p_name_lower or p_name_lower in pref:
                    return idx

            if "auto" in prefs_lower:
                return prefs_lower.index("auto")

            return 999

        eligible.sort(key=preference_sorter)
        return eligible

    async def route_skill_execution(
        self,
        prompt: str,
        required_capabilities: List[Capability],
        preferences: List[str],
        attachments: Optional[List[Any]] = None,
        **kwargs
    ) -> AsyncGenerator[str, None]:
        eligible_providers = await self._get_eligible_providers(required_capabilities, preferences)

        if not eligible_providers:
            # FIXED: Raise exception instead of yielding plain text error string
            raise RuntimeError(
                f"[Router Error] No provider found supporting capabilities: "
                f"{[_cap_value(c) for c in required_capabilities]}"
            )

        last_error = None
        primary_cap = _cap_value(required_capabilities[0]) if required_capabilities else "chat"

        logger.info(
            "[CapabilityRouter] Eligible providers for capability %s: %s",
            primary_cap,
            [p.name for p in eligible_providers],
        )

        for provider in eligible_providers:
            model_hint = getattr(provider, "last_model_id", None)

            if self._is_in_cooldown(provider.name, model_hint):
                logger.warning(
                    f"[CapabilityRouter] Skipping provider={provider.name} model={model_hint or 'unknown'} "
                    f"due to active cooldown"
                )
                continue

            logger.info(f"[CapabilityRouter] Evaluating provider: {provider.name}")

            try:
                health = await provider.check_health()
                if health == ProviderHealth.UNHEALTHY:
                    logger.warning(f"[CapabilityRouter] Provider {provider.name} is UNHEALTHY. Failing over...")
                    continue

                logger.info(f"[CapabilityRouter] Routing stream to healthy provider: {provider.name}")

                gen_method = provider.generate_stream
                sig = inspect.signature(gen_method)
                call_kwargs = {}

                if "capability" in sig.parameters:
                    call_kwargs["capability"] = primary_cap
                if "attachments" in sig.parameters:
                    call_kwargs["attachments"] = attachments

                accepts_var_kwargs = any(
                    p.kind == inspect.Parameter.VAR_KEYWORD
                    for p in sig.parameters.values()
                )

                # Forward ordinary options only when the provider explicitly
                # declares them. Internal routing hints are additionally allowed
                # for Groq, which consumes them before building its SDK payload.
                for k, v in kwargs.items():
                    is_groq_hint = (
                        provider.name.lower() == "groq"
                        and k in _GROQ_ROUTING_HINTS
                        and accepts_var_kwargs
                    )
                    if k in sig.parameters or is_groq_hint:
                        call_kwargs[k] = v

                logger.info(
                    f"[CapabilityRouter] Selected provider={provider.name} capability={primary_cap}"
                )

                stream = gen_method(prompt, **call_kwargs)

                async for chunk in stream:
                    yield chunk

                return

            except Exception as e:
                last_error = str(e)
                text = _exc_text(e)

                if self._is_rate_limited(e):
                    retry_after = self._extract_retry_after(e, default_seconds=30.0)
                    model_used = getattr(provider, "last_model_id", None)
                    self._mark_cooldown(provider.name, model_used, retry_after)

                    logger.warning(
                        f"[CapabilityRouter] Rate limit from provider={provider.name} "
                        f"model={model_used or 'unknown'} -> failover in {retry_after:.1f}s"
                    )
                    continue

                logger.error(
                    f"[CapabilityRouter] Exception from {provider.name}: {text}. Failing over..."
                )
                continue

        # FIXED: Raise RuntimeError so callers catch exceptions rather than receiving non-JSON strings
        raise RuntimeError(f"[Router Execution Error] All eligible providers failed. Last error: {last_error}")
