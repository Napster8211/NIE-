from typing import Dict, Any, Optional, List
from enum import Enum
from datetime import datetime, timezone
from pydantic import BaseModel, Field

class StrategicPlanStatus(str, Enum):
    DRAFT = "DRAFT"
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
    READY = "READY"
    BLOCKED = "BLOCKED"
    SUPERSEDED = "SUPERSEDED"
    CANCELLED = "CANCELLED"

class StrategicWorkstream(BaseModel):
    workstream_id: str = Field(...)
    title: str = Field(...)
    purpose: str = Field(...)
    desired_outcome: str = Field(...)
    required_capabilities: List[str] = Field(default_factory=list)
    priority: str = Field(default="NORMAL")
    dependencies: List[str] = Field(default_factory=list)
    success_evidence: List[str] = Field(default_factory=list)
    constraints: List[str] = Field(default_factory=list)
    status: str = Field(default="PLANNED")

class DepartmentAssignment(BaseModel):
    agent_id: str = Field(...)
    role: str = Field(...)
    assigned_workstreams: List[str] = Field(default_factory=list)
    required_capabilities: List[str] = Field(default_factory=list)
    matched_capabilities: List[str] = Field(default_factory=list)
    selection_reason: str = Field(...)
    dependency_relationships: List[str] = Field(default_factory=list)
    priority: str = Field(default="NORMAL")

class StrategicPlan(BaseModel):
    strategic_plan_id: str = Field(pattern=r"^plan_[a-z0-9]{8,64}$")
    objective_id: str = Field(...)
    objective_version: int = Field(..., ge=1)
    status: StrategicPlanStatus = Field(default=StrategicPlanStatus.DRAFT)
    business_outcome: str = Field(...)
    executive_summary: str = Field(...)
    success_criteria: Dict[str, Any] = Field(default_factory=dict)
    workstreams: List[StrategicWorkstream] = Field(default_factory=list)
    department_assignments: List[DepartmentAssignment] = Field(default_factory=list)
    dependencies: List[str] = Field(default_factory=list)
    assumptions: List[str] = Field(default_factory=list)
    risks: List[str] = Field(default_factory=list)
    constraints: List[str] = Field(default_factory=list)
    clarification_questions: List[str] = Field(default_factory=list)
    execution_readiness: str = Field(default="NEEDS_CLARIFICATION")
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    version: int = Field(default=1, ge=1)
    strategy_confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    financial_constraints: Dict[str, Any] = Field(default_factory=dict)