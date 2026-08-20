from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import AsyncSession
from app.repositories.document_repository import DocumentRepository
# from app.router.engine import capability_router

class DocumentAIEngine:
    @staticmethod
    def _construct_context(chunks: list, max_chunks: int = 5) -> str:
        """
        Concatenates document chunks for the prompt. 
        Limits size to prevent token overflow before full Vector DB integration.
        """
        return "\n...\n".join(chunks[:max_chunks])

    @staticmethod
    async def stream_task(
        session: AsyncSession,
        document_id: str,
        task_type: str,
        user_query: str = None,
        required_capabilities: list = [],
        preferences: list = ["openrouter", "gemini", "ollama"]
    ) -> AsyncGenerator[str, None]:
        
        repo = DocumentRepository(session)
        doc = await repo.get(document_id)
        
        if not doc or doc.status != "COMPLETED":
            yield "Error: Document is not ready or does not exist."
            return

        context_text = DocumentAIEngine._construct_context(doc.chunks)
        
        # Build System Prompt based on Task
        if task_type == "summarize":
            prompt = f"Summarize the following document accurately and concisely:\n\n{context_text}"
        elif task_type == "analyze":
            prompt = f"Analyze the following document. Extract key themes, entities, and action items:\n\n{context_text}"
        elif task_type == "ask":
            prompt = f"Based on the following document context, answer the question.\n\nContext:\n{context_text}\n\nQuestion: {user_query}"
        else:
            prompt = user_query

        # Execute via existing CapabilityRouter
        # stream_generator = capability_router.route_skill_execution(prompt, required_capabilities, preferences)
        
        # Simulated generator for architectural drop-in
        async def mock_router_stream():
            yield f"[Streaming {task_type.upper()} result...]\n"
            yield "Processed via existing capability router."
            
        stream_generator = mock_router_stream()

        async for chunk in stream_generator:
            yield chunk