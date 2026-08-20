"""
NapsterTec AI - Engineering Review Database Model
Module: app/models/review.py
"""
import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, DateTime, JSON, Integer
from app.database import Base

def get_naive_utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)

class EngineeringReviewBlueprint(Base):
    __tablename__ = "engineering_reviews"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    lead_id = Column(String, index=True, nullable=False)
    version = Column(Integer, default=1, nullable=False)
    
    approval_status = Column(String, nullable=False)
    report = Column(JSON, nullable=False) # Stores the ReviewArtifact
    
    created_at = Column(DateTime, default=get_naive_utc_now)