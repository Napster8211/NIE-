from typing import Dict, Any, Optional, List
from enum import Enum
from datetime import datetime, timezone
from pydantic import BaseModel, Field

class ExecutiveStrategyEvaluation(BaseModel):
    evaluation_id: str = Field(...)
    objective_id: str = Field(...)
    objective_version: int = Field(...)
    strategic_plan_id: str = Field(...)
    strategic_plan_version: int = Field(...)
    portfolio_id: str = Field(...)
    portfolio_version: int = Field(...)
    trigger_mission_id: str = Field(...)
    trigger_mission_definition_id: str = Field(...)
    verified_outcome: str = Field(...)
    objective_progress_before: float = Field(...)
    objective_progress_after: float = Field(...)
    progress_delta: float = Field(...)
    portfolio_progress: float = Field(...)
    strategy_effectiveness: str = Field(...)
    financial_state: str = Field(...)
    risk_state: str = Field(...)
    blocked_dependencies: List[str] = Field(default_factory=list)
    newly_eligible_missions: List[str] = Field(default_factory=list)
    failed_workstreams: List[str] = Field(default_factory=list)
    successful_workstreams: List[str] = Field(default_factory=list)
    evidence_refs: List[str] = Field(default_factory=list)
    recommendation: str = Field(...)
    reason_codes: List[str] = Field(default_factory=list)
    created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    version: int = Field(default=1)