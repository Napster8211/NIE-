from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


# --- Message Schemas ---
class MessageBase(BaseModel):
    role: str
    content: str
    tokens_used: int = 0
    status: str = "COMPLETED"
    correlation_id: str | None = None
    message_metadata: dict[str, Any] = Field(default_factory=dict, alias="metadata")


class MessageCreate(MessageBase):
    pass


class MessageResponse(MessageBase):
    id: str
    conversation_id: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


# --- Conversation Schemas ---
class ConversationBase(BaseModel):
    title: str | None = "New Chat"


class ConversationCreate(ConversationBase):
    model_config = ConfigDict(extra="forbid")


class ConversationResponse(ConversationBase):
    id: str
    user_id: str
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ConversationDetailResponse(ConversationResponse):
    messages: list[MessageResponse] = Field(default_factory=list)
