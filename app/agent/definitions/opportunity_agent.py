"""
NapsterTec AI - Opportunity Intelligence Agent
Module: app/agent/definitions/opportunity_agent.py
"""
import logging
from typing import Dict, Any

from app.agent.base_agent import BaseAgent
from app.agent.agent_models import AgentMetadata, AgentContext, AgentResult, AgentPermission, AgentCapability
from app.schemas.shared_artifacts import OpportunityAgentContext

logger = logging.getLogger(__name__)

class OpportunityIntelligenceAgent(BaseAgent):
    def __init__(self, **kwargs):
        metadata = AgentMetadata(
            name="opportunity_intelligence",
            display_name="Opportunity Intelligence Director",
            description="Transforms verified intelligence into structured business opportunities and service mappings.",
            version="1.1.0",
            category="business_intelligence",
            capabilities={AgentCapability.RESEARCH},
            supported_task_types=["evaluate opportunity", "score lead", "business opportunity"],
            allowed_tools={"opportunity_context_builder", "opportunity_evaluator", "opportunity_artifact_saver"},
            required_permissions={AgentPermission.READ, AgentPermission.WRITE} 
        )
        super().__init__(metadata=metadata, **kwargs)

    async def execute(self, context: AgentContext) -> AgentResult:
        result = AgentResult(success=False, agent_name=self.metadata.name, session_id=context.session_id)
        
        try:
            # 1. Build Isolated Context
            search_query = context.planner_output.get("query", context.task)
            b_res = await self.invoke_tool("opportunity_context_builder", {"query": search_query}, context)
            b_data = getattr(b_res["output"], "data", b_res["output"])
            if hasattr(b_data, "model_dump"): b_data = b_data.model_dump()
            
            if not b_data.get("found"):
                result.final_output = f"Task Failed: {b_data.get('error', 'Lead or Website Artifact missing.')}"
                return result
            
            iso_context = b_data["isolated_context"]

            # 2. Evaluate (Deterministic)
            e_res = await self.invoke_tool("opportunity_evaluator", {"context": iso_context}, context)
            e_data = getattr(e_res["output"], "data", e_res["output"])
            if hasattr(e_data, "model_dump"): e_data = e_data.model_dump()
            
            artifact = e_data["artifact"]

            # 3. Save & Register Artifact
            s_res = await self.invoke_tool("opportunity_artifact_saver", {"artifact": artifact}, context)
            s_data = getattr(s_res["output"], "data", s_res["output"])
            if hasattr(s_data, "model_dump"): s_data = s_data.model_dump()

            # 4. Strict Execution Grounding
            stats = s_data
            if stats.get("success") and stats.get("registered"):
                result.success = True
                status_msg = "Completed"
            elif stats.get("success"):
                result.success = False
                status_msg = "Partial Success (Registry Failed)"
            else:
                result.success = False
                status_msg = "Persistence Failed"

            # 5. Generate Formatted Execution Summary
            services = artifact.get("recommended_services", [])
            svc_list = "\n".join([f"- {s['service_name']} (Confidence: {s['confidence']})" for s in services])
            
            summary = (
                f"**Opportunity Intelligence Execution Report**\n\n"
                f"Opportunity Level: {artifact.get('opportunity_level')}\n"
                f"Verified Issues: {len(artifact.get('verified_issues', []))}\n"
                f"Evidence Count: {len(services) + len(artifact.get('opportunity_drivers', []))}\n\n"
                f"**Recommended Services:**\n"
                f"{svc_list if svc_list else '- None required at this time.'}\n\n"
                f"**Artifact Generation:**\n"
                f"Artifact Created: OpportunityArtifact\n"
                f"Artifact ID: {stats.get('artifact_id', 'Unknown')}\n"
                f"Repository Saved: {'Yes' if stats.get('success') else 'No'}\n"
                f"Registry Registered: {'Yes' if stats.get('registered') else 'No'}\n"
                f"Version: {stats.get('version', 0)}\n"
                f"Validation: {stats.get('validation', 'Failed')}\n\n"
                f"Recommended Next Step: {artifact.get('recommended_next_step')}\n"
                f"Status: {status_msg}"
            )
            
            result.final_output = summary
            result.tool_calls.extend([b_res, e_res, s_res])
            return result

        except Exception as e:
            logger.error(f"[Opportunity Agent] Execution failed: {str(e)}", exc_info=True)
            result.errors.append(str(e))
            return result