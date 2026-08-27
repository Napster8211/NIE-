"""
NapsterTec AI - Customer Success Intelligence Agent
Module: app/agent/definitions/customer_success_agent.py
"""
import logging
from typing import Dict, Any

from app.agent.base_agent import BaseAgent
from app.agent.agent_models import AgentMetadata, AgentContext, AgentResult, AgentPermission, AgentCapability

logger = logging.getLogger(__name__)

class CustomerSuccessIntelligenceAgent(BaseAgent):
    def __init__(self, **kwargs):
        metadata = AgentMetadata(
            name="customer_success_intelligence",
            display_name="Customer Success Intelligence Director",
            description="Manages customer lifecycle, onboarding, health scoring, and expansion opportunities post-sale.",
            version="1.0.0",
            category="customer_success",
            department_id="operations_success",      # CANONICAL TAXONOMY
            department_name="Operations & Success",  # CANONICAL TAXONOMY
            capabilities={AgentCapability.RESEARCH},
            supported_task_types=["evaluate customer success", "check onboarding", "predict churn", "health score"],
            allowed_tools={"customer_success_context_builder", "customer_success_evaluator", "customer_success_artifact_saver"},
            required_permissions={AgentPermission.READ, AgentPermission.WRITE} 
        )
        super().__init__(metadata=metadata, **kwargs)

    async def execute(self, context: AgentContext) -> AgentResult:
        result = AgentResult(success=False, agent_name=self.metadata.name, session_id=context.session_id)
        
        try:
            search_query = context.planner_output.get("query", context.task)
            
            b_res = await self.invoke_tool("customer_success_context_builder", {"query": search_query}, context)
            b_data = getattr(b_res["output"], "data", b_res["output"])
            if hasattr(b_data, "model_dump"): b_data = b_data.model_dump()
            if not isinstance(b_data, dict): b_data = {}
            
            if not b_data.get("found"):
                result.final_output = f"Task Failed: {b_data.get('error', 'Context missing.')}"
                return result
            iso_context = b_data["isolated_context"]

            e_res = await self.invoke_tool("customer_success_evaluator", {"context": iso_context}, context)
            e_data = getattr(e_res["output"], "data", e_res["output"])
            if hasattr(e_data, "model_dump"): e_data = e_data.model_dump()
            if not isinstance(e_data, dict): e_data = {}
            
            if not e_data or "artifact" not in e_data:
                result.final_output = "Task Failed: Evaluator failed to generate artifact."
                return result
            artifact = e_data["artifact"]

            s_res = await self.invoke_tool("customer_success_artifact_saver", {"artifact": artifact}, context)
            s_data = getattr(s_res["output"], "data", s_res["output"])
            if hasattr(s_data, "model_dump"): s_data = s_data.model_dump()
            if not isinstance(s_data, dict): s_data = {}

            if s_data.get("success") and s_data.get("registered"):
                result.success = True
                status_msg = "Completed"
            else:
                result.success = False
                status_msg = "Persistence Failed"

            health = artifact.get('health_score', {})
            onboarding = artifact.get('onboarding_status', {})
            churn = artifact.get('churn_risk', {})
            expansions = artifact.get('expansion_opportunities', [])

            summary = (
                f"**Customer Success Intelligence Execution Report**\n\n"
                f"Customer: {iso_context.get('business_name')}\n"
                f"Health Score: {health.get('score')}/100 ({health.get('engagement_trend')})\n"
                f"Onboarding Status: {onboarding.get('status')}\n"
                f"Renewal Probability: {health.get('renewal_likelihood')}\n"
                f"Churn Risk: {churn.get('level')}\n"
                f"Expansion Opportunities: {len(expansions)} Detected (Top: {expansions[0].get('recommendation') if expansions else 'None'})\n"
                f"Recommended Next Action: {artifact.get('recommended_actions', ['None'])[0]}\n\n"
                f"**Artifact Generation:**\n"
                f"Artifact Created: CustomerSuccessArtifact\n"
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
            logger.error(f"[Customer Success Agent] Execution failed: {str(e)}", exc_info=True)
            result.errors.append(str(e))
            return result