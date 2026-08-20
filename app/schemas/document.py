from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import datetime

class DocumentResponse(BaseModel):
    id: str
    filename: str
    mime_type: str
    file_size_bytes: int
    status: str
    metadata_blob: Optional[Dict[str, Any]] = Field(default_factory=dict)
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True

class DocumentQuestionRequest(BaseModel):
    question: str = Field(..., description="The query to ask against the document context")
    required_capabilities: Optional[List[str]] = []
    preferences: Optional[List[str]] = ["openrouter", "gemini", "ollama"]

class DocumentTaskRequest(BaseModel):
    required_capabilities: Optional[List[str]] = []
    preferences: Optional[List[str]] = ["openrouter", "gemini", "ollama"]