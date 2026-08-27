import uuid
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone

from app.schemas.company_objective import CompanyObjective
from app.schemas.strategic_plan import StrategicPlan
from app.schemas.mission_portfolio import MissionPortfolio
from app.schemas.executive_strategy import ExecutiveStrategyEvaluation
from app.repositories.executive_strategy_repository import executive_strategy_repository
from app.repositories.mission_portfolio_repository import mission_portfolio_repository
from app.repositories.strategic_plan_repository import strategic_plan_repository

# Use the mocked/reconstructed ExecutiveDecision models
try:
    from app.schemas.shared_artifacts import ExecutiveDecisionType, ExecutiveDecisionRecord
except ImportError:
    from enum import Enum
    from pydantic import BaseModel, Field
    class ExecutiveDecisionType(str, Enum):
        OBJECTIVE_COMPLETE = "OBJECTIVE_COMPLETE"
        FOLLOW_UP_MISSION = "FOLLOW_UP_MISSION"
        CHANGE_STRATEGY = "CHANGE_STRATEGY"
        WAIT = "WAIT"
        ESCALATE = "ESCALATE"
        STOP = "STOP"
        NO_ACTION = "NO_ACTION"
        BLOCKED = "BLOCKED"

    class ExecutiveDecisionRecord(BaseModel):
        decision_id: str = Field(...)
        objective_id: str = Field(...)
        objective_version: int = Field(...)
        mission_id: str = Field(...)
        mission_terminal_event_id: str = Field(...)
        mission_terminal_state: str = Field(...)
        decision_type: ExecutiveDecisionType = Field(...)
        evidence_artifact_ids: List[str] = Field(default_factory=list)
        evidence_summary: Dict[str, Any] = Field(default_factory=dict)
        selected_follow_up_action: Optional[Dict[str, Any]] = Field(default=None)
        authority_scope: str = Field(default="INTERNAL_COMPANY_OBJECTIVE_STATE")
        approval_required: bool = Field(default=False)
        created_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
        strategy_version_before: int = Field(default=1)
        strategy_version_after: int = Field(default=1)
        objective_progress_before: float = Field(default=0.0)
        objective_progress_after: float = Field(default=0.0)
        zero_progress_detected: bool = Field(default=False)
        terminal: bool = Field(default=False)
        question: str = Field(default="Unknown")
        decision_mode: str = Field(default="STRATEGIC DECISION MODE")
        specialists_consulted: List[str] = Field(default_factory=list)
        decision: str = Field(...)
        reason: str = Field(...)
        evidence: str = Field(...)
        confidence: str = Field(...)
        approval_requirement: str = Field(...)
        action_executed: bool = Field(default=False)

