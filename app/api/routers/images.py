import os
import shutil
from fastapi import APIRouter, Depends, HTTPException, status, UploadFile, File
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List

from app.schemas.image import ImageResponse, ImageGenerateRequest, ImageAnalyzeRequest
from app.repositories.image_repository import ImageRepository
from app.services.image_engine import ImageAIEngine
from app.database import get_db_session as get_db

router = APIRouter(prefix="/images", tags=["Images"])
IMAGE_DIR = "storage/images"
os.makedirs(IMAGE_DIR, exist_ok=True)

@router.post("/upload", response_model=ImageResponse, status_code=status.HTTP_201_CREATED)
async def upload_image(file: UploadFile = File(...), db: AsyncSession = Depends(get_db)):
    if not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Invalid file type. Only images allowed.")

    file_path = os.path.join(IMAGE_DIR, file.filename)
    
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    
    file_size = os.path.getsize(file_path)
    
    repo = ImageRepository(db)
    # Create record using only supported repository parameters
    doc = await repo.create(
        filename=file.filename,
        file_path=file_path,
        mime_type=file.content_type,
        file_size=file_size,
        source="UPLOADED"
    )
    
    # Safely mark status as COMPLETED so analysis is unlocked
    if hasattr(repo, "update_status_and_metadata"):
        await repo.update_status_and_metadata(doc.id, status="COMPLETED", metadata={})
    elif hasattr(repo, "update_status"):
        await repo.update_status(doc.id, "COMPLETED")
    else:
        doc.status = "COMPLETED"
        await db.commit()
        await db.refresh(doc)

    return doc

@router.post("/generate")
async def generate_image(req: ImageGenerateRequest, db: AsyncSession = Depends(get_db)):
    repo = ImageRepository(db)
    
    img_record = await repo.create(
        filename=f"gen_{req.prompt[:10].replace(' ', '_')}.png",
        file_path="",
        mime_type="image/png",
        file_size=0,
        source="GENERATED",
        prompt=req.prompt
    )

    stream_generator = ImageAIEngine.stream_generation(
        session=db,
        image_id=img_record.id,
        prompt=req.prompt,
        resolution=req.resolution,
        required_capabilities=req.required_capabilities,
        preferences=req.preferences
    )
    
    return StreamingResponse(stream_generator, media_type="application/x-ndjson")

@router.post("/{image_id}/analyze")
async def analyze_image(image_id: str, req: ImageAnalyzeRequest, db: AsyncSession = Depends(get_db)):
    stream_generator = ImageAIEngine.stream_analysis(
        session=db,
        image_id=image_id,
        prompt=req.prompt,
        required_capabilities=req.required_capabilities,
        preferences=req.preferences
    )
    return StreamingResponse(stream_generator, media_type="text/event-stream")

@router.get("", response_model=List[ImageResponse])
async def list_images(skip: int = 0, limit: int = 100, db: AsyncSession = Depends(get_db)):
    repo = ImageRepository(db)
    return await repo.list(skip=skip, limit=limit)

@router.get("/{image_id}", response_model=ImageResponse)
async def get_image(image_id: str, db: AsyncSession = Depends(get_db)):
    repo = ImageRepository(db)
    img = await repo.get(image_id)
    if not img:
        raise HTTPException(status_code=404, detail="Image not found")
    return img

@router.delete("/{image_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_image(image_id: str, db: AsyncSession = Depends(get_db)):
    repo = ImageRepository(db)
    img = await repo.get(image_id)
    if not img:
        raise HTTPException(status_code=404, detail="Image not found")
    
    if img.file_path and os.path.exists(img.file_path):
        os.remove(img.file_path)
        
    await repo.delete(image_id)