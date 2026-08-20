from sqlalchemy import Column, String, Integer, DateTime, Text, JSON
from sqlalchemy.sql import func
import uuid

# Corrected Base import to match your database.py configuration
from app.database import Base

def generate_uuid():
    return str(uuid.uuid4())

class Document(Base):
    __tablename__ = "documents"

    id = Column(String, primary_key=True, default=generate_uuid, index=True)
    filename = Column(String, nullable=False)
    file_path = Column(String, nullable=False)
    mime_type = Column(String, nullable=False)
    file_size_bytes = Column(Integer, default=0)
    
    # State tracking: PENDING, EXTRACTING, COMPLETED, FAILED
    status = Column(String, default="PENDING") 
    
    extracted_text = Column(Text, nullable=True)
    chunks = Column(JSON, default=[]) # Stores text chunks for context window management
    metadata_blob = Column(JSON, default={})
    
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())