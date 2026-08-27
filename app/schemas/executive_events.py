"""
NapsterTec AI - Executive Live Event Schemas
Module: app/schemas/executive_events.py
"""
from typing import Optional, Any, Dict
from pydantic import BaseModel, Field
from datetime import datetime, timezone

class ExecutiveLiveEvent(BaseModel):
    """
    A safe, read-only projection of an internal system event.
    Strictly prohibits the inclusion of reasoning traces, API keys, or raw provider prompts.
    """
    event_id: str = Field(..., description="Stable, unique event identifier for frontend deduplication.")
    event_type: str = Field(..., description="Categorical event type (e.g., MISSION_STARTED, DEPARTMENT_ACTIVE).")
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    severity: str = Field(default="INFO", description="Severity level: INFO, SUCCESS, WARNING, HIGH, CRITICAL.")
    
    # Entity Correlation
    entity_type: str = Field(..., description="The primary entity mutating (e.g., MISSION, AGENT, OBJECTIVE).")
    entity_id: str = Field(..., description="The ID of the mutating entity.")
    
    # Optional Hierarchy Context
    objective_id: Optional[str] = None
    mission_id: Optional[str] = None
    department_id: Optional[str] = None
    agent_id: Optional[str] = None
    
    # Presentation
    headline: str = Field(..., description="Short UI display title.")
    summary: str = Field(default="", description="Safe UI display summary.")
    
    # Safe Metadata Payload
    metadata_safe: Dict[str, Any] = Field(default_factory=dict, description="Safe key/value pairs for targeted UI updates.")