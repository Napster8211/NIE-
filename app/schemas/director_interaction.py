"""
NapsterTec AI - Canonical Director Interaction Schema
Module: app/schemas/director_interaction.py
"""
from typing import Optional, List, Dict, Any
from datetime import datetime, timezone
from pydantic import BaseModel, Field

class DirectorInteractionRequest(BaseModel):
    message: str = Field(..., description="The executive question or command.")
    conversation_id: Optional[str] = Field(default=None, description="Optional bounding context.")
    context_objective_id: Optional[str] = None
    context_mission_id: Optional[str] = None
    voice_response_requested: bool = Field(default=False)

class DirectorActionProposal(BaseModel):
    requires_owner_action: bool = Field(default=True)
    action_type: str = Field(..., description="E.g., approval_resolution, objective_pause, financial_override")
    resource_id: str
    summary: str
    warning: Optional[str] = None

class DirectorInteractionResponse(BaseModel):
    interaction_id: str
    conversation_id: str
    message: str = Field(..., description="The Director's safe narrative response.")
    status: str = Field(default="COMPLETED")
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    related_objective_id: Optional[str] = None
    related_mission_id: Optional[str] = None
    proposed_action: Optional[DirectorActionProposal] = None
    voice_available: bool = Field(default=True)
    speech_text: Optional[str] = None