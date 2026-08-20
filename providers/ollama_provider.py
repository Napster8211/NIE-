import json
import asyncio
import time
from typing import List, Dict, Any, AsyncGenerator, Optional
from concurrent.futures import ThreadPoolExecutor

from providers.base_provider import BaseProvider
from utils.config import settings

# Import the synchronous package
from ollamafreeapi import OllamaFreeAPI

class OllamaProvider(BaseProvider):
    def __init__(self, executor: ThreadPoolExecutor):
        self.client: Optional[OllamaFreeAPI] = None
        self.executor = executor

    async def initialize(self) -> None:
        """Initialize synchronous client."""
        self.client = OllamaFreeAPI()

    def _convert_messages_to_prompt(self, messages: List[Dict[str, Any]]) -> str:
        """
        Translates OpenAI message structures into a clean conversational context.
        Preserves system instructions, user prompts, and assistant replies.
        """
        formatted_blocks = []
        for msg in messages:
            role = msg.get("role", "user").capitalize()
            content = msg.get("content", "")
            if isinstance(content, list):
                # Handle structured/vision arrays if string content exists
                content = " ".join([c.get("text", "") for c in content if isinstance(c, dict) and "text" in c])
            formatted_blocks.append(f"### {role}:\n{content}")
        
        formatted_blocks.append("### Assistant:\n")
        return "\n\n".join(formatted_blocks)

    async def chat(self, messages: List[Dict[str, Any]], model: str, **kwargs) -> Dict[str, Any]:
        """Wrap synchronous chat in the shared thread pool."""
        prompt = self._convert_messages_to_prompt(messages)
        temperature = kwargs.get("temperature", 0.7)
        loop = asyncio.get_running_loop()

        # OVERRIDE: Give free servers plenty of time to warm up
        chat_timeout = 120.0

        try:
            # Run blocking operation in shared thread pool executor
            response_text = await asyncio.wait_for(
                loop.run_in_executor(
                    self.executor,
                    lambda: self.client.chat(model=model, prompt=prompt, temperature=temperature, timeout=chat_timeout)
                ),
                timeout=chat_timeout + 5.0 # Add small buffer over the client timeout
            )

            return {
                "id": f"chatcmpl-ollama-{int(time.time())}",
                "object": "chat.completion",
                "created": int(time.time()),
                "model": model,
                "choices": [{
                    "index": 0,
                    "message": {
                        "role": "assistant",
                        "content": response_text
                    },
                    "finish_reason": "stop"
                }]
            }
        except asyncio.TimeoutError:
            raise TimeoutError("OllamaFreeAPI provider request timed out.")
        except Exception as e:
            raise RuntimeError(f"OllamaFreeAPI execution error: {str(e)}")

    async def stream_chat(self, messages: List[Dict[str, Any]], model: str, **kwargs) -> AsyncGenerator[str, None]:
        """Wrap synchronous stream generator using an async queue and shared executor."""
        prompt = self._convert_messages_to_prompt(messages)
        queue = asyncio.Queue()
        loop = asyncio.get_running_loop()

        # OVERRIDE: Streaming requires high TTFT allowance for remote free servers
        stream_timeout = 120.0

        def _sync_stream_worker():
            try:
                # Pass the extended timeout directly to the client generator
                for chunk in self.client.stream_chat(prompt=prompt, model=model, timeout=stream_timeout):
                    loop.call_soon_threadsafe(queue.put_nowait, chunk)
                loop.call_soon_threadsafe(queue.put_nowait, None)  # Sentinel value (EOF)
            except Exception as ex:
                loop.call_soon_threadsafe(queue.put_nowait, ex)

        # Run background thread in shared executor
        loop.run_in_executor(self.executor, _sync_stream_worker)

        try:
            while True:
                # Disconnect from settings.REQUEST_TIMEOUT to prevent premature failure
                chunk = await asyncio.wait_for(queue.get(), timeout=stream_timeout)
                if chunk is None:
                    break
                if isinstance(chunk, Exception):
                    raise chunk

                sse_payload = {
                    "id": f"chatcmpl-stream-{int(time.time())}",
                    "object": "chat.completion.chunk",
                    "created": int(time.time()),
                    "model": model,
                    "choices": [{
                        "index": 0,
                        "delta": {"content": chunk},
                        "finish_reason": None
                    }]
                }
                yield f"data: {json.dumps(sse_payload)}\n\n"

            yield "data: [DONE]\n\n"
        except asyncio.TimeoutError:
            yield f"data: {json.dumps({'error': 'Stream timed out waiting for remote server'})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'error': str(e)})}\n\n"

    async def list_models(self) -> List[Dict[str, Any]]:
        """Wrap synchronous list_models."""
        loop = asyncio.get_running_loop()
        try:
            raw_models = await asyncio.wait_for(
                loop.run_in_executor(self.executor, self.client.list_models),
                timeout=10.0
            )
            parsed_models = []
            for item in (raw_models or []):
                model_id = item if isinstance(item, str) else item.get("name", str(item))
                parsed_models.append({
                    "id": model_id,
                    "object": "model",
                    "created": int(time.time()),
                    "owned_by": "ollamafreeapi"
                })
            return parsed_models
        except Exception as e:
            raise RuntimeError(f"Failed to retrieve models from OllamaFreeAPI: {str(e)}")

    async def health_check(self) -> Dict[str, Any]:
        """Health check via list_models execution."""
        loop = asyncio.get_running_loop()
        try:
            await asyncio.wait_for(
                loop.run_in_executor(self.executor, self.client.list_models),
                timeout=5.0
            )
            return {"status": "healthy"}
        except Exception:
            return {"status": "unhealthy"}