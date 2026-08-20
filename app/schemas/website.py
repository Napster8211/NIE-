"""
NapsterTec AI - Website Intelligence Artifact
Module: app/schemas/website.py
"""
from typing import Optional, List, Dict, Any
from pydantic import BaseModel, Field
from app.schemas.shared_artifacts import BaseArtifact, Evidence

class TechnologyFinding(BaseModel):
    name: str = Field(..., description="Name of the technology (e.g., React, Cloudflare).")
    confidence: float = Field(..., description="0.0 to 1.0 confidence.")
    evidence: Evidence

class StructuredRecommendation(BaseModel):
    category: str = Field(..., description="SEO, Performance, Security, etc.")
    priority: str = Field(..., description="High, Medium, Low")
    reason: str = Field(..., description="Why this recommendation is being made.")
    evidence: Evidence

class BusinessSignal(BaseModel):
    """Objective, boolean business capabilities to be consumed by Opportunity Intelligence."""
    name: str = Field(..., description="E.g., 'Online Booking Present', 'HTTPS Enabled'")
    present: bool
    evidence: Evidence

class WebsiteArtifact(BaseArtifact):
    artifact_type: str = "WebsiteArtifact"
    lead_id: str = Field(...)
    target_url: Optional[str] = None
    status: str = Field(default="unknown")
    
    http_info: Dict[str, Any] = Field(default_factory=dict)
    technology: List[TechnologyFinding] = Field(default_factory=list)
    performance: Dict[str, Any] = Field(default_factory=dict)
    seo: Dict[str, Any] = Field(default_factory=dict)
    accessibility: Dict[str, Any] = Field(default_factory=dict)
    security: Dict[str, Any] = Field(default_factory=dict)
    visual_analysis: Dict[str, Any] = Field(default_factory=dict)
    
    business_signals: List[BusinessSignal] = Field(default_factory=list)
    recommendations: List[StructuredRecommendation] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    
    execution_metadata: Dict[str, Any] = Field(default_factory=dict)