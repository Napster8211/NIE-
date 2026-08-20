from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import datetime

class ImageResponse(BaseModel):
    id: str
    filename: str
    source: str
    status: str
    mime_type: str
    file_size_bytes: int
    prompt_used: Optional[str] = None
    metadata_blob: Optional[Dict[str, Any]] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

class ImageGenerateRequest(BaseModel):
    prompt: str = Field(..., description="The textual prompt to generate the image")
    resolution: Optional[str] = "1024x1024"
    required_capabilities: Optional[List[str]] = ["image_generation"]
    preferences: Optional[List[str]] = ["openrouter", "ollama"]

class ImageAnalyzeRequest(BaseModel):
    prompt: str = Field(default="Analyze this image and describe its contents.", description="Instructions for the Vision model (e.g., OCR extraction, description)")
    required_capabilities: Optional[List[str]] = ["vision"]
    preferences: Optional[List[str]] = ["openrouter", "gemini", "ollama"]