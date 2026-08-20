from pydantic import BaseModel, ConfigDict
from typing import List, Optional
from datetime import datetime

# --- Message Schemas ---
class MessageBase(BaseModel):
    role: str
    content: str
    tokens_used: int = 0

class MessageCreate(MessageBase):
    pass

class MessageResponse(MessageBase):
    id: str
    conversation_id: str
    created_at: datetime
    
    model_config = ConfigDict(from_attributes=True)

# --- Conversation Schemas ---
class ConversationBase(BaseModel):
    title: Optional[str] = "New Chat"
    user_id: Optional[str] = "local_user"

class ConversationCreate(ConversationBase):
    pass

class ConversationResponse(ConversationBase):
    id: str
    created_at: datetime
    updated_at: datetime
    
    model_config = ConfigDict(from_attributes=True)

class ConversationDetailResponse(ConversationResponse):
    messages: List[MessageResponse] = []