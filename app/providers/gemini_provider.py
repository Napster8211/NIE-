import os
import logging
import base64
import httpx
from typing import AsyncGenerator, Union, List, Any, Tuple, Optional
from google import genai
from google.genai import types

from app.providers.base import BaseProviderPlugin
from app.schemas.completion import CompletionRequest, StandardResponse
from app.tools.tool_registry import tool_registry
from app.engine.models import Capability, ProviderHealth

# Enterprise standard: Auto-load .env file if present
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

logger = logging.getLogger(__name__)

class GeminiProvider(BaseProviderPlugin):
    def __init__(self):
        try:
            self.client = genai.Client()
            self.is_configured = True
        except Exception as e:
            self.client = None
            self.is_configured = False
            logger.warning(f"[GeminiProvider] Warning: {e}. Provider is disabled.")

    @property
    def name(self) -> str:
        """Name required by the CapabilityRouter."""
        return "gemini"

    @property
    def provider_name(self) -> str:
        """Alias for backwards compatibility with legacy schemas."""
        return self.name

    @property
    def supported_models(self) -> List[str]:
        return ["gemini-3.6-flash", "gemini-2.5-pro", "gemini-1.5-flash"]

    @property
    def supported_capabilities(self) -> List[Capability]:
        """Exposes capability tags to the router matching Gemini's feature set."""
        return [
            Capability.CHAT,
            Capability.CODING,
            Capability.VISION,
            Capability.RESEARCH,
            Capability.DOCUMENTS,
            Capability.SYSTEM_INSPECTION
        ]

    async def check_health(self) -> ProviderHealth:
        """Used by CapabilityRouter for real-time failover checks."""
        if self.is_configured and self.client is not None:
            return ProviderHealth.HEALTHY
        return ProviderHealth.UNHEALTHY

    async def health_check(self) -> bool:
        """Legacy health check wrapper returning boolean."""
        return (await self.check_health()) == ProviderHealth.HEALTHY

    def _extract_prompt(self, prompt_or_request: Union[str, CompletionRequest, dict]) -> str:
        """Helper to extract a clean string prompt from string or request payloads."""
        if isinstance(prompt_or_request, str):
            return prompt_or_request

        if hasattr(prompt_or_request, 'messages') and prompt_or_request.messages:
            last_message = prompt_or_request.messages[-1]
            if hasattr(last_message, 'content'):
                return last_message.content
            elif isinstance(last_message, dict):
                return last_message.get('content', str(last_message))

        if isinstance(prompt_or_request, dict):
            return prompt_or_request.get('prompt', str(prompt_or_request))

        return str(prompt_or_request)

    def _parse_system_instruction(self, prompt: str) -> Tuple[Union[str, None], str]:
        """
        Parses incoming prompt text for system instructions.
        Returns a tuple of (system_instruction, clean_user_prompt).
        """
        if "[System Instruction]" in prompt:
            parts = prompt.split("[System Instruction]", 1)[1].split("\n\n", 1)
            if len(parts) == 2:
                return parts[0].strip(), parts[1].strip()
        
        return None, prompt

    async def _process_attachments(self, attachments: Optional[List[Any]]) -> List[types.Part]:
        """Converts attachments (Base64 data, URLs) into Gemini Part objects."""
        parts = []
        if not attachments:
            return parts

        async with httpx.AsyncClient(timeout=15.0) as http_client:
            for att in attachments:
                url = getattr(att, "url", None) or (
                    att.get("url") if isinstance(att, dict) else None
                )
                if not url:
                    continue

                try:
                    # Handle Base64 Data URI
                    if url.startswith("data:"):
                        header, encoded = url.split(",", 1)
                        mime_type = header.split(";")[0].replace("data:", "")
                        data_bytes = base64.b64decode(encoded)
                        parts.append(types.Part.from_bytes(data=data_bytes, mime_type=mime_type))

                    # Handle HTTP/HTTPS Image URLs
                    elif url.startswith(("http://", "https://")):
                        resp = await http_client.get(url)
                        if resp.status_code == 200:
                            mime_type = resp.headers.get("content-type", "image/jpeg")
                            parts.append(types.Part.from_bytes(data=resp.content, mime_type=mime_type))
                        else:
                            logger.warning(f"[GeminiProvider] Failed to fetch image from URL: {url} ({resp.status_code})")

                except Exception as e:
                    logger.error(f"[GeminiProvider] Error processing attachment: {e}")

        return parts

    def _get_gemini_tools(self) -> list:
        """Converts ToolRegistry schemas into Gemini's required FunctionDeclaration format."""
        declarations = []
        for schema in tool_registry.get_all_schemas():
            declarations.append({
                "name": schema["name"],
                "description": schema["description"],
                "parameters": schema["parameters"]
            })

        if not declarations:
            return None

        return [{"function_declarations": declarations}]

    async def generate_completion(
        self, 
        request: Union[CompletionRequest, str], 
        model: str = "gemini-3.6-flash",
        attachments: Optional[List[Any]] = None,
        capability: str = "chat",
        **kwargs
    ) -> StandardResponse:
        if not self.is_configured:
            raise RuntimeError("Gemini API key is not configured.")

        raw_prompt = self._extract_prompt(request)
        system_text, user_prompt = self._parse_system_instruction(raw_prompt)

        # Prepare multimodal contents
        attachment_parts = await self._process_attachments(attachments)
        contents = [user_prompt] + attachment_parts

        # Load tools and configure the model
        gemini_tools = self._get_gemini_tools()
        
        config_kwargs = {}
        if gemini_tools:
            config_kwargs["tools"] = gemini_tools
        if system_text:
            config_kwargs["system_instruction"] = system_text
            
        config = types.GenerateContentConfig(**config_kwargs) if config_kwargs else None

        # Start a chat session
        chat = self.client.aio.chats.create(model=model, config=config)
        response = await chat.send_message(contents)

        # Intercept and process any Tool/Function calls Gemini requests
        while response.function_calls:
            for fn_call in response.function_calls:
                tool_name = fn_call.name
                tool_args = fn_call.args

                logger.info(f"[🤖 Agent Triggered Tool]: {tool_name} with args: {tool_args}")

                target_tool = tool_registry.get_tool(tool_name)
                if target_tool:
                    try:
                        tool_output = await target_tool.run(**tool_args)
                    except Exception as e:
                        tool_output = f"Error executing tool: {str(e)}"
                else:
                    tool_output = f"Error: Tool '{tool_name}' not found."

                logger.info(f"[⚙️ Tool Result]: {str(tool_output)[:100]}...")

                response = await chat.send_message(
                    types.Part.from_function_response(
                        name=tool_name,
                        response={"result": tool_output}
                    )
                )

        return StandardResponse(
            content=response.text,
            provider_used=self.name,
            model_used=model
        )

    async def generate_stream(
        self, 
        prompt_or_request: Union[str, CompletionRequest], 
        model: str = "gemini-3.6-flash",
        attachments: Optional[List[Any]] = None,
        capability: str = "chat",
        **kwargs
    ) -> AsyncGenerator[str, None]:
        """
        Streaming endpoint supporting multimodal inputs via Gemini SDK.
        """
        if not self.is_configured:
            # Force an exception instead of yielding a string so the router can failover
            raise RuntimeError("Gemini API key is not configured.")

        raw_prompt = self._extract_prompt(prompt_or_request)
        system_text, user_prompt = self._parse_system_instruction(raw_prompt)

        # Process attached images/files into Gemini parts
        attachment_parts = await self._process_attachments(attachments)
        contents = [user_prompt] + attachment_parts

        config = types.GenerateContentConfig(system_instruction=system_text) if system_text else None

        try:
            response_stream = await self.client.aio.models.generate_content_stream(
                model=model,
                contents=contents,
                config=config
            )

            async for chunk in response_stream:
                if chunk.text:
                    yield chunk.text
        except Exception as e:
            logger.error(f"[GeminiProvider] Streaming error: {e}")
            # FORCE EXCEPTION SO ROUTER CAN FAILOVER
            raise e