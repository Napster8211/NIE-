"""
NapsterTec AI - Lead Database Model
Module: app/models/lead.py
"""
import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, DateTime, JSON
from app.database import Base

def get_naive_utc_now():
    """Generates a UTC timestamp without timezone metadata to satisfy PostgreSQL."""
    return datetime.now(timezone.utc).replace(tzinfo=None)

class Lead(Base):
    __tablename__ = "leads"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    
    # Core lookup indices (extracted for faster deduplication queries)
    business_name = Column(String, index=True, nullable=False)
    place_id = Column(String, index=True, nullable=True)
    website_domain = Column(String, index=True, nullable=True)
    
    # JSON payloads matching the canonical schema
    business = Column(JSON, nullable=False)
    location = Column(JSON, nullable=False)
    contact = Column(JSON, nullable=False)
    source = Column(JSON, nullable=False)
    reputation = Column(JSON, nullable=False)
    qualification = Column(JSON, nullable=False)
    metadata_blob = Column(JSON, nullable=False)

    created_at = Column(DateTime, default=get_naive_utc_now)
    updated_at = Column(DateTime, default=get_naive_utc_now, onupdate=get_naive_utc_now)