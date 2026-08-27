"""
NapsterTec AI - Business Operations Intelligence Agent
Module: app/agent/definitions/business_operations_agent.py
"""
import logging
from typing import Dict, Any

from app.agent.base_agent import BaseAgent
from app.agent.agent_models import AgentMetadata, AgentContext, AgentResult, AgentPermission, AgentCapability

logger = logging.getLogger(__name__)

class BusinessOperationsIntelligenceAgent(BaseAgent):
    def __init__(self, **kwargs):
        metadata = AgentMetadata(
            name="business_operations_intelligence",
            display_name="Business Operations Director (AI COO)",
            description="Continuously monitors departments, agents, workflows, and KPIs to ensure optimal enterprise efficiency.",
            version="1.1.0",
            category="operations",
            # --- SPRINT 6B.2B HOTFIX: CANONICAL ORGANIZATION MAPPING ---
            department_id="operations_success",
            department_name="Operations & Success",
            # -----------------------------------------------------------
            capabilities={AgentCapability.RESEARCH},
            supported_task_types=["analyze operations", "monitor health", "department analysis", "bottlenecks", "coo report"],
            allowed_tools={"business_operations_context_builder", "business_operations_evaluator", "business_operations_artifact_saver"},
            required_permissions={AgentPermission.READ, AgentPermission.WRITE} 
        )
        super().__init__(metadata=metadata, **kwargs)

    async def execute(self, context: AgentContext) -> AgentResult:
        result = AgentResult(success=False, agent_name=self.metadata.name, session_id=context.session_id)
        
        try:
            search_query = context.planner_output.get("query", context.task)
            
            b_res = await self.invoke_tool("business_operations_context_builder", {"query": search_query}, context)
            b_data = getattr(b_res["output"], "data", b_res["output"])
            if hasattr(b_data, "model_dump"): b_data = b_data.model_dump()
            if not isinstance(b_data, dict): b_data = {}
            
            if not b_data.get("found"):
                result.final_output = f"Task Failed: {b_data.get('error', 'Context missing.')}"
                return result
            iso_context = b_data["isolated_context"]

            e_res = await self.invoke_tool("business_operations_evaluator", {"context": iso_context}, context)
            e_data = getattr(e_res["output"], "data", e_res["output"])
            if hasattr(e_data, "model_dump"): e_data = e_data.model_dump()
            if not isinstance(e_data, dict): e_data = {}
            
            if not e_data or "artifact" not in e_data:
                result.final_output = "Task Failed: Evaluator failed to generate artifact."
                return result
            artifact = e_data["artifact"]

            s_res = await self.invoke_tool("business_operations_artifact_saver", {"artifact": artifact}, context)
            s_data = getattr(s_res["output"], "data", s_res["output"])
            if hasattr(s_data, "model_dump"): s_data = s_data.model_dump()
            if not isinstance(s_data, dict): s_data = {}

            if s_data.get("success") and s_data.get("registered"):
                result.success = True
                status_msg = "Completed"
            else:
                result.success = False
                status_msg = "Persistence Failed"

            comp_health = artifact.get('company_health', {})
            dept_ranks = artifact.get('department_rankings', [])
            wf_ranks = artifact.get('workflow_rankings', [])
            kpis = artifact.get('kpis', {})
            risks = artifact.get('operational_risks', [])
            recs = artifact.get('executive_recommendations', [])
            
            top_dept = dept_ranks[0].get('department') if dept_ranks else "N/A"
            second_dept = dept_ranks[1].get('department') if len(dept_ranks) > 1 else "N/A"
            third_dept = dept_ranks[2].get('department') if len(dept_ranks) > 2 else "N/A"
            
            top_wf = wf_ranks[0].get('workflow_name') if wf_ranks else "N/A"
            second_wf = wf_ranks[1].get('workflow_name') if len(wf_ranks) > 1 else "N/A"
            third_wf = wf_ranks[2].get('workflow_name') if len(wf_ranks) > 2 else "N/A"
            
            main_rec = recs[0] if recs else {}

            summary = (
                f"**Business Operations Intelligence Execution Report**\n\n"
                f"Overall Company Health: {comp_health.get('status')}\n"
                f"Overall Company Score: {comp_health.get('score')}/100 ({comp_health.get('trend')})\n"
                f"Department Health: {len(artifact.get('departments', []))} Departments Evaluated\n"
                f"Department Rankings: #1 {top_dept}, #2 {second_dept}, #3 {third_dept}\n"
                f"Workflow Health: {len(artifact.get('workflows', []))} Workflows Evaluated\n"
                f"Workflow Rankings: #1 {top_wf}, #2 {second_wf}, #3 {third_wf}\n"
                f"Highest Performing Department: {top_dept}\n"
                f"Most Reliable Workflow: {top_wf}\n"
                f"Operational KPIs: {kpis.get('engineering_velocity')} Velocity | {kpis.get('deployments_this_week')} Deployments/Wk\n"
                f"Operational Risks: {len(risks)} Logged\n"
                f"Executive Recommendations: {main_rec.get('recommendation', 'None')} ({main_rec.get('priority', 'Low')} Priority)\n\n"
                f"**Artifact Generation:**\n"
                f"Artifact Created: BusinessOperationsArtifact\n"
                f"Artifact ID: {s_data.get('artifact_id', 'Unknown')}\n"
                f"Repository Saved: {'Yes' if s_data.get('success') else 'No'} (v{s_data.get('version', 0)})\n"
                f"Registry Registered: {'Yes' if s_data.get('registered') else 'No'}\n"
                f"Validation: {s_data.get('validation', 'Failed')}\n\n"
                f"Status: {status_msg}"
            )
            
            result.final_output = summary
            result.tool_calls.extend([b_res, e_res, s_res])
            return result

        except Exception as e:
            logger.error(f"[Business Operations Agent] Execution failed: {str(e)}", exc_info=True)
            result.errors.append(str(e))
            return result