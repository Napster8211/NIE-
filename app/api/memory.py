from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from typing import List

from app.database import get_db_session
from app.models.memory_models import Conversation, Message
from app.schemas.memory_schemas import (
    ConversationCreate, 
    ConversationResponse, 
    MessageCreate, 
    MessageResponse
)

router = APIRouter(prefix="/api/v1/memory", tags=["Memory"])

@router.post("/conversations", response_model=ConversationResponse)
async def create_conversation(
    conv: ConversationCreate, 
    db: AsyncSession = Depends(get_db_session)
):
    new_conv = Conversation(title=conv.title, user_id=conv.user_id)
    db.add(new_conv)
    await db.commit()
    await db.refresh(new_conv)
    return new_conv

@router.get("/conversations", response_model=List[ConversationResponse])
async def list_conversations(
    user_id: str = "local_user",
    db: AsyncSession = Depends(get_db_session)
):
    result = await db.execute(
        select(Conversation).where(Conversation.user_id == user_id).order_by(Conversation.updated_at.desc())
    )
    return result.scalars().all()

@router.post("/conversations/{conversation_id}/messages", response_model=MessageResponse)
async def add_message(
    conversation_id: str, 
    message: MessageCreate, 
    db: AsyncSession = Depends(get_db_session)
):
    # Verify conversation exists
    result = await db.execute(select(Conversation).where(Conversation.id == conversation_id))
    conversation = result.scalars().first()
    
    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")
        
    new_message = Message(
        conversation_id=conversation_id,
        role=message.role,
        content=message.content,
        tokens_used=message.tokens_used
    )
    db.add(new_message)
    
    # Update conversation timestamp to bump it up in the sidebar
    from datetime import datetime, timezone
    conversation.updated_at = datetime.now(timezone.utc)
    
    await db.commit()
    await db.refresh(new_message)
    return new_message

@router.get("/conversations/{conversation_id}/messages", response_model=List[MessageResponse])
async def get_messages(
    conversation_id: str, 
    limit: int = 50,
    db: AsyncSession = Depends(get_db_session)
):
    result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.asc())
        .limit(limit)
    )
    return result.scalars().all()