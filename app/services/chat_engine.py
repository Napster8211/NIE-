from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import AsyncSession
from app.repositories.chat_repository import MessageRepository, ConversationRepository
from app.schemas.chat import MessageCreate
# Assuming your existing router is importable here:
# from app.router.engine import capability_router

class MemoryEngineInterface:
    """Stub for future Vector DB / Long-term memory integration."""
    @staticmethod
    async def process_context(conversation_id: str, current_prompt: str):
        # Future implementation for RAG / semantic memory retrieval
        pass

class ConversationService:
    @staticmethod
    async def stream_and_persist(
        session: AsyncSession,
        conversation_id: str,
        user_prompt: str,
        context_messages: list,
        required_capabilities: list,
        preferences: list
    ) -> AsyncGenerator[str, None]:
        
        msg_repo = MessageRepository(session)
        
        # 1. Persist User Message Immediately
        await msg_repo.create(
            conversation_id=conversation_id, 
            obj_in=MessageCreate(role="user", content=user_prompt)
        )

        # 2. Future Memory Hook
        await MemoryEngineInterface.process_context(conversation_id, user_prompt)

        # 3. Format context for your existing Skill Engine
        # (Assuming your capability_router accepts a history array or string block)
        formatted_history = "\n".join([f"{m.role}: {m.content}" for m in context_messages])
        full_prompt = f"System History:\n{formatted_history}\n\nUser: {user_prompt}" if formatted_history else user_prompt

        # 4. Stream Execution & Capture
        assistant_full_response = ""
        
        try:
            # THIS CALLS YOUR EXISTING ROUTER (Do NOT rewrite existing routing)
            # stream_generator = capability_router.route_skill_execution(full_prompt, required_capabilities, preferences)
            
            # Simulated existing router yield for architectural completeness
            async def mock_router_stream():
                yield "This is a "
                yield "streamed response."
            stream_generator = mock_router_stream()

            async for chunk in stream_generator:
                assistant_full_response += chunk
                yield chunk
                
        finally:
            # 5. Persist final assistant message regardless of stream completion/abort
            if assistant_full_response:
                await msg_repo.create(
                    conversation_id=conversation_id,
                    obj_in=MessageCreate(role="assistant", content=assistant_full_response)
                )