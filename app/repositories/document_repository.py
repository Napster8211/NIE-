from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import delete
from app.models.document import Document

class DocumentRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(self, filename: str, file_path: str, mime_type: str, file_size: int) -> Document:
        db_obj = Document(
            filename=filename,
            file_path=file_path,
            mime_type=mime_type,
            file_size_bytes=file_size,
            status="PENDING"
        )
        self.session.add(db_obj)
        await self.session.commit()
        await self.session.refresh(db_obj)
        return db_obj

    async def get(self, id: str) -> Document:
        result = await self.session.execute(select(Document).where(Document.id == id))
        return result.scalars().first()

    async def list(self, skip: int = 0, limit: int = 100) -> list[Document]:
        result = await self.session.execute(
            select(Document).order_by(Document.created_at.desc()).offset(skip).limit(limit)
        )
        return result.scalars().all()

    async def update_extraction(self, id: str, text: str, chunks: list, status: str = "COMPLETED") -> Document:
        db_obj = await self.get(id)
        if db_obj:
            db_obj.extracted_text = text
            db_obj.chunks = chunks
            db_obj.status = status
            await self.session.commit()
            await self.session.refresh(db_obj)
        return db_obj

    async def update_status(self, id: str, status: str) -> Document:
        db_obj = await self.get(id)
        if db_obj:
            db_obj.status = status
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