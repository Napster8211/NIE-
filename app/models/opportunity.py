"""
NapsterTec AI - Opportunity Intelligence Database Model
Module: app/models/opportunity.py
"""
import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, DateTime, JSON, Integer
from app.database import Base

def get_naive_utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)

class OpportunityIntelligence(Base):
    __tablename__ = "opportunity_intelligence"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    lead_id = Column(String, index=True, nullable=False)
    version = Column(Integer, default=1, nullable=False)
    
    level = Column(String, nullable=False)
    report = Column(JSON, nullable=False) # Stores the OpportunityArtifact
    
    created_at = Column(DateTime, default=get_naive_utc_now)