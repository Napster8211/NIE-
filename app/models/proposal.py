"""
NapsterTec AI - Proposal Intelligence Database Model
Module: app/models/proposal.py
"""
import uuid
from datetime import datetime, timezone
from sqlalchemy import Column, String, DateTime, JSON, Integer
from app.database import Base

def get_naive_utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)

class ProposalIntelligenceBlueprint(Base):
    __tablename__ = "business_proposals"

    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    lead_id = Column(String, index=True, nullable=False)
    version = Column(Integer, default=1, nullable=False)
    
    proposal_type = Column(String, nullable=False)
    report = Column(JSON, nullable=False) # Stores the ProposalArtifact
    
    created_at = Column(DateTime, default=get_naive_utc_now)