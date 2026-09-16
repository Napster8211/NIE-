import os

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session
from app.models.memory_models import Conversation, Message
from app.schemas.memory_schemas import (
    ConversationCreate,
    ConversationResponse,
    ConversationUpdate,
    MessageCreate,
    MessageResponse,
)
from app.services.director_auth_service import (
    DirectorAuthError,
    validate_trusted_origin,
    verify_firebase_identity,
)

router = APIRouter(prefix="/api/v1/memory", tags=["Memory"])
_optional_bearer = HTTPBearer(auto_error=False)


def _uid_set(name: str) -> set[str]:
    return {item.strip() for item in os.getenv(name, "").split(",") if item.strip()}


def memory_owner_ids(owner_id: str) -> tuple[str, ...]:
    """Return the verified owner's bounded conversation ownership scope.

    Before browser identity existed, Standard Chat stored the single user's
    conversations under ``local_user``.  Only a uniquely configured NIE owner
    may access that legacy namespace; ordinary Firebase users remain isolated
    to their own server-derived owner ID.
    """
    if not owner_id.startswith("firebase:"):
        return (owner_id,)
    uid = owner_id.removeprefix("firebase:")
    owner_uids = _uid_set("NIE_OWNER_FIREBASE_UIDS")
    if len(owner_uids) == 1 and uid in owner_uids:
        return (owner_id, "local_user")
    return (owner_id,)


async def resolve_memory_owner(
    request: Request,
    credentials: HTTPAuthorizationCredentials | None = Depends(_optional_bearer),
) -> str:
    """Resolve Standard Chat ownership only from a verified Firebase identity."""
    if credentials is None:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="CHAT_AUTH_REQUIRED")
    try:
        validate_trusted_origin(request)
        identity = await verify_firebase_identity(credentials.credentials, error_prefix="CHAT")
    except DirectorAuthError as error:
        raise HTTPException(status_code=error.http_status, detail=error.code) from error
    return f"firebase:{identity.uid}"


@router.post("/conversations", response_model=ConversationResponse)
async def create_conversation(
    conv: ConversationCreate,
    db: AsyncSession = Depends(get_db_session),
    owner_id: str = Depends(resolve_memory_owner),
):
    new_conv = Conversation(title=conv.title, user_id=owner_id)
    db.add(new_conv)
    await db.commit()
    await db.refresh(new_conv)
    return new_conv


@router.get("/conversations", response_model=list[ConversationResponse])
async def list_conversations(
    db: AsyncSession = Depends(get_db_session),
    owner_id: str = Depends(resolve_memory_owner),
):
    result = await db.execute(
        select(Conversation)
        .where(Conversation.user_id.in_(memory_owner_ids(owner_id)))
        .order_by(Conversation.updated_at.desc())
    )
    return result.scalars().all()


@router.put("/conversations/{conversation_id}", response_model=ConversationResponse)
async def update_conversation(
    conversation_id: str,
    update: ConversationUpdate,
    db: AsyncSession = Depends(get_db_session),
    owner_id: str = Depends(resolve_memory_owner),
):
    result = await db.execute(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.user_id.in_(memory_owner_ids(owner_id)),
        )
    )
    conversation = result.scalars().first()
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")

    conversation.title = update.title
    await db.commit()
    await db.refresh(conversation)
    return conversation


@router.post("/conversations/{conversation_id}/messages", response_model=MessageResponse)
async def add_message(
    conversation_id: str,
    message: MessageCreate,
    db: AsyncSession = Depends(get_db_session),
    owner_id: str = Depends(resolve_memory_owner),
):
    # A caller may mutate only a conversation owned by its verified identity.
    result = await db.execute(
        select(Conversation).where(
            Conversation.id == conversation_id,
            Conversation.user_id.in_(memory_owner_ids(owner_id)),
        )
    )
    conversation = result.scalars().first()

    if not conversation:
        raise HTTPException(status_code=404, detail="Conversation not found")

    new_message = Message(
        conversation_id=conversation_id,
        role=message.role,
        content=message.content,
        tokens_used=message.tokens_used,
        status=message.status,
        correlation_id=message.correlation_id,
        message_metadata=message.message_metadata,
    )
    db.add(new_message)

    # Update conversation timestamp to bump it up in the sidebar
    from datetime import datetime, timezone

    conversation.updated_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(new_message)
    return new_message


@router.get("/conversations/{conversation_id}/messages", response_model=list[MessageResponse])
async def get_messages(
    conversation_id: str,
    limit: int = 50,
    db: AsyncSession = Depends(get_db_session),
    owner_id: str = Depends(resolve_memory_owner),
):
    conversation = await db.execute(
        select(Conversation.id).where(
            Conversation.id == conversation_id,
            Conversation.user_id.in_(memory_owner_ids(owner_id)),
        )
    )
    if conversation.scalar_one_or_none() is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")
    result = await db.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.asc())
        .limit(limit)
    )
    return result.scalars().all()
