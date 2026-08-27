"""Typed company-objective state owned by Director Intelligence."""
from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()

class CompanyObjectiveStatus(str, Enum):
    DRAFT = "DRAFT"
    ACTIVE = "ACTIVE"
    PAUSED = "PAUSED"  # SPRINT 6D: Owner Control state
    WAITING_APPROVAL = "WAITING_APPROVAL"
    WAITING_DIRECTOR = "WAITING_DIRECTOR"
    BLOCKED = "BLOCKED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    ESCALATED = "ESCALATED"
    EXHAUSTED = "EXHAUSTED"

TERMINAL_OBJECTIVE_STATUSES = frozenset({
    CompanyObjectiveStatus.COMPLETED,
    CompanyObjectiveStatus.FAILED,
    CompanyObjectiveStatus.CANCELLED,
    CompanyObjectiveStatus.ESCALATED,
    CompanyObjectiveStatus.EXHAUSTED,
})

class CompanyObjectiveSuccessCriteria(BaseModel):
    criterion: str = Field(min_length=1)
    required: int = Field(ge=1)
    unit: str = Field(default="verified_outcomes", min_length=1)
    evidence_requirements: List[str] = Field(default_factory=list)

class CompanyObjective(BaseModel):
    """Persisted CEO-level objective; deliberately separate from mission success."""

    model_config = ConfigDict(validate_assignment=True)

    objective_id: str = Field(pattern=r"^obj_[a-z0-9]{8,64}$")
    title: str = Field(min_length=1, max_length=200)
    objective: str = Field(min_length=1)
    status: CompanyObjectiveStatus = CompanyObjectiveStatus.DRAFT
    priority: str = Field(default="NORMAL", min_length=1)
    autonomy_level: str = Field(default="SUPERVISED", min_length=1)
    created_at: str = Field(default_factory=utc_now_iso)
    updated_at: str = Field(default_factory=utc_now_iso)
    version: int = Field(default=1, ge=1)
    success_criteria: CompanyObjectiveSuccessCriteria
    verified_success_count: int = Field(default=0, ge=0)
    progress: float = Field(default=0.0, ge=0, le=100)
    credited_evidence_ids: List[str] = Field(default_factory=list)
    evaluated_terminal_event_ids: List[str] = Field(default_factory=list)
    linked_mission_ids: List[str] = Field(default_factory=list)
    max_missions: int = Field(ge=1)
    max_strategy_changes: int = Field(ge=0)
    max_zero_progress_cycles: int = Field(ge=0)
    strategy_change_count: int = Field(default=0, ge=0)
    zero_progress_cycles: int = Field(default=0, ge=0)
    time_limit_seconds: Optional[int] = Field(default=None, gt=0)
    deadline: Optional[str] = None
    budget_limit: Optional[float] = Field(default=None, ge=0)
    financial_budget_id: Optional[str] = Field(default=None)
    risk_limit: Optional[str] = None
    escalation_threshold: Optional[int] = Field(default=None, ge=0)
    current_strategy_version: int = Field(default=1, ge=1)
    terminal_reason: Optional[str] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def enforce_objective_invariants(self) -> "CompanyObjective":
        if len(self.linked_mission_ids) != len(set(self.linked_mission_ids)):
            raise ValueError("OBJECTIVE_DUPLICATE_MISSION_LINK")
        if len(self.credited_evidence_ids) != len(set(self.credited_evidence_ids)):
            raise ValueError("OBJECTIVE_DUPLICATE_EVIDENCE_CREDIT")
        if len(self.evaluated_terminal_event_ids) != len(
            set(self.evaluated_terminal_event_ids)
        ):
            raise ValueError("OBJECTIVE_DUPLICATE_TERMINAL_EVENT")
        if len(self.linked_mission_ids) > self.max_missions:
            raise ValueError("OBJECTIVE_MAX_MISSIONS_REACHED")
        if self.strategy_change_count > self.max_strategy_changes:
            raise ValueError("OBJECTIVE_MAX_STRATEGY_CHANGES_REACHED")
        if self.zero_progress_cycles > self.max_zero_progress_cycles:
            raise ValueError("OBJECTIVE_MAX_ZERO_PROGRESS_CYCLES_REACHED")
        required = self.success_criteria.required
        derived_progress = min(
            100.0, round((self.verified_success_count * 100.0) / required, 2)
        )
        object.__setattr__(self, "progress", derived_progress)
        if self.status == CompanyObjectiveStatus.COMPLETED:
            if self.verified_success_count < required or derived_progress != 100.0:
                raise ValueError("OBJECTIVE_SUCCESS_CRITERIA_UNVERIFIED")
        return self

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_OBJECTIVE_STATUSES