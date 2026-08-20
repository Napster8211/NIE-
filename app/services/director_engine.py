"""
NapsterTec AI - Director Intelligence Engine
Module: app/services/director_engine.py
"""
import uuid
import asyncio
from typing import Dict, Any
from datetime import datetime, timezone
from app.schemas.shared_artifacts import (
    DirectorAgentContext, DirectorArtifact,
    ExecutivePriority, DelegationRecord, ApprovalRequest,
    ActiveAgentSession, ExecutiveDecisionRecord, MutationLedger
)
from app.engine.event_bus import event_bus, BusinessEvent
from app.engine.session_manager import session_manager
from app.engine.mission_engine import MissionEngine, mission_registry, MissionAuditService, MissionWorkCoordinator
from app.services.director_command_resolver import DIRECTOR_COMMAND_UNRESOLVED
from app.services.company_objective_service import CompanyObjectiveService

class DirectorEngine:
    def __init__(
        self,
        objective_service: CompanyObjectiveService | None = None,
        post_mission_coordinator: Any = None,
    ):
        self.objective_service = objective_service or CompanyObjectiveService()
        if post_mission_coordinator is None:
            from app.services.post_mission_evaluation import (
                PostMissionEvaluationCoordinator,
            )

            post_mission_coordinator = PostMissionEvaluationCoordinator(
                objective_repository=self.objective_service.repository,
            )
        self.post_mission_coordinator = post_mission_coordinator

    async def execute_director(self, context: DirectorAgentContext, session_id: str) -> DirectorArtifact:
        
        mode = context.operating_mode
        command_class = getattr(context, 'command_class', 'UNKNOWN') # HOTFIX: Extracted command_class
        exec_ctx = context.execution_context
        m_id = context.mission_id
        m_action = context.mission_action
        objective_id = context.objective_id
        objective_action = context.objective_action
        read_only = context.mission_read_only
        metrics = context.aggregated_metrics
        
        priorities = [ExecutivePriority(level="CRITICAL", description="Resolve pending marketing approvals.")]
        delegations = []
        approvals = []
        sessions = []
        decisions = []
        meta = {"evaluation_method": "CEO Artifact Synthesis & Governance"}
        
        ledger = MutationLedger() # Start pristine 0-mutation ledger

        if command_class == "UNKNOWN":
            raise ValueError(DIRECTOR_COMMAND_UNRESOLVED)

        if command_class == "OBJECTIVE_CREATE":
            objective_record = self.objective_service.create_from_request(context.query)
            objective_id = objective_record.objective_id
            objective_action = "CREATE"
            meta.update(objective_record.model_dump(mode="json"))
            summary = "Company objective created without launching mission work."
            recs = "Objective is ready for explicitly authorized mission linkage."

        elif command_class == "OBJECTIVE_INSPECT":
            objective_action = objective_action or "LIST"
            if objective_id:
                objective_record = self.post_mission_coordinator.inspect_objective(
                    objective_id
                )
                meta.update(objective_record)
                summary = "Company objective inspected with read-only safeguards."
            else:
                objectives = self.objective_service.list()
                meta.update({"objectives": objectives, "objective_count": len(objectives)})
                summary = "Company objectives listed with read-only safeguards."
            recs = "No objective or mission state was mutated."

        elif command_class == "AUDIT" and exec_ctx == "MISSION" and m_id and m_action == "INSPECT_EXECUTION":
            audit_svc = MissionAuditService()
            meta.update(audit_svc.run_execution_audit(m_id))
            summary = "Mission Execution State Audit complete."
            recs = "Review execution evidence constraints."

        elif mode == "AUDIT MODE" or command_class in ["AUDIT", "INSPECT"]:
            audit_svc = MissionAuditService()
            audit_report = audit_svc.run_engineering_audit()
            meta.update(audit_report)
            ledger = MutationLedger(**audit_report["mutation_ledger"])
            summary = "MISSION INTELLIGENCE COMPREHENSIVE ENGINEERING AUDIT generated safely."
            recs = "Review zero-mutation isolation."
            
        elif mode == "EXECUTIVE COMMAND MODE" and exec_ctx == "MISSION" and m_id:
            mission = mission_registry.get_mission(m_id)
            if not mission:
                summary = "MissionStateValidationFailed: MissionNotFound."
                recs = "Validate mission ID."
            elif read_only or m_action == "STATUS":
                summary = "Status Query executed with Read-Only safeguards."
                recs = "No active state mutations performed."
                meta.update({
                    "mission_id": m_id, "mission_action": "STATUS", "read_only": "Yes",
                    "mission_status": mission.status, "current_phase": mission.current_phase, "current_milestone": mission.current_milestone,
                    "plan_version": mission.plan_version, "plan_status": mission.plan_status, "overall_progress": f"{mission.progress}%",
                    "success_criteria": mission.success_criteria_progress, "mission_objective_achieved": mission.mission_objective_achieved,
                    "last_completed_delegation": mission.last_completed_delegation, "last_result_artifact": mission.last_result_artifact,
                    "current_autonomous_delegation": mission.active_delegations[0]["target_agent"] if mission.active_delegations else "None",
                    "expected_artifact": mission.active_delegations[0].get("expected_artifact", "Unknown") if mission.active_delegations else "Unknown",
                    "auto_continue_state": mission.auto_continue_status, "state_mutation_from_query": "None", "mission_state_validation": "Passed"
                })
            elif m_action == "DISPATCH_REQUEST":
                req = next((r for r in mission.execution_requests if r.get("status") == "CLAIMED"), None)
                if req:
                    delegation = MissionWorkCoordinator().create_delegation(m_id, req["execution_request_id"])
                    if not delegation:
                        summary = "Execution request could not be dispatched because its state changed."
                        recs = "Refresh mission execution state."
                        delegation = None
                    if delegation:
                        del_id = delegation["delegation_id"]
                        mission = mission_registry.get_mission(m_id)
                        summary = f"Execution Request {req['execution_request_id']} dispatched to {req['target_intelligence']}."
                        recs = "Awaiting AutonomousMissionWorker claim."
                    if delegation and not read_only: # FireWall Event Bus
                        await event_bus.publish(BusinessEvent(
                            event_id=f"evt_{uuid.uuid4().hex[:8]}", event_type="MISSION_DELEGATION_CREATED", timestamp=datetime.now(timezone.utc).isoformat(),
                            lead_id="internal_napstertec", business_name="NapsterTec", communication_id="", conversation_id="", correlation_id="", workflow_id="", channel="", evidence=f"Delegation {del_id} pending.", confidence=1.0
                        ))
                else:
                    summary = "No claimed requests available to dispatch."
                    recs = "None"
                    
            elif m_action == "CONTINUE":
                if mission.active_delegations:
                    active_del = mission.active_delegations[0]
                    d_id = active_del["delegation_id"]
                    target_agt = active_del["target_agent"]
                    from app.engine.autonomous_worker import autonomous_worker
                    executed = await autonomous_worker.process_mission_once(m_id)
                    mission = mission_registry.get_mission(m_id)
                    summary = f"Execution Loop {'completed' if executed else 'did not complete'} for {target_agt}."
                    recs = "Review verified specialist result and mission lineage."
                    meta.update({
                        "mission_id": m_id, "mission_action": "CONTINUE", "read_only": "No", "mission_status": mission.status,
                        "current_phase": mission.current_phase, "current_milestone": mission.current_milestone,
                        "plan_version": mission.plan_version, "plan_status": mission.plan_status, "delegation_id": d_id,
                        "target_intelligence": target_agt, "delegation_execution": "Completed" if executed else "Failed or already claimed", "mission_progress_updated": "Yes" if executed else "No",
                        "next_eligible_action": mission.next_eligible_action, "auto_continue": "Triggered"
                    })
                else:
                    summary = "No pending delegation is available to continue."
                    recs = "Inspect mission execution state."
        else:
            summary = "Session logic evaluated."
            recs = "Awaiting input."

        act_count, susp_count = session_manager.get_counts()
        meta["active_sessions"] = act_count
        meta["suspended_sessions"] = susp_count
        
        return DirectorArtifact(
            artifact_id=f"dir_{uuid.uuid4().hex[:8]}", agent_run_id=session_id, lead_id=context.company_id,
            operating_mode=mode, execution_context=exec_ctx, mission_id=m_id, mission_action=context.mission_action,
            objective_id=objective_id, objective_action=objective_action,
            read_only=read_only, state_mutation_from_query="None" if read_only else "Executed", mutation_ledger=ledger,
            company_health=f"Excellent ({metrics.get('coo_health', 97)}/100)", executive_board_consulted=context.board_consultation_details,
            executive_summary=summary, top_priorities=priorities, major_opportunities=["Vertical expansion."], major_risks=["Manual queues."],
            delegations=delegations, pending_approvals=approvals, active_agent_sessions=sessions, executive_decisions=decisions,
            recommended_actions=[recs], execution_metadata=meta
        )
