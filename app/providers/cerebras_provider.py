"""
NapsterTec AI - Cerebras Inference Provider
Module: app/providers/cerebras_provider.py
"""
import os
import json
import logging
import re
import httpx
from typing import AsyncGenerator, Dict, Any, List, Optional
from dotenv import load_dotenv

from app.providers.base import BaseProviderPlugin
from app.engine.models import ProviderHealth, Capability

load_dotenv()
logger = logging.getLogger(__name__)

# --- Model Profiles Configuration with Safe Defaults ---
CEREBRAS_FAST_MODEL = os.getenv("CEREBRAS_FAST_MODEL", "llama3.1-8b")
CEREBRAS_REASONING_MODEL = os.getenv("CEREBRAS_REASONING_MODEL", "gpt-oss-120b")

class ProviderRateLimitError(Exception):
    def __init__(self, message: str, retry_after_seconds: float = 30.0):
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class ProviderPayloadTooLargeError(Exception):
    def __init__(
        self,
        message: str,
        prompt_chars: int,
        compacted_prompt_chars: int,
        model_id: str,
    ):
        super().__init__(message)
        self.prompt_chars = prompt_chars
        self.compacted_prompt_chars = compacted_prompt_chars
        self.model_id = model_id
        self.non_retryable_same_payload = True


