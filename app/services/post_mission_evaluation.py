"""Deterministic, bounded CEO evaluation of one terminal mission event."""
from __future__ import annotations

import hashlib
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, model_validator

from app.engine.event_bus import BusinessEvent
from app.engine.mission_engine import MissionCompletionGuard, PersistentMission, mission_registry
from app.repositories.company_objective_repository import (
    CompanyObjectiveRepository,
    ObjectivePersistenceError,
    company_objective_repository,
)
from app.repositories.executive_decision_repository import (
    DuplicateExecutiveDecisionError,
    ExecutiveDecisionPersistenceError,
    ExecutiveDecisionRepository,
    executive_decision_repository,
)
from app.schemas.company_objective import (
    CompanyObjective,
    CompanyObjectiveStatus,
)
from app.schemas.shared_artifacts import (
    ExecutiveDecisionRecord,
    ExecutiveDecisionType,
)


MISSION_TERMINAL_EVENT = "MISSION_TERMINAL"
TERMINAL_MISSION_STATES = frozenset({
    "COMPLETED",
    "FAILED",
    "BLOCKED",
    "WAITING_DIRECTOR",
    "EXHAUSTED",
    "ESCALATED",
    "CANCELLED",
})


class PostMissionEvaluationError(RuntimeError):
    pass


class MissionTerminalEvent(BaseModel):
    event_id: str = Field(min_length=1)
    mission_id: str = Field(min_length=1)
    objective_id: str = Field(min_length=1)
    terminal_state: str = Field(min_length=1)
    terminal_reason: str = Field(min_length=1)
    state_revision: int = Field(ge=0)
    occurred_at: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_terminal_state(self) -> "MissionTerminalEvent":
        self.terminal_state = self.terminal_state.upper()
        if self.terminal_state not in TERMINAL_MISSION_STATES:
            raise ValueError("MISSION_TERMINAL_EVENT_INVALID_STATE")
        return self

    @classmethod
    def from_mission(cls, mission: PersistentMission) -> "MissionTerminalEvent":
        if not mission.objective_id:
            raise ValueError("MISSION_OBJECTIVE_LINK_MISSING")
        terminal_state = str(mission.status).upper()
        if terminal_state not in TERMINAL_MISSION_STATES:
            raise ValueError("MISSION_TERMINAL_EVENT_INVALID_STATE")
        reason = str(mission.terminal_reason or mission.escalation_reason or terminal_state)
        signature = "|".join((
            mission.mission_id,
            mission.objective_id,
            terminal_state,
            reason,
            str(mission.state_revision),
        ))
        return cls(
            event_id=f"mte_{hashlib.sha256(signature.encode('utf-8')).hexdigest()[:16]}",
            mission_id=mission.mission_id,
            objective_id=mission.objective_id,
            terminal_state=terminal_state,
            terminal_reason=reason,
            state_revision=mission.state_revision,
            occurred_at=mission.updated_at,
        )

    def to_business_event(self) -> BusinessEvent:
        return BusinessEvent(
            event_id=self.event_id,
            event_type=MISSION_TERMINAL_EVENT,
            timestamp=self.occurred_at,
            lead_id=self.objective_id,
            business_name="NapsterTec",
            communication_id="",
            conversation_id="",
            correlation_id=self.mission_id,
            workflow_id=self.objective_id,
            channel="INTERNAL",
            evidence=self.terminal_reason,
            confidence=1.0,
            execution_metadata=self.model_dump(mode="json"),
        )


