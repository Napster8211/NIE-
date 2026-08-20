"""
NapsterTec AI - Marketing Analytics Database Model
Module: app/models/marketing_analytics.py
"""
import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, DateTime, JSON, Integer
from app.database import Base

def get_naive_utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)

class MarketingAnalyticsBlueprint(Base):
    __tablename__ = "marketing_analytics"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    lead_id = Column(String, index=True, nullable=False) # 'internal_napstertec' for brand analytics
    version = Column(Integer, default=1, nullable=False)
    
    report = Column(JSON, nullable=False) # Stores the MarketingAnalyticsArtifact
    
    created_at = Column(DateTime, default=get_naive_utc_now)