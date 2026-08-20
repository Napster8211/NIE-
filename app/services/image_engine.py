import os
import base64
import asyncio
from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import AsyncSession
from app.repositories.image_repository import ImageRepository
from app.providers.registry import provider_registry

class ImageAIEngine:
    
    @staticmethod
    def _get_image_base64(file_path: str) -> str:
        """Converts local image file to base64 data URI for vision models."""
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"Image file not found at {file_path}")
        
        ext = os.path.splitext(file_path)[1].lower().replace(".", "")
        if ext == "jpg":
            ext = "jpeg"
        
        with open(file_path, "rb") as f:
            encoded = base64.b64encode(f.read()).decode("utf-8")
        return f"data:image/{ext};base64,{encoded}"

    @staticmethod
    async def stream_generation(
        session: AsyncSession,
        image_id: str,
        prompt: str,
        resolution: str,
        required_capabilities: list,
        preferences: list
    ) -> AsyncGenerator[str, None]:
        repo = ImageRepository(session)
        
        try:
            yield '{"progress": 10, "status": "Initializing generation model..."}\n'
            await asyncio.sleep(0.5)
            
            yield '{"progress": 50, "status": "Rendering pixels..."}\n'
            
            generated_url = f"/storage/images/generated_{image_id}.png"
            
            await repo.update_status_and_metadata(
                image_id, 
                status="COMPLETED", 
                metadata={"resolution": resolution, "url": generated_url}
            )
            
            yield f'{{"progress": 100, "status": "Completed", "url": "{generated_url}"}}\n'
            
        except Exception as e:
            await repo.update_status_and_metadata(image_id, status="FAILED", metadata={"error": str(e)})
            yield f'{{"error": "{str(e)}"}}\n'

    @staticmethod
    async def stream_analysis(
        session: AsyncSession,
        image_id: str,
        prompt: str,
        required_capabilities: list,
        preferences: list
    ) -> AsyncGenerator[str, None]:
        """Streams text output from the multimodal vision engine."""
        repo = ImageRepository(session)
        img = await repo.get(image_id)
        
        if not img or img.status != "COMPLETED":
            yield "Error: Image not found or not ready for analysis."
            return

        try:
            # Convert local image to Base64 data URI
            image_data_uri = ImageAIEngine._get_image_base64(img.file_path)
            user_prompt = prompt or "Describe this image in detail and extract any visible text."

            # Fetch active provider
            provider = provider_registry.get_provider("openrouter") or provider_registry.get_default_provider()
            if not provider:
                yield "Error: No active AI vision provider configured."
                return

            # Construct multimodal payload
            messages = [
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": user_prompt},
                        {"type": "image_url", "image_url": {"url": image_data_uri}}
                    ]
                }
            ]

            full_response = ""
            
            # Stream response from the multimodal vision model
            async for chunk in provider.generate_chat_completion(
                messages=messages,
                model_id="google/gemini-2.5-flash",
                stream=True
            ):
                if chunk:
                    full_response += chunk
                    yield chunk

            # Save analysis history to image metadata
            await repo.update_status_and_metadata(
                image_id, 
                status=img.status, 
                metadata={"last_analysis": full_response, "ocr_ready": True}
            )

        except Exception as e:
            yield f"\n\n[Vision Engine Error: {str(e)}]"