"""
NapsterTec AI - Owner Control Typed Models
Module: app/schemas/owner_controls.py
"""
from pydantic import BaseModel, Field, ConfigDict
from typing import Optional
from datetime import datetime, timezone

class OwnerApprovalDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="ignore") # Forges are safely dropped
    decision: str = Field(..., description="APPROVE or REJECT")
    reason: str = Field(..., description="Reason for the decision")
    expected_version: Optional[int] = Field(None, description="Optimistic concurrency version")

class OwnerObjectiveControlRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    reason: str = Field(..., description="Reason for pause/resume/cancel")

class OwnerMissionControlRequest(BaseModel):
    model_config = ConfigDict(extra="ignore")
    reason: str = Field(..., description="Reason for pause/resume/cancel")

class OwnerControlActionRecord(BaseModel):
    action_id: str
    actor_id: str
    action_type: str
    target_type: str
    target_id: str
    previous_state: str
    new_state: str
    reason: str
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    success: bool
    error_code: Optional[str] = None