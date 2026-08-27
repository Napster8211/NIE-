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
        summary = ""
        recs = ""

        if objective_id:
            from app.services.finance_engine import FinanceEngine
            snap = FinanceEngine().generate_snapshot(objective_id)
            assess = FinanceEngine().assess_finances(snap)
            metrics['financial_status'] = assess.financial_status
            metrics['financial_risk'] = assess.risk_level
            
            if assess.financial_status == "EXHAUSTED":
                recs += " PAUSE_COST_BEARING_WORK. REQUEST_BUDGET."
            elif assess.financial_status == "WARNING":
                recs += " CONTINUE_WITH_CAUTION. Review upcoming mission costs."
            elif assess.financial_status == "OVER_BUDGET":
                recs += " ESCALATE_TO_HUMAN. Budget limits exceeded."

        # SPRINT 5A: STRATEGIC PLANNING ROUTING
        if command_class == "STRATEGY_DEVELOP" or (mode == "STRATEGIC DECISION MODE" and objective_id and not m_id):
            from app.services.director_strategy import DirectorStrategyService
            
            objective = self.objective_service.repository.get(objective_id)
            if not objective:
                raise ValueError(f"OBJECTIVE_NOT_FOUND: {objective_id}")
                
            strategy_service = DirectorStrategyService()
            plan = strategy_service.develop_strategy(objective)
            
            summary = (
                f"**Strategic Plan Generated**\n\n"
                f"Objective: {objective.title}\n"
                f"Status: {plan.status.value}\n"
                f"Readiness: {plan.execution_readiness}\n"
                f"Workstreams: {len(plan.workstreams)}\n"
                f"Departments Assigned: {len(plan.department_assignments)}\n"
            )
            if plan.clarification_questions:
                summary += f"\nQuestions:\n- " + "\n- ".join(plan.clarification_questions)
                
            meta.update({"strategic_plan": plan.model_dump(mode="json")})
            
            return DirectorArtifact(
                artifact_id=f"dir_{uuid.uuid4().hex[:8]}", agent_run_id=session_id, lead_id=context.company_id,
                operating_mode=mode, execution_context=exec_ctx, mission_id=m_id, mission_action=context.mission_action,
                objective_id=objective_id, objective_action="STRATEGY_DEVELOP",
                read_only=True, state_mutation_from_query="None", mutation_ledger=ledger,
                company_health=metrics.get('financial_status', 'NOT_CONFIGURED'), executive_board_consulted=[],
                executive_summary=summary, top_priorities=[], major_opportunities=[], major_risks=[],
                delegations=[], pending_approvals=[], active_agent_sessions=[], executive_decisions=[],
                recommended_actions=["Review Strategic Plan"], execution_metadata=meta
            )

        # SPRINT 5B: PORTFOLIO MATERIALIZATION ROUTING
        if command_class == "PORTFOLIO_MATERIALIZE" or (mode == "STRATEGIC DECISION MODE" and objective_id and metrics.get("strategic_plan")):
            from app.services.mission_portfolio_service import MissionPortfolioService
            from app.repositories.strategic_plan_repository import strategic_plan_repository
            
            objective = self.objective_service.repository.get(objective_id)
            plan_id = metrics.get("strategic_plan", {}).get("strategic_plan_id")
            plan = strategic_plan_repository.get(plan_id) if plan_id else None
            
            if not objective:
                raise ValueError(f"OBJECTIVE_NOT_FOUND: {objective_id}")
            if not plan:
                raise ValueError(f"STRATEGIC_PLAN_NOT_FOUND")
                
            port_service = MissionPortfolioService()
            portfolio = port_service.materialize_portfolio(plan, objective)
            
            summary = (
                f"**Mission Portfolio Materialized**\n\n"
                f"Objective: {objective.title}\n"
                f"Portfolio ID: {portfolio.portfolio_id}\n"
                f"Status: {portfolio.status.value}\n"
                f"Mission Count: {len(portfolio.mission_definitions)}\n"
                f"Execution Groups: {len(portfolio.execution_groups)}\n"
            )
            if portfolio.blocking_reasons:
                summary += f"\nBlockers:\n- " + "\n- ".join(portfolio.blocking_reasons)
                
            meta.update({"mission_portfolio": portfolio.model_dump(mode="json")})
            
            return DirectorArtifact(
                artifact_id=f"dir_{uuid.uuid4().hex[:8]}", agent_run_id=session_id, lead_id=context.company_id,
                operating_mode=mode, execution_context=exec_ctx, mission_id=m_id, mission_action=context.mission_action,
                objective_id=objective_id, objective_action="PORTFOLIO_MATERIALIZE",
                read_only=True, state_mutation_from_query="None", mutation_ledger=ledger,
                company_health=metrics.get('financial_status', 'NOT_CONFIGURED'), executive_board_consulted=[],
                executive_summary=summary, top_priorities=[], major_opportunities=[], major_risks=[],
                delegations=[], pending_approvals=[], active_agent_sessions=[], executive_decisions=[],
                recommended_actions=["Review Mission Portfolio"], execution_metadata=meta
            )

        # SPRINT 5C: EXECUTIVE STRATEGY LOOP ROUTING
        if command_class == "EXECUTIVE_EVALUATION" or (mode == "STRATEGIC DECISION MODE" and m_id and metrics.get("mission_terminal_state")):
            from app.services.executive_strategy_service import ExecutiveStrategyService
            
            terminal_state = metrics.get("mission_terminal_state")
            terminal_def_id = metrics.get("mission_definition_id", "mdef_unknown")
            portfolio_id = metrics.get("portfolio_id", "port_unknown")
            evidence_refs = metrics.get("evidence_refs", [])
            success_met = terminal_state == "COMPLETED"
            
            exec_service = ExecutiveStrategyService()
            evaluation = exec_service.evaluate_portfolio_outcome(
                objective_id=objective_id,
                portfolio_id=portfolio_id,
                terminal_mission_id=m_id,
                terminal_definition_id=terminal_def_id,
                verified_outcome=terminal_state,
                evidence_refs=evidence_refs,
                success_criteria_met=success_met
            )
            
            decision = exec_service.generate_decision(evaluation)
            
            summary = (
                f"**Executive Strategy Cycle Complete**\n\n"
                f"Objective: {objective_id}\n"
                f"Terminal Mission: {m_id}\n"
                f"Outcome: {terminal_state}\n"
                f"Progress Delta: {evaluation.progress_delta}%\n"
                f"Strategy Effectiveness: {evaluation.strategy_effectiveness}\n"
                f"Recommendation: {evaluation.recommendation}\n"
                f"Reason: {', '.join(evaluation.reason_codes)}\n"
            )
            
            meta.update({
                "executive_evaluation": evaluation.model_dump(mode="json"),
                "executive_decision": decision.model_dump(mode="json")
            })
            
            return DirectorArtifact(
                artifact_id=f"dir_{uuid.uuid4().hex[:8]}", agent_run_id=session_id, lead_id=context.company_id,
                operating_mode=mode, execution_context=exec_ctx, mission_id=m_id, mission_action=context.mission_action,
                objective_id=objective_id, objective_action="EXECUTIVE_EVALUATION",
                read_only=True, state_mutation_from_query="None", mutation_ledger=ledger,
                company_health=metrics.get('financial_status', 'NOT_CONFIGURED'), executive_board_consulted=[],
                executive_summary=summary, top_priorities=[], major_opportunities=[], major_risks=[],
                delegations=[], pending_approvals=[], active_agent_sessions=[], executive_decisions=[decision],
                recommended_actions=[f"Execute {evaluation.recommendation}"], execution_metadata=meta
            )

        if command_class == "UNKNOWN":
            raise ValueError(DIRECTOR_COMMAND_UNRESOLVED)

        # ... (Keep existing code from `OBJECTIVE_CREATE` onward exact)
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
            company_health=metrics.get('financial_status', 'NOT_CONFIGURED'), executive_board_consulted=context.board_consultation_details,
            executive_summary=summary, top_priorities=priorities, major_opportunities=["Vertical expansion."], major_risks=["Manual queues."],
            delegations=delegations, pending_approvals=approvals, active_agent_sessions=sessions, executive_decisions=decisions,
            recommended_actions=[recs], execution_metadata=meta
        )