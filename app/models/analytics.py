from sqlalchemy import Column, String, Integer, Float, Boolean, DateTime, JSON
from sqlalchemy.sql import func
from app.db.base_class import Base
import uuid

def generate_uuid():
    return str(uuid.uuid4())

class AnalyticsLog(Base):
    """Represents the existing monitoring event stream from the CapabilityRouter."""
    __tablename__ = "analytics_logs"

    id = Column(String, primary_key=True, default=generate_uuid, index=True)
    timestamp = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    
    # Execution Details
    provider_used = Column(String, index=True) # e.g., 'openrouter', 'gemini'
    skill_used = Column(String, index=True) # e.g., 'text_generation', 'vision'
    endpoint = Column(String)
    
    # Performance Metrics
    latency_ms = Column(Float, default=0.0)
    is_streaming = Column(Boolean, default=True)
    tokens_used = Column(Integer, default=0)
    
    # Resilience & Fallback Tracking
    status_code = Column(Integer, default=200)
    is_fallback = Column(Boolean, default=False)
    fallback_from = Column(String, nullable=True) # Provider that failed (e.g., 503 from openrouter)
    error_message = Column(String, nullable=True)

    metadata_blob = Column(JSON, default={})