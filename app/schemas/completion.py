# app/schemas/completion.py
from pydantic import BaseModel, Field, ConfigDict
from typing import List, Optional, Dict, Any

class Message(BaseModel):
    role: str = Field(..., description="system, user, assistant, or tool")
    content: str

class CompletionRequest(BaseModel):
    messages: List[Message]
    temperature: float = Field(default=0.7, ge=0.0, le=2.0)
    max_tokens: int = Field(default=2048)
    stream: bool = Field(default=False)
    # Allows the Intelligent Router to pass provider-specific kwargs safely
    provider_kwargs: Optional[Dict[str, Any]] = Field(default_factory=dict)

    model_config = ConfigDict(extra="forbid")

class StandardResponse(BaseModel):
    content: str
    provider_used: str
    model_used: str
    total_tokens_used: int
    latency_ms: float