class PostMissionEvaluationCoordinator:
    """One event in, at most one objective update and one immutable decision out."""

    def __init__(
        self,
        objective_repository: Optional[CompanyObjectiveRepository] = None,
        decision_repository: Optional[ExecutiveDecisionRepository] = None,
        mission_source: Any = None,
    ):
        self.objectives = objective_repository or company_objective_repository
        self.decisions = decision_repository or executive_decision_repository
        self.missions = mission_source or mission_registry
        self._lock = threading.RLock()

    async def handle_business_event(
        self, event: BusinessEvent
    ) -> ExecutiveDecisionRecord:
        if event.event_type != MISSION_TERMINAL_EVENT:
            raise PostMissionEvaluationError("MISSION_TERMINAL_EVENT_TYPE_REQUIRED")
        try:
            terminal_event = MissionTerminalEvent.model_validate(event.execution_metadata)
        except Exception as exc:
            raise PostMissionEvaluationError(
                f"MISSION_TERMINAL_EVENT_MALFORMED: {exc}"
            ) from exc
        if event.event_id != terminal_event.event_id:
            raise PostMissionEvaluationError("MISSION_TERMINAL_EVENT_ID_MISMATCH")
        return self.evaluate(terminal_event)

    def evaluate(self, terminal_event: MissionTerminalEvent) -> ExecutiveDecisionRecord:
        with self._lock:
            existing = self.decisions.get_by_terminal_event(
                terminal_event.objective_id, terminal_event.event_id
            )
            if existing:
                return existing

            objective = self.objectives.get(terminal_event.objective_id)
            if not objective:
                raise PostMissionEvaluationError("COMPANY_OBJECTIVE_NOT_FOUND")
            mission = self.missions.get_mission(terminal_event.mission_id)
            if not mission:
                raise PostMissionEvaluationError("MISSION_NOT_FOUND")
            self._validate_lineage(objective, mission, terminal_event)

            decision, changes = self._build_decision(objective, mission, terminal_event)
            try:
                persisted_decision = self.decisions.create(decision)
            except DuplicateExecutiveDecisionError as duplicate:
                existing = self.decisions.get(duplicate.existing_decision_id)
                if not existing:
                    raise PostMissionEvaluationError(
                        "EXECUTIVE_DECISION_DUPLICATE_LOOKUP_FAILED"
                    )
                return existing
            except ExecutiveDecisionPersistenceError as exc:
                raise PostMissionEvaluationError(
                    f"EXECUTIVE_DECISION_PERSISTENCE_FAILED: {exc}"
                ) from exc

            if changes:
                try:
                    self.objectives.update(
                        objective.objective_id,
                        changes,
                        expected_version=objective.version,
                    )
                except Exception as exc:
                    try:
                        self.decisions._rollback_create(persisted_decision.decision_id)
                    except Exception as rollback_exc:
                        raise PostMissionEvaluationError(
                            "OBJECTIVE_UPDATE_FAILED_AND_DECISION_ROLLBACK_FAILED: "
                            f"{exc}; rollback={rollback_exc}"
                        ) from exc
                    raise PostMissionEvaluationError(
                        f"OBJECTIVE_UPDATE_PERSISTENCE_FAILED: {exc}"
                    ) from exc
            return persisted_decision

    @staticmethod
    def _validate_lineage(
        objective: CompanyObjective,
        mission: PersistentMission,
        terminal_event: MissionTerminalEvent,
    ) -> None:
        if mission.objective_id != objective.objective_id:
            raise PostMissionEvaluationError("MISSION_OBJECTIVE_LINK_MISMATCH")
        if mission.mission_id not in objective.linked_mission_ids:
            raise PostMissionEvaluationError("MISSION_NOT_LINKED_TO_OBJECTIVE")
        if mission.status.upper() != terminal_event.terminal_state:
            raise PostMissionEvaluationError("MISSION_TERMINAL_STATE_MISMATCH")
        if terminal_event.objective_id != objective.objective_id:
            raise PostMissionEvaluationError("EVENT_OBJECTIVE_LINK_MISMATCH")

    @staticmethod
    def _relevant_verified_evidence_ids(
        objective: CompanyObjective, mission: PersistentMission
    ) -> List[str]:
        if mission.status.upper() != "COMPLETED" or not mission.mission_objective_achieved:
            return []
        guard = MissionCompletionGuard()
        guard_status, mission_success = guard.evaluate_completion(mission)
        if not mission_success or guard_status != "PASSED":
            return []
        verified_ids = guard.verified_success_evidence_ids(mission)
        artifacts_by_id = {
            str(item.get("artifact_id")): item
            for item in mission.artifact_lineage
            if item.get("artifact_id")
        }
        verified_ids = [
            evidence_id
            for evidence_id in verified_ids
            if evidence_id not in artifacts_by_id
            or (
                str(
                    artifacts_by_id[evidence_id].get("evidence_source") or "UNKNOWN"
                ).upper()
                not in {"UNKNOWN", "MOCK_FALLBACK"}
                and artifacts_by_id[evidence_id].get("simulation_evidence") is False
            )
        ]
        if objective.success_criteria.criterion == "verified_qualified_prospects":
            lead_ids = {
                str(item.get("artifact_id"))
                for item in mission.artifact_lineage
                if item.get("artifact_type") == "LeadArtifact"
            }
            verified_ids = [item for item in verified_ids if item in lead_ids]
        return verified_ids

    @staticmethod
    def _evidence_summary(
        mission: PersistentMission, verified_ids: List[str]
    ) -> Dict[str, Any]:
        selected = [
            item
            for item in mission.artifact_lineage
            if str(item.get("artifact_id")) in set(verified_ids)
        ]
        return {
            "mission_status": mission.status,
            "mission_terminal_reason": mission.terminal_reason,
            "mission_progress": mission.progress,
            "mission_success_criteria": dict(mission.success_criteria),
            "mission_verified_count": MissionCompletionGuard.verified_success_count(mission),
            "artifact_types": [item.get("artifact_type") for item in selected],
            "artifact_provenance": [
                {
                    "artifact_id": item.get("artifact_id"),
                    "artifact_type": item.get("artifact_type"),
                    "mission_id": item.get("mission_id"),
                    "plan_version": item.get("plan_version"),
                    "evidence_source": item.get("evidence_source"),
                    "verified": item.get("verified") is True,
                }
                for item in selected
            ],
            "retry_count": mission.retry_count,
            "replan_count": mission.replan_count,
            "external_operation_count": len(mission.external_operations),
        }

    def _build_decision(
        self,
        objective: CompanyObjective,
        mission: PersistentMission,
        terminal_event: MissionTerminalEvent,
    ) -> tuple[ExecutiveDecisionRecord, Dict[str, Any]]:
        before_progress = objective.progress
        strategy_before = objective.current_strategy_version
        relevant = self._relevant_verified_evidence_ids(objective, mission)
        new_evidence = [
            evidence_id
            for evidence_id in relevant
            if evidence_id not in objective.credited_evidence_ids
        ]
        remaining = max(
            0, objective.success_criteria.required - objective.verified_success_count
        )
        credited = new_evidence[:remaining]
        verified_after = objective.verified_success_count + len(credited)
        progress_after = min(
            100.0,
            round(
                (verified_after * 100.0) / objective.success_criteria.required, 2
            ),
        )
        zero_progress = len(credited) == 0
        zero_after = (
            0
            if credited
            else min(
                objective.max_zero_progress_cycles,
                objective.zero_progress_cycles + 1,
            )
        )
        strategy_count_after = objective.strategy_change_count
        strategy_after = strategy_before
        objective_status = objective.status
        decision_type = ExecutiveDecisionType.NO_ACTION
        terminal = False
        follow_up: Optional[Dict[str, Any]] = None
        reason = "Terminal mission produced no objective-relevant verified evidence."

        if objective.is_terminal:
            decision_type = ExecutiveDecisionType.NO_ACTION
            terminal = True
            reason = "Objective is already terminal; no additional work is authorized."
            changes: Dict[str, Any] = {}
        else:
            state = terminal_event.terminal_state
            if state == "COMPLETED" and credited:
                reason = (
                    f"Credited {len(credited)} unique verified mission artifact(s) "
                    "toward the company objective."
                )
                if verified_after >= objective.success_criteria.required:
                    decision_type = ExecutiveDecisionType.OBJECTIVE_COMPLETE
                    objective_status = CompanyObjectiveStatus.COMPLETED
                    terminal = True
                    reason = "Company objective success criteria are verified."
                elif len(objective.linked_mission_ids) >= objective.max_missions:
                    decision_type = ExecutiveDecisionType.ESCALATE
                    objective_status = CompanyObjectiveStatus.EXHAUSTED
                    terminal = True
                    reason = "Objective mission limit reached before success criteria."
                elif objective.status == CompanyObjectiveStatus.WAITING_APPROVAL:
                    decision_type = ExecutiveDecisionType.WAIT
                    reason = "Verified progress recorded; objective remains waiting for approval."
                else:
                    decision_type = ExecutiveDecisionType.FOLLOW_UP_MISSION
                    follow_up = {
                        "action": "PROPOSE_TYPED_FOLLOW_UP_MISSION",
                        "executable": False,
                        "objective_id": objective.objective_id,
                        "mission_type": mission.mission_type,
                        "verification_mode": mission.verification_mode,
                        "success_criteria": dict(mission.success_criteria),
                    }
            elif objective.status == CompanyObjectiveStatus.WAITING_APPROVAL:
                decision_type = ExecutiveDecisionType.WAIT
                objective_status = CompanyObjectiveStatus.WAITING_APPROVAL
                reason = "Objective is waiting for scoped approval; no follow-up is authorized."
            elif state in {"COMPLETED", "FAILED"}:
                if zero_after >= objective.max_zero_progress_cycles:
                    decision_type = ExecutiveDecisionType.ESCALATE
                    objective_status = CompanyObjectiveStatus.ESCALATED
                    terminal = True
                    reason = "Objective zero-progress limit reached."
                elif objective.strategy_change_count >= objective.max_strategy_changes:
                    decision_type = ExecutiveDecisionType.ESCALATE
                    objective_status = CompanyObjectiveStatus.ESCALATED
                    terminal = True
                    reason = "Objective strategy-change limit reached."
                else:
                    decision_type = ExecutiveDecisionType.CHANGE_STRATEGY
                    strategy_count_after += 1
                    strategy_after += 1
                    reason = (
                        "Mission produced no verified objective progress; a bounded "
                        "strategy change is justified."
                    )
            elif state in {"BLOCKED", "WAITING_DIRECTOR"}:
                if zero_after >= objective.max_zero_progress_cycles:
                    decision_type = ExecutiveDecisionType.ESCALATE
                    objective_status = CompanyObjectiveStatus.ESCALATED
                    terminal = True
                    reason = "Blocked mission reached the objective zero-progress limit."
                else:
                    decision_type = ExecutiveDecisionType.WAIT
                    objective_status = CompanyObjectiveStatus.WAITING_DIRECTOR
                    reason = "Mission requires bounded Director or owner intervention."
            elif state == "EXHAUSTED":
                decision_type = ExecutiveDecisionType.ESCALATE
                objective_status = CompanyObjectiveStatus.EXHAUSTED
                terminal = True
                reason = "Mission exhausted its bounded execution policy."
            elif state == "ESCALATED":
                decision_type = ExecutiveDecisionType.ESCALATE
                objective_status = CompanyObjectiveStatus.ESCALATED
                terminal = True
                reason = "Mission explicitly escalated to company-objective governance."
            elif state == "CANCELLED":
                decision_type = ExecutiveDecisionType.STOP
                objective_status = CompanyObjectiveStatus.CANCELLED
                terminal = True
                reason = "Cancelled mission stops objective follow-up work."
            else:
                raise PostMissionEvaluationError("MISSION_TERMINAL_STATE_UNSUPPORTED")

            changes = {
                "verified_success_count": verified_after,
                "credited_evidence_ids": [
                    *objective.credited_evidence_ids, *credited
                ],
                "evaluated_terminal_event_ids": [
                    *objective.evaluated_terminal_event_ids, terminal_event.event_id
                ],
                "zero_progress_cycles": zero_after,
                "strategy_change_count": strategy_count_after,
                "current_strategy_version": strategy_after,
                "status": objective_status.value,
                "terminal_reason": reason if terminal else objective.terminal_reason,
            }

        from app.services.finance_engine import FinanceEngine
        snap = FinanceEngine().generate_snapshot(objective.objective_id)
        assess = FinanceEngine().assess_finances(snap)
        
        financial_evidence = {
            "financial_status": assess.financial_status,
            "financial_risk": assess.risk_level,
            "budget_available": assess.available_amount,
            "budget_currency": assess.currency,
            "finance_assessment_id": assess.assessment_id,
            "financial_snapshot_id": snap.snapshot_id,
            "financial_evidence_version": snap.ledger_version
        }
        
        # SPRINT 4B: Add financial warnings to reasons
        if assess.financial_status == "EXHAUSTED":
            reason += " Budget is EXHAUSTED. Request additional budget."
        elif assess.financial_status == "WARNING":
            reason += " Budget is at WARNING threshold."
        elif assess.financial_status == "OVER_BUDGET":
            reason += " Budget is OVER_BUDGET. Escalate to human."

        decision = ExecutiveDecisionRecord(
            decision_id=f"exd_{uuid.uuid4().hex[:12]}",
            objective_id=objective.objective_id,
            objective_version=objective.version,
            mission_id=mission.mission_id,
            mission_terminal_event_id=terminal_event.event_id,
            mission_terminal_state=terminal_event.terminal_state,
            decision_type=decision_type,
            reason=reason,
            evidence_artifact_ids=credited,
            evidence_summary={**self._evidence_summary(mission, relevant), "financial_evidence": financial_evidence},
            selected_follow_up_action=follow_up,
            authority_scope="INTERNAL_COMPANY_OBJECTIVE_STATE",
            approval_required=(objective.status == CompanyObjectiveStatus.WAITING_APPROVAL),
            created_at=datetime.now(timezone.utc).isoformat(),
            strategy_version_before=strategy_before,
            strategy_version_after=strategy_after,
            objective_progress_before=before_progress,
            objective_progress_after=progress_after,
            zero_progress_detected=zero_progress,
            terminal=terminal,
            question="What bounded company-objective action follows this terminal mission?",
            decision_mode="POST-MISSION EVALUATION MODE",
            specialists_consulted=[],
            decision=decision_type.value,
            evidence=", ".join(credited) if credited else "NO_NEW_VERIFIED_EVIDENCE",
            confidence="HIGH",
            approval_requirement=(
                "PENDING_EXISTING_APPROVAL" if objective.status == CompanyObjectiveStatus.WAITING_APPROVAL else "NONE"
            ),
            action_executed=False,
        )
        return decision, changes

    def inspect_objective(self, objective_id: str) -> Dict[str, Any]:
        objective_digest = self.objectives.persisted_digest()
        decision_digest = self.decisions.persisted_digest()
        objective = self.objectives.get(objective_id)
        if not objective:
            raise PostMissionEvaluationError("COMPANY_OBJECTIVE_NOT_FOUND")
        mission_outcomes = []
        for mission_id in objective.linked_mission_ids:
            mission = self.missions.get_mission(mission_id)
            if mission:
                mission_outcomes.append({
                    "mission_id": mission.mission_id,
                    "status": mission.status,
                    "terminal_reason": mission.terminal_reason,
                    "progress": mission.progress,
                    "verified_evidence_ids": MissionCompletionGuard.verified_success_evidence_ids(mission),
                })
        latest = self.decisions.get_latest_by_objective(objective_id)
        result = objective.model_dump(mode="json")
        result.update({
            "linked_mission_outcomes": mission_outcomes,
            "latest_executive_decision": latest.model_dump(mode="json") if latest else None,
        })
        if (
            objective_digest != self.objectives.persisted_digest()
            or decision_digest != self.decisions.persisted_digest()
        ):
            raise PostMissionEvaluationError("READ_ONLY_EXECUTIVE_STATUS_MUTATED_STATE")
        return result


post_mission_evaluation_coordinator = PostMissionEvaluationCoordinator()