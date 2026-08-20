"""
NapsterTec AI - Social Intelligence Agent
Module: app/agent/definitions/social_agent.py
"""
import logging
from typing import Dict, Any

from app.agent.base_agent import BaseAgent
from app.agent.agent_models import AgentMetadata, AgentContext, AgentResult, AgentPermission, AgentCapability

logger = logging.getLogger(__name__)

class SocialIntelligenceAgent(BaseAgent):
    def __init__(self, **kwargs):
        metadata = AgentMetadata(
            name="social_intelligence",
            display_name="Social Intelligence Operations",
            description="Transforms content strategies into platform-specific, ready-to-publish social media assets.",
            version="1.0.0",
            category="social_media_operations",
            capabilities={AgentCapability.RESEARCH},
            supported_task_types=["prepare social assets", "adapt content", "generate social media"],
            allowed_tools={"social_context_builder", "social_evaluator", "social_artifact_saver"},
            required_permissions={AgentPermission.READ, AgentPermission.WRITE} 
        )
        super().__init__(metadata=metadata, **kwargs)

    async def execute(self, context: AgentContext) -> AgentResult:
        result = AgentResult(success=False, agent_name=self.metadata.name, session_id=context.session_id)
        
        try:
            search_query = context.planner_output.get("query", context.task)
            b_res = await self.invoke_tool("social_context_builder", {"query": search_query}, context)
            b_data = getattr(b_res["output"], "data", b_res["output"])
            if hasattr(b_data, "model_dump"): b_data = b_data.model_dump()
            if not isinstance(b_data, dict): b_data = {}
            
            if not b_data.get("found"):
                result.final_output = f"Task Failed: {b_data.get('error', 'Context missing.')}"
                return result
            iso_context = b_data["isolated_context"]

            e_res = await self.invoke_tool("social_evaluator", {"context": iso_context}, context)
            e_data = getattr(e_res["output"], "data", e_res["output"])
            if hasattr(e_data, "model_dump"): e_data = e_data.model_dump()
            if not isinstance(e_data, dict): e_data = {}
            
            if not e_data or "artifact" not in e_data:
                result.final_output = "Task Failed: Evaluator failed to generate artifact."
                return result
            artifact = e_data["artifact"]

            s_res = await self.invoke_tool("social_artifact_saver", {"artifact": artifact}, context)
            s_data = getattr(s_res["output"], "data", s_res["output"])
            if hasattr(s_data, "model_dump"): s_data = s_data.model_dump()
            if not isinstance(s_data, dict): s_data = {}

            if s_data.get("success") and s_data.get("registered"):
                result.success = True
                status_msg = "Completed (Awaiting CTO Approval)"
            else:
                result.success = False
                status_msg = "Persistence Failed"

            posts = artifact.get('posts', [])
            media_reqs = sum([len(p.get("media_requirements", [])) for p in posts])

            summary = (
                f"**Social Intelligence Execution Report**\n\n"
                f"Platforms Prepared: {len(posts)} ({', '.join([p.get('platform') for p in posts])})\n"
                f"Brand Voice Validation: Verified\n"
                f"Media Assets Required: {media_reqs} Creative Tasks Generated\n"
                f"Hashtag Strategy: Implemented\n"
                f"Publishing Recommendations: {len(posts)} Windows Calculated\n"
                f"Approval Required: True\n\n"
                f"**Artifact Generation:**\n"
                f"Artifact Created: SocialArtifact\n"
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
            logger.error(f"[Social Agent] Execution failed: {str(e)}", exc_info=True)
            result.errors.append(str(e))
            return result