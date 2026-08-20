"""
NapsterTec AI - Proposal Intelligence Agent
Module: app/agent/definitions/proposal_agent.py
"""
import logging
from typing import Dict, Any

from app.agent.base_agent import BaseAgent
from app.agent.agent_models import AgentMetadata, AgentContext, AgentResult, AgentPermission, AgentCapability

logger = logging.getLogger(__name__)

class ProposalIntelligenceAgent(BaseAgent):
    def __init__(self, **kwargs):
        metadata = AgentMetadata(
            name="proposal_intelligence",
            display_name="Proposal Intelligence Director",
            description="Transforms verified business blueprints into structured proposal architectures.",
            version="1.0.0",
            category="business_consulting",
            capabilities={AgentCapability.RESEARCH},
            supported_task_types=["generate proposal", "design proposal", "draft proposal architecture"],
            allowed_tools={"proposal_context_builder", "proposal_evaluator", "proposal_artifact_saver"},
            required_permissions={AgentPermission.READ, AgentPermission.WRITE} 
        )
        super().__init__(metadata=metadata, **kwargs)

    async def execute(self, context: AgentContext) -> AgentResult:
        result = AgentResult(success=False, agent_name=self.metadata.name, session_id=context.session_id)
        
        try:
            # 1. Build Context
            search_query = context.planner_output.get("query", context.task)
            b_res = await self.invoke_tool("proposal_context_builder", {"query": search_query}, context)
            b_data = getattr(b_res["output"], "data", b_res["output"])
            if hasattr(b_data, "model_dump"): b_data = b_data.model_dump()
            if not isinstance(b_data, dict): b_data = {}
            
            if not b_data.get("found"):
                result.final_output = f"Task Failed: {b_data.get('error', 'Context missing.')}"
                return result
            iso_context = b_data["isolated_context"]

            # 2. Design Proposal Architecture
            e_res = await self.invoke_tool("proposal_evaluator", {"context": iso_context}, context)
            e_data = getattr(e_res["output"], "data", e_res["output"])
            if hasattr(e_data, "model_dump"): e_data = e_data.model_dump()
            if not isinstance(e_data, dict): e_data = {}
            
            # Strict Failsafe against Validation Errors
            if not e_data or "artifact" not in e_data:
                result.final_output = "Task Failed: Evaluator failed to generate artifact. Check schema validation."
                return result

            artifact = e_data["artifact"]

            # 3. Save & Register Artifact
            s_res = await self.invoke_tool("proposal_artifact_saver", {"artifact": artifact}, context)
            s_data = getattr(s_res["output"], "data", s_res["output"])
            if hasattr(s_data, "model_dump"): s_data = s_data.model_dump()
            if not isinstance(s_data, dict): s_data = {}

            # 4. Strict Grounding
            if s_data.get("success") and s_data.get("registered"):
                result.success = True
                status_msg = "Completed"
            else:
                result.success = False
                status_msg = "Persistence Failed"

            # 5. Generate Report
            summary = (
                f"**Proposal Intelligence Execution Report**\n\n"
                f"Proposal Type: {artifact.get('proposal_type')}\n"
                f"Executive Summary: Generated\n"
                f"Verified Issues: {len(artifact.get('verified_problems', []))}\n"
                f"Business Benefits: {len(artifact.get('business_benefits', []))}\n"
                f"Implementation Phases: {len(artifact.get('implementation_phases', []))}\n"
                f"Deliverables: {len(artifact.get('deliverables', []))}\n"
                f"Risks: {len(artifact.get('risks', []))}\n"
                f"Assumptions: {len(artifact.get('assumptions', []))}\n\n"
                f"**Artifact Generation:**\n"
                f"Artifact Created: ProposalArtifact\n"
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
            logger.error(f"[Proposal Agent] Execution failed: {str(e)}", exc_info=True)
            result.errors.append(str(e))
            return result