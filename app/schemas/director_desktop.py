from typing import Dict, Any, Optional, List
from enum import Enum
from datetime import datetime, timezone
from pydantic import BaseModel, Field

class DesktopBaseModel(BaseModel):
    pass

class ExecutiveOverview(DesktopBaseModel):
    generated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    director_status: str = Field(default="UNKNOWN")
    company_state: str = Field(default="UNKNOWN")
    active_objectives: int = Field(default=0)
    blocked_objectives: int = Field(default=0)
    completed_objectives: int = Field(default=0)
    active_missions: int = Field(default=0)
    blocked_missions: int = Field(default=0)
    completed_missions: int = Field(default=0)
    active_departments: int = Field(default=0)
    pending_approvals: int = Field(default=0)
    owner_actions_required: int = Field(default=0)
    critical_risks: int = Field(default=0)
    financial_status: str = Field(default="UNKNOWN")
    recent_decisions: List[Dict[str, Any]] = Field(default_factory=list)
    recent_activity: List[Dict[str, Any]] = Field(default_factory=list)
    system_health: str = Field(default="HEALTHY")

class DirectorStatusView(DesktopBaseModel):
    agent_id: str = Field(default="director_intelligence")
    display_name: str = Field(default="Director Intelligence")
    role: str = Field(default="AI CEO")
    status: str = Field(default="IDLE")
    current_objective_id: Optional[str] = Field(default=None)
    current_portfolio_id: Optional[str] = Field(default=None)
    current_mission_id: Optional[str] = Field(default=None)
    current_activity: str = Field(default="Awaiting executive action.")
    last_decision: Optional[str] = Field(default=None)
    last_evaluation_at: Optional[str] = Field(default=None)
    owner_action_required: bool = Field(default=False)
    owner_action_reason: Optional[str] = Field(default=None)
    health: str = Field(default="HEALTHY")

class ObjectiveView(DesktopBaseModel):
    objective_id: str = Field(...)
    title: str = Field(...)
    description: str = Field(...)
    status: str = Field(...)
    priority: str = Field(...)
    version: int = Field(...)
    created_at: str = Field(...)
    updated_at: str = Field(...)
    success_criteria: Dict[str, Any] = Field(...)
    verified_success_count: int = Field(...)
    progress_percentage: float = Field(...)
    mission_count: int = Field(...)
    max_missions: int = Field(...)
    zero_progress_cycles: int = Field(...)
    max_zero_progress_cycles: int = Field(...)
    strategy_change_count: int = Field(...)
    max_strategy_changes: int = Field(...)
    current_strategy_id: Optional[str] = Field(default=None)
    current_portfolio_id: Optional[str] = Field(default=None)
    risk_level: str = Field(default="NORMAL")
    owner_action_required: bool = Field(default=False)

class StrategyView(DesktopBaseModel):
    strategic_plan_id: str = Field(...)
    objective_id: str = Field(...)
    version: int = Field(...)
    status: str = Field(...)
    executive_summary: str = Field(...)
    workstreams: List[Dict[str, Any]] = Field(...)
    department_assignments: List[Dict[str, Any]] = Field(...)
    dependencies: List[str] = Field(...)
    risks: List[str] = Field(...)
    capability_gaps: List[str] = Field(default_factory=list)
    execution_readiness: str = Field(...)
    created_at: str = Field(...)
    superseded_at: Optional[str] = Field(default=None)

class PortfolioView(DesktopBaseModel):
    portfolio_id: str = Field(...)
    objective_id: str = Field(...)
    strategic_plan_id: str = Field(...)
    status: str = Field(...)
    progress: float = Field(...)
    mission_definitions: List[Dict[str, Any]] = Field(...)
    execution_groups: List[List[str]] = Field(...)
    currently_eligible: List[str] = Field(...)
    blocked_missions: List[str] = Field(...)
    completed_missions: List[str] = Field(...)
    failed_missions: List[str] = Field(...)
    max_parallel_missions: int = Field(...)
    created_at: str = Field(...)
    updated_at: str = Field(...)

class MissionView(DesktopBaseModel):
    mission_id: str = Field(...)
    mission_definition_id: Optional[str] = Field(default=None)
    portfolio_id: Optional[str] = Field(default=None)
    objective_id: Optional[str] = Field(default=None)
    department_id: Optional[str] = Field(default=None)
    title: str = Field(...)
    description: str = Field(...)
    status: str = Field(...)
    priority: str = Field(...)
    progress: int = Field(...)
    current_step: str = Field(...)
    started_at: Optional[str] = Field(default=None)
    completed_at: Optional[str] = Field(default=None)
    verification_status: str = Field(...)
    result_summary: Optional[str] = Field(default=None)
    failure_summary: Optional[str] = Field(default=None)
    blocked_reason: Optional[str] = Field(default=None)
    requires_human_approval: bool = Field(default=False)
    financial_state: str = Field(default="UNKNOWN")

# --- SPRINT 6B.2B DESKTOP VIEWS ---

class AgentDesktopView(DesktopBaseModel):
    agent_id: str = Field(...)
    display_name: str = Field(...)
    role: str = Field(...)
    category: str = Field(...)
    department_id: Optional[str] = Field(default=None)
    department_name: Optional[str] = Field(default=None)
    capabilities: List[str] = Field(...)
    status: str = Field(default="IDLE")
    current_mission_id: Optional[str] = Field(default=None)
    current_objective_id: Optional[str] = Field(default=None)
    current_task_summary: str = Field(default="No active task")
    missions_completed: int = Field(default=0)
    missions_failed: int = Field(default=0)
    last_activity_at: str = Field(...)
    health: str = Field(default="HEALTHY")