class ExecutiveStrategyService:
    def __init__(self, objective_repo=None, portfolio_repo=None, decision_repo=None, plan_repo=None):
        from app.repositories.company_objective_repository import company_objective_repository
        self.objective_repo = objective_repo or company_objective_repository
        self.portfolio_repo = portfolio_repo or mission_portfolio_repository
        self.decision_repo = decision_repo
        self.plan_repo = plan_repo or strategic_plan_repository

    def evaluate_portfolio_outcome(self, 
        objective_id: str,
        portfolio_id: str,
        terminal_mission_id: str,
        terminal_definition_id: str,
        verified_outcome: str,
        evidence_refs: List[str],
        success_criteria_met: bool
    ) -> ExecutiveStrategyEvaluation:
        
        # Concurrency safety: Idempotent processing check
        existing = executive_strategy_repository.get_by_trigger(portfolio_id, terminal_mission_id)
        if existing:
            return existing
            
        objective = self.objective_repo.get(objective_id)
        if not objective:
            raise ValueError("OBJECTIVE_NOT_FOUND")
            
        portfolio = self.portfolio_repo.get(portfolio_id)
        if not portfolio:
            raise ValueError("MISSION_PORTFOLIO_NOT_FOUND")
            
        if portfolio.objective_id != objective.objective_id:
            raise ValueError("CROSS_OBJECTIVE_ATTACK_REJECTED")

        plan = self.plan_repo.get(portfolio.strategic_plan_id)
        if not plan:
            raise ValueError("STRATEGIC_PLAN_NOT_FOUND")
            
        # Check staleness
        if objective.version != portfolio.objective_version or portfolio.status in ["SUPERSEDED", "CANCELLED"]:
            raise ValueError("STALE_PORTFOLIO_STATE")

        mission_def = next((m for m in portfolio.mission_definitions if m.mission_definition_id == terminal_definition_id), None)
        
        progress_before = objective.progress
        progress_after = progress_before 
        
        failed_ws = []
        success_ws = []
        if success_criteria_met:
            progress_after = min(100.0, progress_before + (100.0 / max(1, objective.success_criteria.required)))
            if mission_def:
                success_ws.append(mission_def.workstream_id)
        else:
            if mission_def:
                failed_ws.append(mission_def.workstream_id)
                
        progress_delta = progress_after - progress_before
        zero_progress_increment = (progress_delta <= 0)
        
        current_zero_cycles = objective.zero_progress_cycles + (1 if zero_progress_increment else 0)
        if progress_delta > 0:
            current_zero_cycles = 0 # meaningful progress resets zero-progress counter
        
        # Prevent database validation crash if we hit the limit
        current_zero_cycles = min(current_zero_cycles, objective.max_zero_progress_cycles)

        recommendation = "CONTINUE"
        reasons = []
        effectiveness = "EFFECTIVE" if success_criteria_met else "INEFFECTIVE"
        
        cycle_count = objective.metadata.get("executive_cycle_count", 0) + 1
        
        # Deterministic Policy BEFORE LLM
        # Limits Enforcement
        if cycle_count >= 20: # MAX_EXECUTIVE_CYCLES_PER_OBJECTIVE
            recommendation = "PAUSE"
            reasons.append("MAX_EXECUTIVE_CYCLES_REACHED")
            effectiveness = "BLOCKED"
        elif current_zero_cycles >= objective.max_zero_progress_cycles:
            recommendation = "ESCALATE"
            reasons.append("MAX_ZERO_PROGRESS_CYCLES_REACHED")
        elif objective.is_terminal:
            recommendation = "STOP"
            reasons.append("OBJECTIVE_TERMINAL_STATE")
        elif not success_criteria_met:
            if objective.strategy_change_count < objective.max_strategy_changes:
                recommendation = "REPLAN"
                reasons.append("INEFFECTIVE_STRATEGY_REVISION_REQUIRED")
            else:
                recommendation = "REQUEST_OWNER_DECISION"
                reasons.append("STRATEGY_REVISION_LIMIT_REACHED")
        else:
            # Success logic
            if progress_after >= 100.0:
                recommendation = "RECOMMEND_OBJECTIVE_COMPLETION"
                reasons.append("SUCCESS_CRITERIA_VERIFIED")
                
        eval_record = ExecutiveStrategyEvaluation(
            evaluation_id=f"eval_{uuid.uuid4().hex[:8]}",
            objective_id=objective.objective_id,
            objective_version=objective.version,
            strategic_plan_id=plan.strategic_plan_id,
            strategic_plan_version=plan.version,
            portfolio_id=portfolio.portfolio_id,
            portfolio_version=portfolio.version,
            trigger_mission_id=terminal_mission_id,
            trigger_mission_definition_id=terminal_definition_id,
            verified_outcome=verified_outcome,
            objective_progress_before=progress_before,
            objective_progress_after=progress_after,
            progress_delta=progress_delta,
            portfolio_progress=portfolio.portfolio_progress,
            strategy_effectiveness=effectiveness,
            financial_state="HEALTHY",
            risk_state="NORMAL" if recommendation in ["CONTINUE", "RECOMMEND_OBJECTIVE_COMPLETION"] else "ELEVATED",
            blocked_dependencies=[],
            newly_eligible_missions=[], 
            failed_workstreams=failed_ws,
            successful_workstreams=success_ws,
            evidence_refs=evidence_refs,
            recommendation=recommendation,
            reason_codes=reasons,
            created_at=datetime.now(timezone.utc).isoformat(),
            version=1
        )
        
        executive_strategy_repository.create(eval_record)
        
        # Mutate canonical objective safely
        updates = {"metadata": {**objective.metadata, "executive_cycle_count": cycle_count}}
        if zero_progress_increment:
            updates["zero_progress_cycles"] = current_zero_cycles
        else:
            updates["zero_progress_cycles"] = 0
            
        # Replan logic increments strategy revision count and syncs objective
        if recommendation == "REPLAN":
            objective = self.objective_repo.record_strategy_change(objective.objective_id)
        
        # ONLY verified mission outcomes update progress. We don't automatically complete objective.
        objective = self.objective_repo.update(objective.objective_id, updates, expected_version=objective.version)
        
        if progress_after != progress_before:
            current_success_count = objective.verified_success_count
            self.objective_repo.set_verified_success_count(objective.objective_id, current_success_count + 1)
        
        return eval_record

    def generate_decision(self, evaluation: ExecutiveStrategyEvaluation) -> ExecutiveDecisionRecord:
        decision_type_map = {
            "CONTINUE": ExecutiveDecisionType.NO_ACTION,
            "FOLLOW_UP_MISSION": ExecutiveDecisionType.FOLLOW_UP_MISSION,
            "REPLAN": ExecutiveDecisionType.CHANGE_STRATEGY,
            "ESCALATE": ExecutiveDecisionType.ESCALATE,
            "PAUSE": ExecutiveDecisionType.WAIT,
            "REQUEST_OWNER_DECISION": ExecutiveDecisionType.BLOCKED,
            "RECOMMEND_OBJECTIVE_COMPLETION": ExecutiveDecisionType.OBJECTIVE_COMPLETE,
            "STOP": ExecutiveDecisionType.STOP
        }
        
        rec_type = decision_type_map.get(evaluation.recommendation, ExecutiveDecisionType.NO_ACTION)
        
        decision = ExecutiveDecisionRecord(
            decision_id=f"dec_exec_{uuid.uuid4().hex[:8]}",
            objective_id=evaluation.objective_id,
            objective_version=evaluation.objective_version,
            mission_id=evaluation.trigger_mission_id,
            mission_terminal_event_id="evt_mock",
            mission_terminal_state=evaluation.verified_outcome,
            decision_type=rec_type,
            evidence_artifact_ids=evaluation.evidence_refs,
            evidence_summary={"effectiveness": evaluation.strategy_effectiveness, "delta": evaluation.progress_delta},
            strategy_version_before=evaluation.strategic_plan_version,
            strategy_version_after=evaluation.strategic_plan_version + 1 if rec_type == ExecutiveDecisionType.CHANGE_STRATEGY else evaluation.strategic_plan_version,
            objective_progress_before=evaluation.objective_progress_before,
            objective_progress_after=evaluation.objective_progress_after,
            zero_progress_detected=(evaluation.progress_delta <= 0),
            terminal=(rec_type in [ExecutiveDecisionType.OBJECTIVE_COMPLETE, ExecutiveDecisionType.ESCALATE, ExecutiveDecisionType.BLOCKED, ExecutiveDecisionType.STOP]),
            decision=evaluation.recommendation,
            reason=",".join(evaluation.reason_codes),
            evidence=f"Progress changed by {evaluation.progress_delta}",
            confidence="High",
            approval_requirement="NONE"
        )
        
        if self.decision_repo:
            self.decision_repo.create(decision)
            
        return decision