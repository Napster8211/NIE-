import os
import json
import logging
import httpx
from typing import AsyncGenerator, Dict, Any, List, Optional, Union

from app.providers.base import BaseProviderPlugin
from app.engine.models import ProviderHealth, Capability
from app.schemas.completion import CompletionRequest, StandardResponse

logger = logging.getLogger(__name__)


class OllamaFreeProvider(BaseProviderPlugin):
    """
    Production-ready local Ollama provider plugin.
    Connects directly to the local Ollama daemon at http://127.0.0.1:11434.
    """
    def __init__(self):
        raw_host = os.getenv("OLLAMA_HOST", "http://127.0.0.1:11434")
        if "localhost" in raw_host:
            raw_host = raw_host.replace("localhost", "127.0.0.1")
        self.host = raw_host.rstrip("/")
        
        self.default_model = os.getenv("OLLAMA_MODEL", "llama3.2")
        self.last_model_id: Optional[str] = self.default_model
        self._candidate_models = ["llama3.2", "llama3.2:latest", "llama3", "llama3:latest", "bakllava:latest"]

    @property
    def name(self) -> str:
        return "ollama"

    @property
    def provider_name(self) -> str:
        return self.name

    @property
    def supported_models(self) -> List[str]:
        return self._candidate_models

    @property
    def supported_capabilities(self) -> List[Capability]:
        return [
            Capability.CHAT,
            Capability.RESEARCH,
            Capability.SEARCH,
            Capability.VISION,
            Capability.CODE,
            Capability.CODING,
            Capability.DOCUMENTS,
            Capability.SYSTEM_INSPECTION,
        ]

    async def _resolve_active_model(self, http_client: httpx.AsyncClient) -> str:
        try:
            response = await http_client.get(f"{self.host}/api/tags", timeout=5.0)
            if response.status_code == 200:
                models_data = response.json().get("models", [])
                available_names = [m.get("name", "") for m in models_data]

                if self.default_model in available_names:
                    return self.default_model

                for candidate in self._candidate_models:
                    if candidate in available_names:
                        return candidate
                    for installed in available_names:
                        if installed.startswith(candidate) or candidate in installed:
                            return installed

                if available_names:
                    return available_names[0]
        except Exception as e:
            logger.warning(f"[OllamaFreeProvider] Could not resolve installed models: {e}")

        return self.default_model

    async def check_health(self) -> ProviderHealth:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(5.0, connect=3.0)) as client:
                response = await client.get(f"{self.host}/api/tags")
                if response.status_code == 200:
                    models_data = response.json().get("models", [])
                    if len(models_data) > 0:
                        return ProviderHealth.HEALTHY
                    else:
                        return ProviderHealth.UNHEALTHY
                return ProviderHealth.UNHEALTHY
        except Exception as e:
            return ProviderHealth.UNHEALTHY

    async def health_check(self) -> bool:
        return (await self.check_health()) == ProviderHealth.HEALTHY

    def _extract_prompt(self, prompt_or_request: Union[str, CompletionRequest, dict]) -> str:
        if isinstance(prompt_or_request, str):
            return prompt_or_request

        if hasattr(prompt_or_request, "messages") and prompt_or_request.messages:
            last_message = prompt_or_request.messages[-1]
            if hasattr(last_message, "content"):
                return last_message.content
            elif isinstance(last_message, dict):
                return last_message.get("content", str(last_message))

        if isinstance(prompt_or_request, dict):
            return prompt_or_request.get("prompt", str(prompt_or_request))

        return str(prompt_or_request)

    def _build_messages(self, prompt: str) -> List[Dict[str, str]]:
        system_text = "You are NapsterTec AI, an intelligent AI assistant powered by the NapsterTec Intelligence Engine (NIE)."
        user_text = prompt

        if "[System Instruction]" in prompt:
            parts = prompt.split("[System Instruction]", 1)[1].split("\n\n", 1)
            if len(parts) == 2:
                system_text, user_text = parts[0].strip(), parts[1].strip()

        return [
            {"role": "system", "content": system_text},
            {"role": "user", "content": user_text},
        ]

    async def generate(
        self,
        prompt: str,
        capability: str = "chat",
        attachments: Optional[List[Any]] = None,
        **kwargs
    ) -> Dict[str, Any]:
        prompt_text = self._extract_prompt(prompt)
        url = f"{self.host}/api/chat"

        # INFINITE TIMEOUT: Local models on CPU can take several minutes to process history
        async with httpx.AsyncClient(timeout=None) as client:
            model_to_use = await self._resolve_active_model(client)
            self.last_model_id = model_to_use

            payload = {
                "model": model_to_use,
                "messages": self._build_messages(prompt_text),
                "stream": False,
            }

            try:
                response = await client.post(url, json=payload)
                response.raise_for_status()
                data = response.json()

                content = data.get("message", {}).get("content", "")
                tokens = data.get("eval_count", 0)

                return {
                    "response": content,
                    "provider": self.name,
                    "model_used": model_to_use,
                    "tokens_used": tokens,
                }
            except Exception as e:
                err_detail = str(e) or repr(e) or type(e).__name__
                logger.error(f"[OllamaFreeProvider] Generation failed on {model_to_use}: {err_detail}")
                raise Exception(f"Ollama execution failed: {err_detail}") from e

    async def generate_completion(
        self,
        request: Union[CompletionRequest, str],
        model: Optional[str] = None
    ) -> StandardResponse:
        prompt_text = self._extract_prompt(request)
        result = await self.generate(prompt=prompt_text)
        return StandardResponse(
            content=result["response"],
            provider_used=self.name,
            model_used=result["model_used"],
            total_tokens_used=result["tokens_used"]
        )

    async def generate_stream(
        self,
        prompt: str,
        capability: str = "chat",
        attachments: Optional[List[Any]] = None,
        **kwargs
    ) -> AsyncGenerator[str, None]:
        prompt_text = self._extract_prompt(prompt)
        url = f"{self.host}/api/chat"

        # INFINITE TIMEOUT
        try:
            async with httpx.AsyncClient(timeout=None) as client:
                model_to_use = await self._resolve_active_model(client)
                self.last_model_id = model_to_use

                payload = {
                    "model": model_to_use,
                    "messages": self._build_messages(prompt_text),
                    "stream": True,
                }

                async with client.stream("POST", url, json=payload) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if line:
                            try:
                                chunk_data = json.loads(line)
                                content = chunk_data.get("message", {}).get("content", "")
                                if content:
                                    yield content
                            except json.JSONDecodeError:
                                continue
        except Exception as e:
            err_detail = str(e) or repr(e) or type(e).__name__
            logger.error(f"[OllamaFreeProvider] Streaming failed: {err_detail}")
            yield f"[Ollama Provider Error]: {err_detail}"