class DepartmentView(DesktopBaseModel):
    department_id: str = Field(...)
    department_name: str = Field(...)
    status: str = Field(default="UNKNOWN")
    health: str = Field(default="HEALTHY")
    agent_count: int = Field(default=0)
    active_agent_count: int = Field(default=0)
    active_mission_count: int = Field(default=0)
    agents: List[AgentDesktopView] = Field(default_factory=list)

# ------------------------------------

class ExecutiveDecisionView(DesktopBaseModel):
    decision_id: str = Field(...)
    objective_id: str = Field(...)
    portfolio_id: Optional[str] = Field(default=None)
    mission_id: Optional[str] = Field(default=None)
    evaluation_id: Optional[str] = Field(default=None)
    decision_type: str = Field(...)
    reason: str = Field(...)
    summary: str = Field(...)
    evidence_refs: List[str] = Field(...)
    created_at: str = Field(...)
    owner_action_required: bool = Field(default=False)

class ExecutiveEvaluationView(DesktopBaseModel):
    evaluation_id: str = Field(...)
    objective_id: str = Field(...)
    portfolio_id: str = Field(...)
    trigger_mission_id: str = Field(...)
    strategy_effectiveness: str = Field(...)
    progress_before: float = Field(...)
    progress_after: float = Field(...)
    progress_delta: float = Field(...)
    portfolio_progress: float = Field(...)
    risk_state: str = Field(...)
    successful_workstreams: List[str] = Field(...)
    failed_workstreams: List[str] = Field(...)
    blocked_dependencies: List[str] = Field(...)
    recommendation: str = Field(...)
    executive_summary: str = Field(...)
    progress_summary: str = Field(...)
    risk_summary: str = Field(...)
    decision_summary: str = Field(...)
    next_actions: List[str] = Field(...)
    created_at: str = Field(...)

class ApprovalView(DesktopBaseModel):
    approval_id: str = Field(...)
    mission_id: str = Field(...)
    objective_id: Optional[str] = Field(default=None)
    operation_type: str = Field(...)
    resource_scope: str = Field(...)
    status: str = Field(...)
    requested_at: str = Field(...)
    expires_at: Optional[str] = Field(default=None)
    resolved_at: Optional[str] = Field(default=None)
    reason: Optional[str] = Field(default=None)
    summary: str = Field(...)
    requesting_agent: str = Field(...)

class FinancialSummaryView(DesktopBaseModel):
    objective_id: Optional[str] = Field(default=None)
    currency: str = Field(default="USD")
    budget_limit: float = Field(default=0.0)
    allocated: float = Field(default=0.0)
    reserved: float = Field(default=0.0)
    committed: float = Field(default=0.0)
    spent: float = Field(default=0.0)
    available: float = Field(default=0.0)
    financial_status: str = Field(default="NOT_AVAILABLE")
    blocked: bool = Field(default=False)
    block_reason: Optional[str] = Field(default=None)
    requires_owner_action: bool = Field(default=False)
    last_updated: str = Field(...)

class OwnerActionItem(DesktopBaseModel):
    action_id: str = Field(...)
    action_type: str = Field(...)
    severity: str = Field(...)
    objective_id: Optional[str] = Field(default=None)
    mission_id: Optional[str] = Field(default=None)
    approval_id: Optional[str] = Field(default=None)
    title: str = Field(...)
    summary: str = Field(...)
    reason: str = Field(...)
    created_at: str = Field(...)
    expires_at: Optional[str] = Field(default=None)
    available_actions: List[str] = Field(...)
    source_type: str = Field(...)
    source_id: str = Field(...)

class RiskView(DesktopBaseModel):
    risk_id: str = Field(...)
    level: str = Field(...)
    category: str = Field(...)
    objective_id: Optional[str] = Field(default=None)
    portfolio_id: Optional[str] = Field(default=None)
    mission_id: Optional[str] = Field(default=None)
    title: str = Field(...)
    summary: str = Field(...)
    source: str = Field(...)
    detected_at: str = Field(...)
    status: str = Field(...)
    owner_action_required: bool = Field(default=False)

class ExecutiveActivityItem(DesktopBaseModel):
    activity_id: str = Field(...)
    timestamp: str = Field(...)
    event_type: str = Field(...)
    severity: str = Field(...)
    objective_id: Optional[str] = Field(default=None)
    mission_id: Optional[str] = Field(default=None)
    department_id: Optional[str] = Field(default=None)
    title: str = Field(...)
    summary: str = Field(...)
    source: str = Field(...)
    metadata_safe: Dict[str, Any] = Field(default_factory=dict)

class DesktopBootstrapState(DesktopBaseModel):
    overview: ExecutiveOverview
    director: DirectorStatusView
    objectives: List[ObjectiveView]
    active_missions: List[MissionView]
    departments: List[DepartmentView]
    pending_approvals: List[ApprovalView]
    owner_actions: List[OwnerActionItem]
    financial_summary: FinancialSummaryView
    recent_decisions: List[ExecutiveDecisionView]
    recent_activity: List[ExecutiveActivityItem]
    system_health: str