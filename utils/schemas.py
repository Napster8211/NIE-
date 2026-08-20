from pydantic import BaseModel, Field, validator
from typing import List, Optional, Dict, Any, Union

class ChatMessage(BaseModel):
    role: str = Field(..., description="Role of the message author (system, user, assistant)")
    content: Union[str, List[Any]] = Field(..., description="Content of the message")

    @validator('role')
    def validate_role(cls, v):
        allowed_roles = {'system', 'user', 'assistant', 'function', 'tool'}
        if v.lower() not in allowed_roles:
            raise ValueError(f"Role must be one of {allowed_roles}")
        return v.lower()

class ChatCompletionRequest(BaseModel):
    model: str = Field(..., description="Logical model identifier or target model name")
    messages: List[ChatMessage] = Field(..., min_items=1, description="List of structured messages")
    provider: Optional[str] = Field(None, description="Optional override provider target")
    temperature: Optional[float] = Field(0.7, ge=0.0, le=2.0)
    top_p: Optional[float] = Field(1.0, ge=0.0, le=1.0)
    max_tokens: Optional[int] = Field(None, gt=0)
    stream: Optional[bool] = False

    class Config:
        extra = "allow" # Preserves extra OpenAI optional parameters (e.g., presence_penalty)