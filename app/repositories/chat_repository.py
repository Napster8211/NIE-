from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import delete, update
from app.models.chat import Conversation, Message
from app.schemas.chat import ConversationCreate, ConversationUpdate, MessageCreate

class ConversationRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, obj_in: ConversationCreate) -> Conversation:
        db_obj = Conversation(
            title=obj_in.title,
            metadata_blob=obj_in.metadata_blob
        )
        self.session.add(db_obj)
        await self.session.commit()
        await self.session.refresh(db_obj)
        return db_obj

    async def get(self, id: str) -> Conversation:
        result = await self.session.execute(
            select(Conversation).where(Conversation.id == id)
        )
        return result.scalars().first()

    async def list(self, skip: int = 0, limit: int = 100) -> list[Conversation]:
        result = await self.session.execute(
            select(Conversation).order_by(Conversation.updated_at.desc()).offset(skip).limit(limit)
        )
        return result.scalars().unique().all()

    async def update(self, id: str, obj_in: ConversationUpdate) -> Conversation:
        db_obj = await self.get(id)
        if not db_obj:
            return None
        
        update_data = obj_in.dict(exclude_unset=True)
        for field, value in update_data.items():
            setattr(db_obj, field, value)
            
        await self.session.commit()
        await self.session.refresh(db_obj)
        return db_obj

    async def delete(self, id: str) -> bool:
        db_obj = await self.get(id)
        if not db_obj:
            return False
        await self.session.delete(db_obj)
        await self.session.commit()
        return True

class MessageRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, conversation_id: str, obj_in: MessageCreate) -> Message:
        db_obj = Message(
            conversation_id=conversation_id,
            role=obj_in.role,
            content=obj_in.content,
            metadata_blob=obj_in.metadata_blob
        )
        self.session.add(db_obj)
        
        # Touch conversation updated_at
        await self.session.execute(
            update(Conversation).where(Conversation.id == conversation_id).values(updated_at=func.now())
        )
        
        await self.session.commit()
        await self.session.refresh(db_obj)
        return db_obj

    async def get_by_conversation(self, conversation_id: str) -> list[Message]:
        result = await self.session.execute(
            select(Message).where(Message.conversation_id == conversation_id).order_by(Message.created_at.asc())
        )
        return result.scalars().all()

    async def get_up_to_message(self, conversation_id: str, message_id: str) -> list[Message]:
        """Fetches all messages in a conversation up to and including a specific message."""
        all_msgs = await self.get_by_conversation(conversation_id)
        context = []
        for msg in all_msgs:
            context.append(msg)
            if msg.id == message_id:
                break
        return context

    async def delete(self, message_id: str) -> bool:
        result = await self.session.execute(delete(Message).where(Message.id == message_id))
        await self.session.commit()
        return result.rowcount > 0