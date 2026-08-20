"""
NapsterTec AI - Sales Intelligence Database Model
Module: app/models/sales.py
"""
import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, DateTime, JSON, Integer
from app.database import Base

def get_naive_utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)

class SalesOperationsBlueprint(Base):
    __tablename__ = "sales_opportunities"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    lead_id = Column(String, index=True, nullable=False)
    version = Column(Integer, default=1, nullable=False)
    
    report = Column(JSON, nullable=False) # Stores the SalesArtifact
    
    created_at = Column(DateTime, default=get_naive_utc_now)