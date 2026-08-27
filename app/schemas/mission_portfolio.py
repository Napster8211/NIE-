from typing import Dict, Any, Optional, List
from enum import Enum
from datetime import datetime, timezone
from pydantic import BaseModel, Field

class MissionPortfolioStatus(str, Enum):
    DRAFT = "DRAFT"
    READY = "READY"
    ACTIVE = "ACTIVE"
    PARTIALLY_BLOCKED = "PARTIALLY_BLOCKED"
    WAITING = "WAITING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    EXHAUSTED = "EXHAUSTED"
    SUPERSEDED = "SUPERSEDED"

class MissionDefinition(BaseModel):
    mission_definition_id: str = Field(...)
    workstream_id: str = Field(...)
    title: str = Field(...)
    objective: str = Field(...)
    department_id: str = Field(...)
    mission_type: str = Field(default="ARTIFACT_PRODUCTION")
    priority: str = Field(default="NORMAL")
    success_criterion: str = Field(...)
    target_count: int = Field(default=1)
    expected_artifact: str = Field(default="UnknownArtifact")
    dependencies: List[str] = Field(default_factory=list)
    estimated_cost: Optional[float] = None
    financial_scope: Optional[Dict[str, Any]] = None
    requires_human_approval: bool = Field(default=False)
    execution_ready: bool = Field(default=False)
    execution_blockers: List[str] = Field(default_factory=list)
    strategic_reason: str = Field(...)

class MissionPortfolio(BaseModel):
    portfolio_id: str = Field(pattern=r"^port_[a-z0-9]{8,64}$")
    objective_id: str = Field(...)
    objective_version: int = Field(..., ge=1)
    strategic_plan_id: str = Field(...)
    strategic_plan_version: int = Field(..., ge=1)
    status: MissionPortfolioStatus = Field(default=MissionPortfolioStatus.DRAFT)
    mission_definitions: List[MissionDefinition] = Field(default_factory=list)
    dependencies: Dict[str, List[str]] = Field(default_factory=dict)
    execution_groups: List[List[str]] = Field(default_factory=list)
    max_parallel_missions: int = Field(default=4)
    max_total_missions: int = Field(default=20)
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    version: int = Field(default=1, ge=1)
    portfolio_progress: float = Field(default=0.0)
    risk_state: str = Field(default="HEALTHY")
    blocking_reasons: List[str] = Field(default_factory=list)