from sqlalchemy import Column, String, Integer, DateTime, Text, JSON
from sqlalchemy.sql import func
from app.db.base_class import Base
import uuid

def generate_uuid():
    return str(uuid.uuid4())

class ImageRecord(Base):
    __tablename__ = "images"

    id = Column(String, primary_key=True, default=generate_uuid, index=True)
    filename = Column(String, nullable=False)
    file_path = Column(String, nullable=False)
    mime_type = Column(String, nullable=False)
    file_size_bytes = Column(Integer, default=0)
    
    # Source type: UPLOADED, GENERATED, EDITED, VIDEO_FRAME
    source = Column(String, default="UPLOADED") 
    status = Column(String, default="COMPLETED") # PENDING, GENERATING, COMPLETED, FAILED
    
    # Stores the prompt used for generation or OCR extraction results
    prompt_used = Column(Text, nullable=True) 
    metadata_blob = Column(JSON, default={}) # Resolution, model, analysis results
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())