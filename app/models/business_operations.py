"""
NapsterTec AI - Business Operations Intelligence Database Model
Module: app/models/business_operations.py
"""
import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, DateTime, JSON, Integer
from app.database import Base

def get_naive_utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)

class BusinessOperationsBlueprint(Base):
    __tablename__ = "business_operations_evaluations"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    lead_id = Column(String, index=True, nullable=False) # Usually 'internal_napstertec'
    version = Column(Integer, default=1, nullable=False)
    
    report = Column(JSON, nullable=False) # Stores the BusinessOperationsArtifact
    
    created_at = Column(DateTime, default=get_naive_utc_now)