"""
NapsterTec AI - Sales Intelligence Agent
Module: app/agent/definitions/sales_agent.py
"""
import logging
from typing import Dict, Any

from app.agent.base_agent import BaseAgent
from app.agent.agent_models import AgentMetadata, AgentContext, AgentResult, AgentPermission, AgentCapability

logger = logging.getLogger(__name__)

class SalesIntelligenceAgent(BaseAgent):
    def __init__(self, **kwargs):
        metadata = AgentMetadata(
            name="sales_intelligence",
            display_name="Sales Intelligence Director",
            description="Continuously evaluates sales opportunities, determines buying intent, and prepares deal closing packages.",
            version="1.0.0",
            category="sales_operations",
            capabilities={AgentCapability.RESEARCH},
            supported_task_types=["evaluate sales opportunity", "prioritize deals", "prepare meeting", "sales analysis"],
            allowed_tools={"sales_context_builder", "sales_evaluator", "sales_artifact_saver"},
            required_permissions={AgentPermission.READ, AgentPermission.WRITE} 
        )
        super().__init__(metadata=metadata, **kwargs)

    async def execute(self, context: AgentContext) -> AgentResult:
        result = AgentResult(success=False, agent_name=self.metadata.name, session_id=context.session_id)
        
        try:
            search_query = context.planner_output.get("query", context.task)
            b_res = await self.invoke_tool("sales_context_builder", {"query": search_query}, context)
            b_data = getattr(b_res["output"], "data", b_res["output"])
            if hasattr(b_data, "model_dump"): b_data = b_data.model_dump()
            if not isinstance(b_data, dict): b_data = {}
            
            if not b_data.get("found"):
                result.final_output = f"Task Failed: {b_data.get('error', 'Context missing.')}"
                return result
            iso_context = b_data["isolated_context"]

            e_res = await self.invoke_tool("sales_evaluator", {"context": iso_context}, context)
            e_data = getattr(e_res["output"], "data", e_res["output"])
            if hasattr(e_data, "model_dump"): e_data = e_data.model_dump()
            if not isinstance(e_data, dict): e_data = {}
            
            if not e_data or "artifact" not in e_data:
                result.final_output = "Task Failed: Evaluator failed to generate artifact."
                return result
            artifact = e_data["artifact"]

            s_res = await self.invoke_tool("sales_artifact_saver", {"artifact": artifact}, context)
            s_data = getattr(s_res["output"], "data", s_res["output"])
            if hasattr(s_data, "model_dump"): s_data = s_data.model_dump()
            if not isinstance(s_data, dict): s_data = {}

            if s_data.get("success") and s_data.get("registered"):
                result.success = True
                status_msg = "Completed"
            else:
                result.success = False
                status_msg = "Persistence Failed"

            meeting = artifact.get('meeting_preparation', {})

            summary = (
                f"**Sales Intelligence Execution Report**\n\n"
                f"Pipeline Stage: {artifact.get('pipeline_stage')}\n"
                f"Buying Intent: {artifact.get('buying_intent')} ({artifact.get('buying_intent_reasoning')})\n"
                f"Relationship Health: {artifact.get('relationship_health')}\n"
                f"Priority: {artifact.get('priority')}\n"
                f"Next Best Action: {artifact.get('next_action')} - {artifact.get('next_action_reasoning')}\n"
                f"Meeting Preparation Ready: Yes ({len(meeting.get('questions_to_ask', []))} Discovery Questions Prepared)\n"
                f"Estimated Deal Value: {artifact.get('estimated_deal_value')}\n\n"
                f"**Artifact Generation:**\n"
                f"Artifact Created: SalesArtifact\n"
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
            logger.error(f"[Sales Agent] Execution failed: {str(e)}", exc_info=True)
            result.errors.append(str(e))
            return result