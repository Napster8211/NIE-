from typing import Optional, Dict, Any, List
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import delete
from app.models.image import ImageRecord

class ImageRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create(
        self, 
        filename: str, 
        file_path: str, 
        mime_type: str, 
        file_size: int, 
        source: str = "UPLOADED", 
        prompt: Optional[str] = None
    ) -> ImageRecord:
        
        db_obj = ImageRecord(
            filename=filename,
            file_path=file_path,
            mime_type=mime_type,
            file_size_bytes=file_size,
            source=source,
            prompt_used=prompt,
            status="COMPLETED" if source == "UPLOADED" else "GENERATING"
        )
        
        self.session.add(db_obj)
        await self.session.commit()
        await self.session.refresh(db_obj)
        return db_obj

    async def get(self, id: str) -> Optional[ImageRecord]:
        result = await self.session.execute(select(ImageRecord).where(ImageRecord.id == id))
        return result.scalars().first()

    async def list(self, skip: int = 0, limit: int = 100) -> List[ImageRecord]:
        result = await self.session.execute(
            select(ImageRecord).order_by(ImageRecord.created_at.desc()).offset(skip).limit(limit)
        )
        return list(result.scalars().all())

    async def update_status_and_metadata(
        self, 
        id: str, 
        status: str, 
        metadata: Optional[Dict[str, Any]] = None
    ) -> Optional[ImageRecord]:
        
        db_obj = await self.get(id)
        if db_obj:
            db_obj.status = status
            
            if metadata:
                # Cast to a new dictionary so SQLAlchemy accurately detects the change to the JSON column
                current_meta = dict(db_obj.metadata_blob or {})
                current_meta.update(metadata)
                db_obj.metadata_blob = current_meta
                
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