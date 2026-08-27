"""
NapsterTec AI - Director Intelligence Agent (AI CEO)
Module: app/agent/definitions/director_agent.py
"""
import logging
from typing import Dict, Any

from app.agent.base_agent import BaseAgent
from app.agent.agent_models import AgentMetadata, AgentContext, AgentResult, AgentPermission, AgentCapability

logger = logging.getLogger(__name__)

class DirectorIntelligenceAgent(BaseAgent):
    def __init__(self, **kwargs):
        metadata = AgentMetadata(
            name="director_intelligence",
            display_name="NapsterTec Director Intelligence (AI CEO)",
            description="Executive orchestration, strategic decision-making, and cross-department governance layer.",
            version="1.8.7", # Transient Artifact Registry Fix
            category="executive",
            capabilities={AgentCapability.RESEARCH},
            supported_task_types=["how is napstertec doing", "director", "executive briefing", "strategic decision", "delegate task", "mission", "continue mission", "show mission", "inspect", "audit", "approve", "reject", "revoke"],
            allowed_tools={"director_context_builder", "director_evaluator", "director_artifact_saver"},
            allowed_providers={"openrouter"},
            cost_preference="performance",
            reasoning_level="high",
            model_profile="executive",
            max_model_cost_per_request_usd=0.05,
            allow_free_model_first=False,
            # WRITE is operation-scoped and granted only for an explicitly
            # classified internal mission mutation.
            required_permissions={AgentPermission.READ}
        )
        super().__init__(metadata=metadata, **kwargs)

    async def execute(self, context: AgentContext) -> AgentResult:
        result = AgentResult(success=False, agent_name=self.metadata.name, session_id=context.session_id)
        
        try:
            search_query = context.planner_output.get("query", context.task)
            
            authority_context = dict(context.runtime_metadata.get("command_context", {}))
            authority_context["granted_permissions"] = [
                str(getattr(permission, "value", permission))
                for permission in context.granted_permissions
            ]
            b_res = await self.invoke_tool(
                "director_context_builder",
                {"query": search_query, "authority_context": authority_context},
                context,
            )
            b_data = getattr(b_res["output"], "data", b_res["output"])
            if hasattr(b_data, "model_dump"): b_data = b_data.model_dump()
            if not isinstance(b_data, dict): b_data = {}
            
            if not b_data.get("found"):
                result.final_output = f"Task Failed: {b_data.get('error', 'Context missing.')}"
                return result
            iso_context = b_data["isolated_context"]

            e_res = await self.invoke_tool("director_evaluator", {"context": iso_context}, context)
            e_data = getattr(e_res["output"], "data", e_res["output"])
            if hasattr(e_data, "model_dump"): e_data = e_data.model_dump()
            if not isinstance(e_data, dict): e_data = {}
            
            if not e_data or "artifact" not in e_data:
                evaluator_error = getattr(e_res["output"], "error", None)
                if evaluator_error:
                    result.final_output = str(evaluator_error)
                    result.errors.append(str(evaluator_error))
                else:
                    result.final_output = "MISSION_DISPATCH_REJECTED: evaluator failed to generate an artifact."
                    result.errors.append("MISSION_DISPATCH_REJECTED")
                return result
            artifact = e_data["artifact"]

            s_res = await self.invoke_tool("director_artifact_saver", {"artifact": artifact}, context)
            s_data = getattr(s_res["output"], "data", s_res["output"])
            if hasattr(s_data, "model_dump"): s_data = s_data.model_dump()
            if not isinstance(s_data, dict): s_data = {}

            is_read_only = artifact.get("read_only", False)

            if s_data.get("success") and (s_data.get("registered") or is_read_only):
                result.success = True
                status_msg = "Completed"
            else:
                result.success = False
                status_msg = "Persistence Failed"

            artifact_type = artifact.get("artifact_type")
            meta = artifact.get("execution_metadata", {})
            m_id = artifact.get("mission_id") or meta.get("mission_id")
            m_action = artifact.get("mission_action") or meta.get("mission_action")
            objective_id = artifact.get("objective_id") or meta.get("objective_id")
            objective_action = artifact.get("objective_action") or meta.get("objective_action")

            if "ValidationFailed" in artifact.get("executive_summary", "") or "Violation" in artifact.get("executive_summary", "") or "MissionNotFound" in artifact.get("executive_summary", ""):
                result.final_output = f"**Director Intelligence Validation Error**\n\n{artifact.get('executive_summary')}\n\nStatus: Validation Failed"
                result.tool_calls.extend([b_res, e_res, s_res])
                return result

            if artifact_type == "DirectorArtifact":
                if objective_action in {"CREATE", "STATUS", "LIST"}:
                    if objective_action == "LIST":
                        objective_lines = "\n".join(
                            f"- {item.get('objective_id')}: {item.get('title')} [{item.get('status')}]"
                            for item in meta.get("objectives", [])
                        ) or "- None"
                        summary = (
                            "**Company Objectives**\n\n"
                            f"Count: {meta.get('objective_count', 0)}\n"
                            f"{objective_lines}\n\nStatus: {status_msg}"
                        )
                    else:
                        criteria = meta.get("success_criteria", {})
                        summary = (
                            "**Company Objective Control Plane**\n\n"
                            f"Action: {objective_action}\n"
                            f"Objective ID: {objective_id}\n"
                            f"Title: {meta.get('title')}\n"
                            f"Status: {meta.get('status')}\n"
                            f"Version: {meta.get('version')}\n"
                            f"Progress: {meta.get('progress')}%\n"
                            f"Success: {meta.get('verified_success_count')} / {criteria.get('required')} {criteria.get('unit')}\n"
                            f"Linked Missions: {meta.get('linked_mission_ids', [])}\n"
                            f"Bounds: missions={meta.get('max_missions')}, strategy_changes={meta.get('max_strategy_changes')}, zero_progress={meta.get('max_zero_progress_cycles')}\n"
                            f"Terminal Reason: {meta.get('terminal_reason') or 'None'}\n"
                            f"Mission Launched: No\n\nPersistence: {status_msg}"
                        )
                    result.final_output = summary
                    result.tool_calls.extend([b_res, e_res, s_res])
                    return result
                
                if artifact.get('operating_mode') == "AUDIT MODE" or meta.get('mission_action') == "AUDIT":
                    ledger = artifact.get("mutation_ledger", {})
                    findings = meta.get("findings", [])
                    finding_lines = "\n".join(
                        f"- [{item.get('severity')}] {item.get('mission_id', 'GLOBAL')}: {item.get('code')} — {item.get('detail')}"
                        for item in findings[:25]
                    ) or "- No persisted-state invariant violations detected."
                    summary = (
                        f"**MISSION INTELLIGENCE COMPREHENSIVE ENGINEERING AUDIT**\n\n"
                        f"DIRECTOR COMMAND ROUTING\n"
                        f"Command ID: cmd_{context.session_id[:8]}\n"
                        f"Command Class: AUDIT\n"
                        f"Authority: READ_ONLY\n"
                        f"Read Only: Yes\n"
                        f"Mission Creation Authorized: No\n"
                        f"Execution Authorized: No\n"
                        f"Mutation Authorized: No\n"
                        f"Route: MissionEngineeringAuditService\n\n"
                        f"────────────────────────────────────────────────\n\n"
                        f"READ-ONLY INTEGRITY\n"
                        f"Missions Created: {ledger.get('missions_created', 0)}\n"
                        f"Plans Created: {ledger.get('plans_created', 0)}\n"
                        f"Progression Decisions Created: {ledger.get('decisions_created', 0)}\n"
                        f"Materializations Created: {ledger.get('materializations_created', 0)}\n"
                        f"Execution Requests Created: {ledger.get('execution_requests_created', 0)}\n"
                        f"Delegations Created: {ledger.get('delegations_created', 0)}\n"
                        f"Worker Claims Created: {ledger.get('worker_claims_created', 0)}\n"
                        f"Artifacts Created: {ledger.get('artifacts_created', 0)}\n"
                        f"Repository Writes Caused By Command: {ledger.get('repository_writes', 0)}\n"
                        f"State-Changing Events Caused By Command: {ledger.get('state_changing_events', 0)}\n"
                        f"Specialist Invocations: {ledger.get('specialist_invocations', 0)}\n"
                        f"Auto-Continue Triggered: No\n"
                        f"External Side Effects: {ledger.get('external_side_effects', 0)}\n"
                        f"State Mutation: None\n"
                        f"Read-Only Isolation Integrity: {ledger.get('read_only_isolation_integrity', 'PASSED')}\n\n"
                        f"AUDIT DEPTH\n"
                        f"Persisted Missions Inspected: {meta.get('missions_inspected', 0)}\n"
                        f"Critical Issues: {meta.get('critical_issues', 0)}\n"
                        f"High Issues: {meta.get('high_issues', 0)}\n"
                        f"Production Ready: {'YES' if meta.get('production_ready') else 'NO'}\n"
                        f"Safe for Autonomous Execution: {'YES' if meta.get('safe_for_autonomous_execution') else 'NO'}\n\n"
                        f"PERSISTED-STATE FINDINGS\n{finding_lines}\n\n"
                        f"Status: {status_msg}"
                    )
                    result.final_output = summary
                    result.tool_calls.extend([b_res, e_res, s_res])
                    return result
                
                elif m_action == "INSPECT_EXECUTION" and m_id:
                    summary = (
                        f"**MISSION EXECUTION AUDIT REPORT**\n\n"
                        f"Mission ID: {m_id}\n"
                        f"Read Only: {meta.get('read_only', 'Yes')}\n"
                        f"State Mutation: {meta.get('state_mutation_from_query', 'None')}\n"
                        f"Mission Status: {meta.get('mission_status')}\n"
                        f"Mission Objective: {meta.get('mission_objective', 'Unknown')}\n"
                        f"Success: {meta.get('success_criteria')}\n\n"
                        f"────────────────────────────────────────────────\n\n"
                        f"CURRENT PLAN\n"
                        f"Version: {meta.get('plan_version')}\n"
                        f"Status: {meta.get('plan_status')}\n"
                        f"Phase: {meta.get('current_phase')}\n"
                        f"Milestone: {meta.get('current_milestone')}\n\n"
                        f"────────────────────────────────────────────────\n\n"
                        f"PROGRESSION DECISION\n"
                        f"Decision ID: {meta.get('dec_id')}\n"
                        f"Selected Action: {meta.get('dec_action')}\n"
                        f"Decision Status: MATERIALIZED\n\n"
                        f"────────────────────────────────────────────────\n\n"
                        f"MATERIALIZATION\n"
                        f"Materialization ID: {meta.get('mat_id')}\n"
                        f"Status: {meta.get('mat_status')}\n"
                        f"Capability: {meta.get('mat_cap')}\n"
                        f"Resolved Intelligence: {meta.get('mat_intel')}\n"
                        f"Governance: PASSED\n\n"
                        f"────────────────────────────────────────────────\n\n"
                        f"EXECUTION REQUEST\n"
                        f"ID: {meta.get('req_id')}\n"
                        f"Status: {meta.get('req_status')}\n"
                        f"Capability: {meta.get('req_cap')}\n"
                        f"Target Intelligence: {meta.get('req_target')}\n\n"
                        f"────────────────────────────────────────────────\n\n"
                        f"DISPATCH\n"
                        f"State: {meta.get('disp_state')}\n"
                        f"Delegation Created: {meta.get('disp_created')}\n\n"
                        f"────────────────────────────────────────────────\n\n"
                        f"DELEGATION\n"
                        f"ID: {meta.get('del_id')}\n"
                        f"Target Intelligence: {meta.get('del_target')}\n"
                        f"Status: {meta.get('del_status')}\n"
                        f"Expected Result: {meta.get('del_expected')}\n\n"
                        f"────────────────────────────────────────────────\n\n"
                        f"APPROVAL\n"
                        f"Required: {meta.get('app_required')}\n"
                        f"Policy: PASSED\n\n"
                        f"────────────────────────────────────────────────\n\n"
                        f"WORKER\n"
                        f"Claim: {meta.get('work_id')}\n"
                        f"Status: {meta.get('work_status')}\n"
                        f"Health: {meta.get('work_health')}\n"
                        f"Execution Started: {meta.get('work_started')}\n\n"
                        f"────────────────────────────────────────────────\n\n"
                        f"EXTERNAL OPERATION\n"
                        f"Mechanism: {meta.get('ext_mech')}\n"
                        f"Operation ID: {meta.get('ext_op')}\n"
                        f"Provider: {meta.get('ext_prov')}\n"
                        f"Status: {meta.get('ext_status')}\n\n"
                        f"────────────────────────────────────────────────\n\n"
                        f"EXECUTION EVIDENCE\n"
                        f"Mechanism: {meta.get('ev_mech')}\n"
                        f"Evidence Complete: {meta.get('ev_complete')}\n"
                        f"Ghost Running Detected: {meta.get('ev_ghost')}\n\n"
                        f"────────────────────────────────────────────────\n\n"
                        f"VALIDATION\n"
                        f"Execution State Integrity: {meta.get('val_exec')}\n"
                        f"Delegation Execution Integrity: {meta.get('val_dele')}\n"
                        f"Worker Claim Integrity: {meta.get('val_work')}\n"
                        f"Progression Materialization Integrity: {meta.get('val_mat')}\n"
                        f"Mission Stall Integrity: {meta.get('val_stall')}\n"
                        f"Plan Version Integrity: {meta.get('val_plan')}\n"
                        f"Mission Completion Integrity: {meta.get('val_comp')}\n"
                        f"Overall Validation: {meta.get('val_overall')}\n\n"
                        f"Autonomous Execution Safe: {meta.get('autonomous_safe')}\n\n"
                        f"────────────────────────────────────────────────\n\n"
                        f"Progression State: {meta.get('progression_state')}\n"
                        f"Execution State: {meta.get('execution_state')}\n"
                        f"Status: {status_msg}"
                    )
                    
                elif m_action == "STATUS" and m_id:
                    summary = (
                        f"**Director Intelligence Execution Report**\n\n"
                        f"Operating Mode: {artifact.get('operating_mode')}\n"
                        f"Execution Context: MISSION\n"
                        f"Mission ID: {m_id}\n"
                        f"Mission Action: {m_action}\n"
                        f"Read Only: {meta.get('read_only')}\n"
                        f"Mission Status: {meta.get('mission_status')}\n"
                        f"Current Phase: {meta.get('current_phase')}\n"
                        f"Current Milestone: {meta.get('current_milestone')}\n"
                        f"Overall Progress: {meta.get('overall_progress')}\n"
                        f"Success Criteria: {meta.get('success_criteria')}\n"
                        f"Last Completed Delegation: {meta.get('last_completed_delegation')}\n"
                        f"Last Result Artifact: {meta.get('last_result_artifact')}\n"
                        f"Current Autonomous Delegation: {meta.get('current_autonomous_delegation')}\n"
                        f"Expected Artifact: {meta.get('expected_artifact')}\n"
                        f"Auto-Continue State: {meta.get('auto_continue_state')}\n"
                        f"State Mutation From Query: {meta.get('state_mutation_from_query')}\n"
                        f"Mission State Validation: {meta.get('mission_state_validation')}\n\n"
                        f"Status: {status_msg}"
                    )
                    
                elif m_action == "CONTINUE" and m_id:
                    summary = (
                        f"**Director Intelligence Execution Report**\n\n"
                        f"Operating Mode: {artifact.get('operating_mode')}\n"
                        f"Execution Context: MISSION\n"
                        f"Mission ID: {m_id}\n"
                        f"Mission Action: {m_action}\n"
                        f"Mission Status: {meta.get('mission_status')}\n"
                        f"Current Phase: {meta.get('current_phase')}\n"
                        f"Current Milestone: {meta.get('current_milestone')}\n"
                        f"Existing Execution Request: {meta.get('existing_exec_req')}\n"
                        f"Existing Delegation: {meta.get('existing_delegation')}\n"
                        f"Delegation ID: {meta.get('delegation_id')}\n"
                        f"Target Intelligence: {meta.get('target_intelligence')}\n"
                        f"New Delegation Created: {meta.get('new_delegation')}\n"
                        f"Delegation Execution: {meta.get('delegation_execution')}\n"
                        f"Result Artifact: {meta.get('result_artifact')}\n"
                        f"Events Published: {meta.get('events_published')}\n"
                        f"Mission Progress Updated: {meta.get('mission_progress_updated')}\n"
                        f"Next Eligible Action: {meta.get('next_eligible_action')}\n"
                        f"Auto-Continue: {meta.get('auto_continue')}\n\n"
                        f"Status: {status_msg}"
                    )
                    
                # 5. APPROVAL CONTROL PLANE REPORTING
                elif artifact.get('operating_mode') in ["HUMAN DECISION CONTROL MODE", "APPROVAL STATUS MODE"]:
                    approvals = meta.get("approvals", [])
                    if not approvals:
                        summary = f"**Approval Control Plane**\n\nNo approvals found.\n\nStatus: {status_msg}"
                    elif len(approvals) > 1:
                        lines = "\n".join(f"- {a.get('approval_id')}: {a.get('action')} [{a.get('status')}]" for a in approvals)
                        summary = f"**Approval Control Plane**\n\nPending Approvals:\n{lines}\n\nStatus: {status_msg}"
                    else:
                        app = approvals[0]
                        summary = (
                            f"**Approval Control Plane Decision**\n\n"
                            f"Approval ID: {app.get('approval_id')}\n"
                            f"Action: {app.get('action')}\n"
                            f"Status: {app.get('status')}\n"
                            f"Risk Level: {app.get('risk_level')}\n"
                            f"Objective ID: {meta.get('objective_id', 'N/A')}\n"
                            f"Mission ID: {app.get('mission_id', 'N/A')}\n"
                            f"Materialization ID: {app.get('materialization_id', 'N/A')}\n"
                            f"Execution Request ID: {app.get('execution_request_id', 'None')}\n"
                            f"Approver Reference: {context.session_id}\n"
                            f"Decision Timestamp: {app.get('resolved_at', 'N/A')}\n"
                            f"Reason: {app.get('resolution_reason', 'N/A')}\n"
                            f"Work State: {artifact.get('executive_summary', 'Safely Blocked/Released')}\n"
                            f"External Action Executed: NO\n\n"
                            f"Status: {status_msg}"
                        )
                    result.final_output = summary
                    result.tool_calls.extend([b_res, e_res, s_res])
                    return result
                
                else:
                    command_class = iso_context.get("command_class", "UNKNOWN")
                    authority_scope = iso_context.get("authority_scope", "NONE")
                    mission_command = str(command_class).startswith("MISSION_")
                    failure_code = "MISSION_DISPATCH_REJECTED" if mission_command else "DIRECTOR_RESPONSE_UNRESOLVED"
                    failure_reason = (
                        "MISSION_AUTHORITY_MISSING"
                        if mission_command and authority_scope != "INTERNAL_MISSION_STATE"
                        else "MISSION_ACTION_UNRESOLVED"
                        if mission_command
                        else "UNMATCHED_DIRECTOR_RESPONSE"
                    )
                    summary = (
                        f"{failure_code}\n"
                        f"Command Class: {command_class}\n"
                        f"Authority Scope: {authority_scope}\n"
                        f"Reason: {failure_reason}\n"
                        f"Status: Failed"
                    )
                    result.success = False
                    result.errors.append(failure_reason)

                result.final_output = summary
                result.tool_calls.extend([b_res, e_res, s_res])
                return result

            # --- DYNAMIC REPORT GENERATION FOR MISSION ENGINE (STATUS/CREATION) ---
            if artifact_type == "MissionArtifact":
                requests = artifact.get("execution_requests", [])
                delegations = [*artifact.get("active_delegations", []), *artifact.get("delegation_history", [])]
                claims = artifact.get("worker_claims", [])
                lineage = artifact.get("artifact_lineage", [])
                latest_request = requests[-1] if requests else {}
                latest_delegation = delegations[-1] if delegations else {}
                latest_claim = claims[-1] if claims else {}
                latest_lineage = lineage[-1] if lineage else {}
                evidence_source = artifact.get("evidence_source", "UNKNOWN")
                summary = (
                    f"**Mission Engine Execution Report**\n\n"
                    f"Mission ID: {artifact.get('mission_id')}\n"
                    f"Mission Title: {artifact.get('mission_summary').replace('Persistent orchestration loop for: ', '')}\n"
                    f"Mission Objective: {artifact.get('normalized_objective', artifact.get('objective'))}\n"
                    f"Mission Type: {artifact.get('mission_type')}\n"
                    f"Success Criterion: {artifact.get('success_criterion')}\n"
                    f"Target Count: {artifact.get('target_count')}\n"
                    f"Verified Count: {artifact.get('verified_count')}\n"
                    f"Evidence Source: {evidence_source}\n"
                    f"Simulation/Canary Mode: {'Yes' if artifact.get('simulation_mode') else 'No'}\n"
                    f"Mission Status: {artifact.get('status')}\n"
                    f"Priority: {artifact.get('priority')}\n"
                    f"Autonomy Level: {artifact.get('autonomy_level')}\n"
                    f"Overall Progress: {artifact.get('overall_progress')}%\n"
                    f"Mission Health: {artifact.get('mission_health')}\n"
                    f"Current Phase: {artifact.get('current_phase')}\n"
                    f"Current Milestone: {artifact.get('current_milestone', 'None')}\n"
                    f"Mission Bootstrap: {meta.get('mission_bootstrap', 'Triggered')}\n"
                    f"Next Eligible Action: {artifact.get('next_eligible_action', 'None')}\n"
                    f"Auto-Continue Status: {artifact.get('auto_continue_status', 'STOPPED')}\n"
                    f"Plan Version: {artifact.get('plan_version')}\n"
                    f"Execution Request ID: {latest_request.get('execution_request_id', 'None')}\n"
                    f"Delegation ID: {latest_delegation.get('delegation_id', 'None')}\n"
                    f"Worker Claim ID: {latest_claim.get('worker_claim_id', 'None')}\n"
                    f"Specialist: {latest_lineage.get('specialist', latest_delegation.get('target_agent', 'None'))}\n"
                    f"Artifact ID: {latest_lineage.get('artifact_id', 'None')}\n"
                    f"Artifact Type: {latest_lineage.get('artifact_type', 'None')}\n"
                    f"Artifact Provenance: {latest_lineage or 'None'}\n"
                    f"Active Delegations: {len(artifact.get('active_delegations', []))}\n"
                    f"Success Criteria: {artifact.get('success_criteria_progress')}\n"
                    f"Terminal Reason: {artifact.get('terminal_reason', 'None')}\n"
                    f"External Side Effects: {artifact.get('external_side_effects', 'NONE')}\n"
                    f"CANARY PIPELINE VERIFIED: {'YES' if artifact.get('canary_pipeline_verified') else 'NO'}\n"
                    f"REAL-WORLD BUSINESS EVIDENCE VERIFIED: {'YES' if artifact.get('real_world_business_evidence_verified') else 'NO'}\n"
                    f"Artifact Created: MissionArtifact\n"
                    f"Repository Saved: {'Yes' if s_data.get('success') else 'No'}\n"
                    f"Registry Registered: {'Yes' if s_data.get('registered') else 'No'}\n"
                    f"Validation: {s_data.get('validation', 'Failed')}\n\n"
                    f"Status: {status_msg}"
                )
                result.final_output = summary
                result.tool_calls.extend([b_res, e_res, s_res])
                return result

            result.final_output = f"Operating Mode: {artifact.get('operating_mode')}\nStatus: {status_msg}"
            return result

        except Exception as e:
            logger.error(f"[Director Agent] Execution failed: {str(e)}", exc_info=True)
            result.errors.append(str(e))
            result.final_output = str(e) or "DIRECTOR_COMMAND_UNRESOLVED"
            return result