from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List

from app.schemas.chat import ConversationCreate, ConversationResponse, ConversationUpdate, MessageCreate, MessageResponse
from app.repositories.chat_repository import ConversationRepository, MessageRepository
from app.services.chat_engine import ConversationService
# from app.db.session import get_db

router = APIRouter(prefix="/conversations", tags=["Conversations"])

# Mock dependency for db session injection
async def get_db():
    yield None 

@router.post("", response_model=ConversationResponse, status_code=status.HTTP_201_CREATED)
async def create_conversation(conv_in: ConversationCreate, db: AsyncSession = Depends(get_db)):
    repo = ConversationRepository(db)
    return await repo.create(conv_in)

@router.get("", response_model=List[ConversationResponse])
async def list_conversations(skip: int = 0, limit: int = 100, db: AsyncSession = Depends(get_db)):
    repo = ConversationRepository(db)
    return await repo.list(skip=skip, limit=limit)

@router.get("/{conversation_id}", response_model=ConversationResponse)
async def get_conversation(conversation_id: str, db: AsyncSession = Depends(get_db)):
    repo = ConversationRepository(db)
    conv = await repo.get(conversation_id)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conv

@router.patch("/{conversation_id}", response_model=ConversationResponse)
async def update_conversation(conversation_id: str, conv_in: ConversationUpdate, db: AsyncSession = Depends(get_db)):
    repo = ConversationRepository(db)
    conv = await repo.update(conversation_id, conv_in)
    if not conv:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return conv

@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_conversation(conversation_id: str, db: AsyncSession = Depends(get_db)):
    repo = ConversationRepository(db)
    success = await repo.delete(conversation_id)
    if not success:
        raise HTTPException(status_code=404, detail="Conversation not found")

@router.get("/{conversation_id}/messages", response_model=List[MessageResponse])
async def get_messages(conversation_id: str, db: AsyncSession = Depends(get_db)):
    repo = MessageRepository(db)
    return await repo.get_by_conversation(conversation_id)

@router.post("/{conversation_id}/messages")
async def add_message_and_stream(
    conversation_id: str, 
    msg_in: MessageCreate, 
    required_capabilities: list = [], 
    preferences: list = ["openrouter", "gemini", "ollama"],
    db: AsyncSession = Depends(get_db)
):
    # Verify conversation exists
    conv_repo = ConversationRepository(db)
    if not await conv_repo.get(conversation_id):
        raise HTTPException(status_code=404, detail="Conversation not found")

    msg_repo = MessageRepository(db)
    context_messages = await msg_repo.get_by_conversation(conversation_id)

    stream_generator = ConversationService.stream_and_persist(
        session=db,
        conversation_id=conversation_id,
        user_prompt=msg_in.content,
        context_messages=context_messages,
        required_capabilities=required_capabilities,
        preferences=preferences
    )
    
    return StreamingResponse(stream_generator, media_type="text/event-stream")

@router.post("/{conversation_id}/messages/{message_id}/regenerate")
async def regenerate_message(
    conversation_id: str,
    message_id: str,
    required_capabilities: list = [],
    preferences: list = ["openrouter", "gemini", "ollama"],
    db: AsyncSession = Depends(get_db)
):
    """Regenerates the assistant response from a specific user message point."""
    msg_repo = MessageRepository(db)
    
    # 1. Fetch context up to the message we want to regenerate FROM
    context = await msg_repo.get_up_to_message(conversation_id, message_id)
    if not context:
        raise HTTPException(status_code=404, detail="Message context not found")
        
    target_message = context[-1]
    
    # 2. Extract the prompt we are regenerating against
    # If the target message is an assistant message, we regenerate based on the preceding user message
    if target_message.role == "assistant":
        user_prompt = context[-2].content if len(context) > 1 else ""
        context_to_send = context[:-2] # Send everything prior to that user message
    else:
        user_prompt = target_message.content
        context_to_send = context[:-1] # Send everything prior to the target message

    # 3. Stream and persist (the ConversationService automatically saves the new assistant message)
    stream_generator = ConversationService.stream_and_persist(
        session=db,
        conversation_id=conversation_id,
        user_prompt=user_prompt,
        context_messages=context_to_send,
        required_capabilities=required_capabilities,
        preferences=preferences
    )
    
    return StreamingResponse(stream_generator, media_type="text/event-stream")