class CerebrasProvider(BaseProviderPlugin):
    MAX_PROMPT_CHARS = int(os.getenv("CEREBRAS_MAX_PROMPT_CHARS", "28000"))
    SYSTEM_HEAD_BUDGET = int(os.getenv("CEREBRAS_SYSTEM_HEAD_BUDGET", "9000"))
    USER_TAIL_BUDGET = int(os.getenv("CEREBRAS_USER_TAIL_BUDGET", "14000"))

    def __init__(self):
        self.last_model_id: Optional[str] = None
        self.last_retry_after_seconds: Optional[float] = None
        self.last_prompt_chars: Optional[int] = None
        self.last_compacted_prompt_chars: Optional[int] = None
        self.last_prompt_was_compacted: bool = False
        self.base_url = "https://api.cerebras.ai/v1"

    @property
    def name(self) -> str:
        return "cerebras"

    @property
    def supported_capabilities(self) -> List[Capability]:
        # VISION is intentionally excluded as requested until verified.
        return [
            Capability.CHAT,
            Capability.RESEARCH,
            Capability.SEARCH,
            Capability.CODE,
        ]

    def _classify_workload(
        self,
        prompt: str,
        capability: str = "chat",
        is_structured: bool = False,
    ) -> str:
        """
        Deterministic classifier for Cerebras workload.
        """
        prompt_lower = prompt.lower()
        prompt_len = len(prompt)

        reasoning_signals = (
            "fault-tolerant",
            "architecture",
            "deep research",
            "engineering review",
            "governance review",
            "technical solution architect",
            "business solution architect",
        )
        if any(signal in prompt_lower for signal in reasoning_signals):
            return "REASONING"

        if is_structured:
            if prompt_len > 6000:
                return "REASONING"
            return "FAST"

        if prompt_len > 6000 and capability in ("code", "research"):
            return "REASONING"

        return "FAST"

    def _get_model_candidates(self, workload: str, retry_attempt: int = 0) -> List[str]:
        """
        Returns model candidates based on workload.
        Maximum 2 models per execution cycle.
        """
        chains = {
            "FAST": [CEREBRAS_FAST_MODEL, CEREBRAS_REASONING_MODEL],
            "REASONING": [CEREBRAS_REASONING_MODEL, CEREBRAS_FAST_MODEL],
        }
        candidates = chains.get(workload, [CEREBRAS_FAST_MODEL, CEREBRAS_REASONING_MODEL])

        # Formatter retry swap
        if retry_attempt > 0 and len(candidates) > 1:
            candidates = [candidates[1], candidates[0]]

        return candidates

    def _extract_retry_after(self, exc: Exception, default_seconds: float = 30.0) -> float:
        text = str(exc)
        match = re.search(r"(retry after|retry-after|cooldown)\s*[:= ]\s*(\d+(?:\.\d+)?)", text, re.IGNORECASE)
        if match:
            try:
                return float(match.group(2))
            except Exception:
                pass
        return default_seconds

    def _is_rate_limit_error(self, exc: Exception) -> bool:
        text = str(exc).lower()
        status_code = getattr(exc, "status_code", None)
        return status_code == 429 or "429" in text or "rate limited" in text

    def _is_payload_too_large_error(self, exc: Exception) -> bool:
        text = str(exc).lower()
        status_code = getattr(exc, "status_code", None)
        return status_code == 413 or "413 payload too large" in text or "request entity too large" in text

    def _raise_rate_limit(self, model_id: str, exc: Exception) -> None:
        retry_after = self._extract_retry_after(exc, default_seconds=30.0)
        self.last_retry_after_seconds = retry_after
        raise ProviderRateLimitError(f"Cerebras model {model_id} rate limited", retry_after_seconds=retry_after) from exc

    def _compact_prompt(self, prompt: str) -> str:
        original_chars = len(prompt)
        self.last_prompt_chars = original_chars

        if original_chars <= self.MAX_PROMPT_CHARS:
            self.last_compacted_prompt_chars = original_chars
            self.last_prompt_was_compacted = False
            return prompt

        head_budget = min(self.SYSTEM_HEAD_BUDGET, self.MAX_PROMPT_CHARS // 2)
        tail_budget = min(self.USER_TAIL_BUDGET, self.MAX_PROMPT_CHARS - head_budget - 500)
        tail_budget = max(4000, tail_budget)

        omitted = max(0, original_chars - head_budget - tail_budget)
        compacted = (
            prompt[:head_budget]
            + "\n\n"
            + f"[CEREBRAS CONTEXT COMPACTION: {omitted} middle characters omitted. Preserve the task goal, latest evidence, and JSON contract.]\n\n"
            + prompt[-tail_budget:]
        )

        compacted = compacted[: self.MAX_PROMPT_CHARS]
        self.last_compacted_prompt_chars = len(compacted)
        self.last_prompt_was_compacted = True

        logger.warning("[CerebrasProvider] Prompt compacted model=%s chars=%s->%s", self.last_model_id or "unknown", original_chars, len(compacted))
        return compacted

    def _build_messages(self, prompt: str) -> List[Dict[str, Any]]:
        compacted_prompt = self._compact_prompt(prompt)
        system_text = ""
        user_text = compacted_prompt

        if "[System Instruction]" in compacted_prompt:
            parts = compacted_prompt.split("[System Instruction]", 1)[1].split("\n\n", 1)
            if len(parts) == 2:
                system_text, user_text = parts[0].strip(), parts[1].strip()

        messages: List[Dict[str, Any]] = []
        if system_text:
            messages.append({"role": "system", "content": system_text})
        messages.append({"role": "user", "content": user_text})
        return messages

    def _clean_internal_kwargs(self, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        """Safely isolates and removes internal NIE metadata from reaching external APIs."""
        safe_kwargs = kwargs.copy()
        # Pop explicit NIE structured routing hints
        safe_kwargs.pop("is_structured", None)
        safe_kwargs.pop("retry_attempt", None)
        safe_kwargs.pop("workload_type", None)
        safe_kwargs.pop("cognitive_json", None)
        safe_kwargs.pop("capability", None)
        safe_kwargs.pop("attachments", None)
        return safe_kwargs

    async def check_health(self) -> ProviderHealth:
        api_key = os.getenv("CEREBRAS_API_KEY")
        if not api_key or api_key == "UNSET" or str(api_key).strip() == "":
            logger.warning("[CerebrasProvider] CEREBRAS_API_KEY is not set.")
            return ProviderHealth.UNHEALTHY

        cleaned_key = api_key.strip().strip("\"'")
        headers = {
            "Authorization": f"Bearer {cleaned_key}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(f"{self.base_url}/models", headers=headers, timeout=8.0)
                return ProviderHealth.HEALTHY if response.status_code == 200 else ProviderHealth.UNHEALTHY
        except Exception as exc:
            logger.error("[CerebrasProvider] Health check failed: %s", exc)
            return ProviderHealth.UNHEALTHY

    async def generate(self, prompt: str, capability: str = "chat", **kwargs) -> Dict[str, Any]:
        api_key = os.getenv("CEREBRAS_API_KEY")
        if not api_key:
            raise Exception("CEREBRAS_API_KEY missing")

        cleaned_key = api_key.strip().strip("\"'")
        headers = {
            "Authorization": f"Bearer {cleaned_key}",
            "Content-Type": "application/json",
        }

        # Extract internal NIE metadata
        is_structured = kwargs.get("is_structured", False)
        retry_attempt = kwargs.get("retry_attempt", 0)
        safe_kwargs = self._clean_internal_kwargs(kwargs)

        workload = self._classify_workload(prompt, capability=capability, is_structured=is_structured)
        candidates = self._get_model_candidates(workload, retry_attempt=retry_attempt)
        messages = self._build_messages(prompt)

        # Apply token conservation for strict JSON
        if is_structured and workload == "FAST":
            safe_kwargs.setdefault("max_tokens", 2048)

        last_exc: Optional[Exception] = None

        for attempt_idx, model_id in enumerate(candidates):
            self.last_model_id = model_id
            self.last_retry_after_seconds = None

            if attempt_idx == 0:
                logger.info("[CerebrasProvider] Workload=%s selected model=%s", workload, model_id)

            payload = {
                "model": model_id,
                "messages": messages,
                "stream": False,
                **safe_kwargs,
            }

            try:
                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        f"{self.base_url}/chat/completions",
                        headers=headers,
                        json=payload,
                        timeout=60.0,
                    )

                    if response.status_code == 429:
                        logger.warning("[CerebrasProvider] Rate limited model=%s; returning control to CapabilityRouter", model_id)
                        self._raise_rate_limit(model_id, Exception(response.text))

                    if response.status_code == 413:
                        raise ProviderPayloadTooLargeError(
                            f"Cerebras rejected request for {model_id}: 413 Payload Too Large",
                            self.last_prompt_chars or len(prompt),
                            self.last_compacted_prompt_chars or len(prompt),
                            model_id,
                        )

                    response.raise_for_status()
                    data = response.json()
                    return {
                        "response": data["choices"][0]["message"]["content"],
                        "provider": self.name,
                        "model_used": model_id,
                        "tokens_used": data.get("usage", {}).get("total_tokens", 0),
                    }

            except (ProviderRateLimitError, ProviderPayloadTooLargeError):
                raise
            except Exception as exc:
                last_exc = exc
                logger.error("[CerebrasProvider] Generation failed on %s: %s", model_id, exc)
                if self._is_payload_too_large_error(exc):
                    raise ProviderPayloadTooLargeError(f"Cerebras 413", self.last_prompt_chars, self.last_compacted_prompt_chars, model_id) from exc
                if self._is_rate_limit_error(exc):
                    self._raise_rate_limit(model_id, exc)
                
                # Single safe fallback allowed for general errors
                if attempt_idx + 1 >= len(candidates):
                    raise Exception("ProviderException") from exc

        if last_exc:
            raise last_exc
        raise Exception("ProviderException")

    async def generate_stream(self, prompt: str, capability: str = "chat", **kwargs) -> AsyncGenerator[str, None]:
        api_key = os.getenv("CEREBRAS_API_KEY")
        if not api_key:
            raise Exception("CEREBRAS_API_KEY missing")

        cleaned_key = api_key.strip().strip("\"'")
        headers = {
            "Authorization": f"Bearer {cleaned_key}",
            "Content-Type": "application/json",
        }

        # Extract internal NIE metadata
        is_structured = kwargs.get("is_structured", False)
        retry_attempt = kwargs.get("retry_attempt", 0)
        safe_kwargs = self._clean_internal_kwargs(kwargs)

        workload = self._classify_workload(prompt, capability=capability, is_structured=is_structured)
        candidates = self._get_model_candidates(workload, retry_attempt=retry_attempt)
        messages = self._build_messages(prompt)

        if is_structured and workload == "FAST":
            safe_kwargs.setdefault("max_tokens", 2048)

        for attempt_idx, model_id in enumerate(candidates):
            self.last_model_id = model_id
            self.last_retry_after_seconds = None

            if attempt_idx == 0:
                logger.info("[CerebrasProvider] Workload=%s selected model=%s", workload, model_id)

            payload = {
                "model": model_id,
                "messages": messages,
                "stream": True,
                **safe_kwargs,
            }

            try:
                async with httpx.AsyncClient() as client:
                    async with client.stream("POST", f"{self.base_url}/chat/completions", headers=headers, json=payload, timeout=60.0) as response:
                        if response.status_code == 429:
                            logger.warning("[CerebrasProvider] Rate limited model=%s; returning control to CapabilityRouter", model_id)
                            body_text = (await response.aread()).decode("utf-8", errors="ignore")
                            self._raise_rate_limit(model_id, Exception(body_text))

                        if response.status_code == 413:
                            raise ProviderPayloadTooLargeError(
                                f"Cerebras 413 Payload Too Large",
                                self.last_prompt_chars,
                                self.last_compacted_prompt_chars,
                                model_id,
                            )

                        response.raise_for_status()

                        async for line in response.aiter_lines():
                            if not line.startswith("data: "):
                                continue

                            data_str = line[6:]
                            if data_str == "[DONE]":
                                break

                            try:
                                data_json = json.loads(data_str)
                            except json.JSONDecodeError:
                                continue

                            if "choices" not in data_json or not data_json["choices"]:
                                continue

                            content = data_json["choices"][0].get("delta", {}).get("content", "")
                            if content:
                                yield content
                        return

            except (ProviderRateLimitError, ProviderPayloadTooLargeError):
                raise
            except Exception as exc:
                logger.error("[CerebrasProvider] Streaming failed on %s: %s", model_id, exc)
                if self._is_payload_too_large_error(exc):
                    raise ProviderPayloadTooLargeError(f"Cerebras 413", self.last_prompt_chars, self.last_compacted_prompt_chars, model_id) from exc
                if self._is_rate_limit_error(exc):
                    self._raise_rate_limit(model_id, exc)
                
                if attempt_idx + 1 >= len(candidates):
                    raise