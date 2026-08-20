"""
NapsterTec AI - Groq Provider with Multi-Model Workload Routing
Module: app/providers/groq_provider.py
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
GROQ_FAST_MODEL = os.getenv("GROQ_FAST_MODEL", "llama-3.1-8b-instant")
GROQ_BALANCED_MODEL = os.getenv("GROQ_BALANCED_MODEL", "openai/gpt-oss-20b")
GROQ_REASONING_MODEL = os.getenv("GROQ_REASONING_MODEL", "openai/gpt-oss-120b")
GROQ_LARGE_MODEL = os.getenv("GROQ_LARGE_MODEL", "llama-3.3-70b-versatile")


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


class GroqProvider(BaseProviderPlugin):
    MAX_PROMPT_CHARS = int(os.getenv("GROQ_MAX_PROMPT_CHARS", "28000"))
    SYSTEM_HEAD_BUDGET = int(os.getenv("GROQ_SYSTEM_HEAD_BUDGET", "9000"))
    USER_TAIL_BUDGET = int(os.getenv("GROQ_USER_TAIL_BUDGET", "14000"))

    def __init__(self):
        self.last_model_id: Optional[str] = None
        self.last_retry_after_seconds: Optional[float] = None
        self.last_prompt_chars: Optional[int] = None
        self.last_compacted_prompt_chars: Optional[int] = None
        self.last_prompt_was_compacted: bool = False

    @property
    def name(self) -> str:
        return "groq"

    @property
    def supported_capabilities(self) -> List[Capability]:
        return [
            Capability.CHAT,
            Capability.RESEARCH,
            Capability.SEARCH,
            Capability.VISION,
            Capability.CODE,
        ]

    def _classify_workload(
        self,
        prompt: str,
        capability: str = "chat",
        attachments: Optional[List[Any]] = None,
        is_structured: bool = False,
    ) -> str:
        """
        Deterministic, lightweight workload classifier.
        Separates strict cognitive JSON from normal/heavy prose reasoning.
        """
        if attachments:
            return "FAST"

        prompt_lower = prompt.lower()
        prompt_len = len(prompt)

        # A. STRICT COGNITIVE JSON BRANCH
        if is_structured:
            if prompt_len < 3000 and "fault-tolerant" not in prompt_lower and "architecture" not in prompt_lower:
                return "COGNITIVE_JSON_FAST"
            return "COGNITIVE_JSON_COMPLEX"

        # B. NORMAL / HEAVY PROSE REASONING BRANCH
        reasoning_signals = (
            "fault-tolerant",
            "architecture",
            "failover",
            "deep research",
            "engineering review",
            "governance review",
            "strategic decision",
            "multi-step",
            "technical solution architect",
            "business solution architect",
        )
        if any(signal in prompt_lower for signal in reasoning_signals):
            return "HEAVY_REASONING"

        if prompt_len > 12000:
            return "LARGE"

        if prompt_len > 4500:
            return "HEAVY_REASONING" if capability in ("code", "research") else "BALANCED"

        simple_calc_signals = (
            "calculate",
            "7 * 8",
            "7*8",
            "two sentences",
            "in one sentence",
            "what is",
            "simple",
            "extract",
            "classify",
        )
        if prompt_len < 1800 and any(signal in prompt_lower for signal in simple_calc_signals):
            return "FAST"

        if capability in ("chat", "search") and prompt_len < 1200:
            return "FAST"

        if capability in ("code", "research") or "write a python function" in prompt_lower:
            return "BALANCED"

        return "BALANCED"

    def _get_model_candidates(self, workload: str, retry_attempt: int = 0) -> List[str]:
        """
        Returns primary and fallback model candidates for a workload profile.
        Maximum 2 models per execution cycle.
        """
        chains = {
            "COGNITIVE_JSON_FAST": [GROQ_FAST_MODEL, GROQ_BALANCED_MODEL],
            "COGNITIVE_JSON_COMPLEX": [GROQ_BALANCED_MODEL, GROQ_FAST_MODEL],
            "FAST": [GROQ_FAST_MODEL, GROQ_BALANCED_MODEL],
            "BALANCED": [GROQ_BALANCED_MODEL, GROQ_FAST_MODEL],
            "HEAVY_REASONING": [GROQ_REASONING_MODEL, GROQ_BALANCED_MODEL],
            "LARGE": [GROQ_LARGE_MODEL, GROQ_BALANCED_MODEL],
        }
        candidates = chains.get(workload, [GROQ_BALANCED_MODEL, GROQ_FAST_MODEL])

        # If this is a format retry, attempt the fallback model candidate first
        if retry_attempt > 0 and len(candidates) > 1:
            candidates = [candidates[1], candidates[0]]

        return candidates

    def _extract_retry_after(
        self,
        exc: Exception,
        default_seconds: float = 30.0,
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
        return bool(
            status_code == 429
            or "429" in text
            or "rate limited" in text
            or "temporarily rate-limited" in text
            or "rate limit" in text
        )

    def _is_payload_too_large_error(self, exc: Exception) -> bool:
        text = str(exc).lower()
        status_code = getattr(exc, "status_code", None)
        return bool(
            status_code == 413
            or "413 payload too large" in text
            or "413 request entity too large" in text
            or "payload too large" in text
            or "request entity too large" in text
        )

    def _is_model_permission_error(self, exc: Exception) -> bool:
        text = str(exc).lower()
        status_code = getattr(exc, "status_code", None)
        return bool(
            status_code in (403, 404)
            or "403" in text
            or "404" in text
            or "model_not_found" in text
            or "permission_denied" in text
            or "does not exist" in text
        )

    def _raise_rate_limit(self, model_id: str, exc: Exception) -> None:
        retry_after = self._extract_retry_after(exc, default_seconds=30.0)
        self.last_retry_after_seconds = retry_after
        raise ProviderRateLimitError(
            f"Groq model {model_id} rate limited",
            retry_after_seconds=retry_after,
        ) from exc

    def _compact_prompt(self, prompt: str, model_id: Optional[str] = None) -> str:
        original_chars = len(prompt)
        self.last_prompt_chars = original_chars

        if original_chars <= self.MAX_PROMPT_CHARS:
            self.last_compacted_prompt_chars = original_chars
            self.last_prompt_was_compacted = False
            return prompt

        head_budget = min(
            self.SYSTEM_HEAD_BUDGET,
            self.MAX_PROMPT_CHARS // 2,
        )
        tail_budget = min(
            self.USER_TAIL_BUDGET,
            self.MAX_PROMPT_CHARS - head_budget - 500,
        )
        tail_budget = max(4000, tail_budget)

        omitted = max(0, original_chars - head_budget - tail_budget)
        compacted = (
            prompt[:head_budget]
            + "\n\n"
            + f"[GROQ CONTEXT COMPACTION: {omitted} middle characters omitted. "
              "Preserve the task goal, latest evidence, and required JSON contract.]\n\n"
            + prompt[-tail_budget:]
        )

        compacted = compacted[: self.MAX_PROMPT_CHARS]
        self.last_compacted_prompt_chars = len(compacted)
        self.last_prompt_was_compacted = True

        logger.warning(
            "[GroqProvider] Prompt compacted model=%s chars=%s->%s",
            model_id or self.last_model_id or "unknown",
            original_chars,
            len(compacted),
        )
        return compacted

    def _build_messages(
        self,
        prompt: str,
        attachments: Optional[List[Any]] = None,
        model_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        compacted_prompt = self._compact_prompt(prompt, model_id=model_id)
        system_text = ""
        user_text = compacted_prompt

        if "[System Instruction]" in compacted_prompt:
            parts = compacted_prompt.split(
                "[System Instruction]",
                1,
            )[1].split("\n\n", 1)
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

        messages: List[Dict[str, Any]] = []
        if system_text:
            messages.append({"role": "system", "content": system_text})
        messages.append({"role": "user", "content": user_content})
        return messages

    def _log_payload_diagnostics(
        self,
        model_id: str,
        messages: List[Dict[str, Any]],
        stream: bool,
    ) -> None:
        try:
            message_chars = len(
                json.dumps(messages, ensure_ascii=False, default=str)
            )
        except Exception:
            message_chars = -1

        logger.info(
            "[GroqProvider] Request diagnostics model=%s stream=%s "
            "prompt_chars=%s compacted_chars=%s messages_chars=%s compacted=%s",
            model_id,
            stream,
            self.last_prompt_chars,
            self.last_compacted_prompt_chars,
            message_chars,
            self.last_prompt_was_compacted,
        )

    async def check_health(self) -> ProviderHealth:
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key or api_key == "UNSET" or str(api_key).strip() == "":
            logger.warning("[GroqProvider] GROQ_API_KEY is not set or empty.")
            return ProviderHealth.UNHEALTHY

        cleaned_key = api_key.strip().strip("\"'")
        headers = {
            "Authorization": f"Bearer {cleaned_key}",
            "Content-Type": "application/json",
        }

        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(
                    "https://api.groq.com/openai/v1/models",
                    headers=headers,
                    timeout=10.0,
                )
                return (
                    ProviderHealth.HEALTHY
                    if response.status_code == 200
                    else ProviderHealth.UNHEALTHY
                )
        except Exception as exc:
            logger.error("[GroqProvider] Health check failed: %s", exc)
            return ProviderHealth.UNHEALTHY

    async def generate(
        self,
        prompt: str,
        capability: str = "chat",
        attachments: Optional[List[Any]] = None,
        **kwargs,
    ) -> Dict[str, Any]:
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            raise Exception("GROQ_API_KEY missing")

        cleaned_key = api_key.strip().strip("\"'")
        headers = {
            "Authorization": f"Bearer {cleaned_key}",
            "Content-Type": "application/json",
        }

        is_structured = kwargs.pop("is_structured", False)
        retry_attempt = kwargs.pop("retry_attempt", 0)

        workload = self._classify_workload(
            prompt,
            capability=capability,
            attachments=attachments,
            is_structured=is_structured,
        )
        candidates = self._get_model_candidates(workload, retry_attempt=retry_attempt)
        last_exc: Optional[Exception] = None

        for attempt_idx, model_id in enumerate(candidates):
            self.last_model_id = model_id
            self.last_retry_after_seconds = None
            messages = self._build_messages(
                prompt,
                attachments=attachments,
                model_id=model_id,
            )

            if attempt_idx == 0:
                logger.info("[GroqProvider] Workload=%s selected model=%s", workload, model_id)
            else:
                logger.warning(
                    "[GroqProvider] Model %s failed; trying fallback=%s",
                    candidates[attempt_idx - 1],
                    model_id,
                )

            self._log_payload_diagnostics(model_id, messages, stream=False)

            payload = {
                "model": model_id,
                "messages": messages,
                "stream": False,
                **kwargs,
            }

            url = "https://api.groq.com/openai/v1/chat/completions"

            try:
                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        url,
                        headers=headers,
                        json=payload,
                        timeout=60.0,
                    )

                    if response.status_code == 429:
                        if attempt_idx + 1 < len(candidates):
                            logger.warning(
                                "[GroqProvider] Model rate limited model=%s; trying fallback=%s",
                                model_id,
                                candidates[attempt_idx + 1],
                            )
                            last_exc = Exception(response.text)
                            continue
                        logger.warning("[GroqProvider] Groq model fallback exhausted; returning control to CapabilityRouter")
                        self._raise_rate_limit(model_id, Exception(response.text))

                    if response.status_code == 413:
                        raise ProviderPayloadTooLargeError(
                            f"Groq rejected request for {model_id}: 413 Payload Too Large",
                            self.last_prompt_chars or len(prompt),
                            self.last_compacted_prompt_chars or len(prompt),
                            model_id,
                        )

                    if response.status_code in (403, 404):
                        if attempt_idx + 1 < len(candidates):
                            logger.warning(
                                "[GroqProvider] Model error (%s) for %s; trying fallback=%s",
                                response.status_code,
                                model_id,
                                candidates[attempt_idx + 1],
                            )
                            last_exc = Exception(f"HTTP {response.status_code}: {response.text}")
                            continue
                        logger.warning("[GroqProvider] Groq model fallback exhausted; returning control to CapabilityRouter")
                        response.raise_for_status()

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
                logger.error("[GroqProvider] Generation failed on %s: %s", model_id, exc)

                if self._is_payload_too_large_error(exc):
                    raise ProviderPayloadTooLargeError(
                        f"Groq rejected request for {model_id}: 413 Payload Too Large",
                        self.last_prompt_chars or len(prompt),
                        self.last_compacted_prompt_chars or len(prompt),
                        model_id,
                    ) from exc

                if (self._is_rate_limit_error(exc) or self._is_model_permission_error(exc)) and (attempt_idx + 1 < len(candidates)):
                    continue

                if attempt_idx + 1 >= len(candidates):
                    logger.warning("[GroqProvider] Groq model fallback exhausted; returning control to CapabilityRouter")
                    if self._is_rate_limit_error(exc):
                        self._raise_rate_limit(model_id, exc)
                    raise Exception("ProviderException") from exc

        if last_exc:
            raise last_exc
        raise Exception("ProviderException")

    async def generate_stream(
        self,
        prompt: str,
        capability: str = "chat",
        attachments: Optional[List[Any]] = None,
        **kwargs,
    ) -> AsyncGenerator[str, None]:
        api_key = os.getenv("GROQ_API_KEY")
        if not api_key:
            raise Exception("GROQ_API_KEY missing")

        cleaned_key = api_key.strip().strip("\"'")
        headers = {
            "Authorization": f"Bearer {cleaned_key}",
            "Content-Type": "application/json",
        }

        is_structured = kwargs.pop("is_structured", False)
        retry_attempt = kwargs.pop("retry_attempt", 0)

        workload = self._classify_workload(
            prompt,
            capability=capability,
            attachments=attachments,
            is_structured=is_structured,
        )
        candidates = self._get_model_candidates(workload, retry_attempt=retry_attempt)
        for attempt_idx, model_id in enumerate(candidates):
            self.last_model_id = model_id
            self.last_retry_after_seconds = None
            messages = self._build_messages(
                prompt,
                attachments=attachments,
                model_id=model_id,
            )

            if attempt_idx == 0:
                logger.info("[GroqProvider] Workload=%s selected model=%s", workload, model_id)
            else:
                logger.warning(
                    "[GroqProvider] Model %s failed; trying fallback=%s",
                    candidates[attempt_idx - 1],
                    model_id,
                )

            self._log_payload_diagnostics(model_id, messages, stream=True)

            payload = {
                "model": model_id,
                "messages": messages,
                "stream": True,
                **kwargs,
            }

            url = "https://api.groq.com/openai/v1/chat/completions"

            try:
                async with httpx.AsyncClient() as client:
                    async with client.stream(
                        "POST",
                        url,
                        headers=headers,
                        json=payload,
                        timeout=60.0,
                    ) as response:
                        if response.status_code == 429:
                            body = await response.aread()
                            body_text = body.decode("utf-8", errors="ignore")
                            if attempt_idx + 1 < len(candidates):
                                logger.warning(
                                    "[GroqProvider] Model rate limited model=%s; trying fallback=%s",
                                    model_id,
                                    candidates[attempt_idx + 1],
                                )
                                continue
                            logger.warning("[GroqProvider] Groq model fallback exhausted; returning control to CapabilityRouter")
                            self._raise_rate_limit(
                                model_id,
                                Exception(body_text),
                            )

                        if response.status_code in (403, 404):
                            if attempt_idx + 1 < len(candidates):
                                logger.warning(
                                    "[GroqProvider] Model error (%s) for %s; trying fallback=%s",
                                    response.status_code,
                                    model_id,
                                    candidates[attempt_idx + 1],
                                )
                                continue
                            logger.warning("[GroqProvider] Groq model fallback exhausted; returning control to CapabilityRouter")
                            response.raise_for_status()

                        if response.status_code == 413:
                            body = await response.aread()
                            body_text = body.decode("utf-8", errors="ignore")
                            raise ProviderPayloadTooLargeError(
                                f"Groq rejected request for {model_id}: "
                                f"413 Payload Too Large. {body_text[:500]}",
                                self.last_prompt_chars or len(prompt),
                                self.last_compacted_prompt_chars or len(prompt),
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

                            delta = data_json["choices"][0].get("delta", {})
                            content = delta.get("content", "")
                            if content:
                                yield content
                        return

            except (ProviderRateLimitError, ProviderPayloadTooLargeError):
                raise

            except Exception as exc:
                logger.error("[GroqProvider] Streaming failed on %s: %s", model_id, exc)

                if self._is_payload_too_large_error(exc):
                    raise ProviderPayloadTooLargeError(
                        f"Groq rejected request for {model_id}: 413 Payload Too Large",
                        self.last_prompt_chars or len(prompt),
                        self.last_compacted_prompt_chars or len(prompt),
                        model_id,
                    ) from exc

                if (self._is_rate_limit_error(exc) or self._is_model_permission_error(exc)) and (attempt_idx + 1 < len(candidates)):
                    continue

                if attempt_idx + 1 >= len(candidates):
                    logger.warning("[GroqProvider] Groq model fallback exhausted; returning control to CapabilityRouter")
                    if self._is_rate_limit_error(exc):
                        self._raise_rate_limit(model_id, exc)
                    raise
