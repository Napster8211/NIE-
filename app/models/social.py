"""
NapsterTec AI - Social Intelligence Database Model
Module: app/models/social.py
"""
import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, DateTime, JSON, Integer
from app.database import Base

def get_naive_utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)

class SocialOperationsBlueprint(Base):
    __tablename__ = "social_operations"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    lead_id = Column(String, index=True, nullable=False) # 'internal_napstertec' for brand assets
    version = Column(Integer, default=1, nullable=False)
    
    report = Column(JSON, nullable=False) # Stores the SocialArtifact
    
    created_at = Column(DateTime, default=get_naive_utc_now)