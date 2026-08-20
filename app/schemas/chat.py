from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from datetime import datetime

class Attachment(BaseModel):
    id: Optional[str] = Field(None, description="Unique identifier for the file/attachment")
    url: Optional[str] = Field(None, description="Accessible URL or Base64 data string of the attachment")
    type: Optional[str] = Field(None, description="MIME type or category (e.g. image, document)")
    filename: Optional[str] = Field(None, description="Original filename")

class MessageBase(BaseModel):
    role: str = Field(..., description="Role of the message author: user, assistant, or system")
    content: str = Field(..., description="The actual message content")
    attachments: Optional[List[Attachment]] = Field(default_factory=list, description="List of image/file attachments")
    metadata_blob: Optional[Dict[str, Any]] = Field(default_factory=dict)

class MessageCreate(MessageBase):
    pass

class MessageResponse(MessageBase):
    id: str
    conversation_id: str
    created_at: datetime

    class Config:
        from_attributes = True

class ConversationBase(BaseModel):
    title: Optional[str] = "New Conversation"
    metadata_blob: Optional[Dict[str, Any]] = Field(default_factory=dict)

class ConversationCreate(ConversationBase):
    pass

class ConversationUpdate(BaseModel):
    title: Optional[str] = None
    metadata_blob: Optional[Dict[str, Any]] = None

class ConversationResponse(ConversationBase):
    id: str
    created_at: datetime
    updated_at: datetime
    messages: List[MessageResponse] = []

    class Config:
        from_attributes = True

# Request schema for POST /api/v1/chat endpoint
class ChatRequest(BaseModel):
    prompt: str = Field(..., description="User prompt text")
    stream: bool = Field(default=True, description="Whether to stream the LLM response")
    context: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Execution context (e.g., conversation_id)")
    attachments: Optional[List[Attachment]] = Field(default_factory=list, description="List of uploaded image attachments")