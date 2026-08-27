"""
NapsterTec AI - Business Solution Architect
Module: app/agent/definitions/solution_agent.py
"""
import logging
from typing import Dict, Any

from app.agent.base_agent import BaseAgent
from app.agent.agent_models import AgentMetadata, AgentContext, AgentResult, AgentPermission, AgentCapability

logger = logging.getLogger(__name__)

class BusinessSolutionArchitectAgent(BaseAgent):
    def __init__(self, **kwargs):
        metadata = AgentMetadata(
            name="business_solution_architect",
            display_name="Business Solution Architect",
            description="Transforms verified intelligence into a complete digital transformation blueprint.",
            version="1.0.0",
            category="solution_architecture",
            department_id="engineering_delivery",    # CANONICAL TAXONOMY
            department_name="Engineering & Delivery",# CANONICAL TAXONOMY
            capabilities={AgentCapability.RESEARCH},
            supported_task_types=["design solution", "architect solution", "build architecture"],
            allowed_tools={"solution_context_builder", "solution_evaluator", "solution_artifact_saver"},
            required_permissions={AgentPermission.READ, AgentPermission.WRITE} 
        )
        super().__init__(metadata=metadata, **kwargs)

    async def execute(self, context: AgentContext) -> AgentResult:
        result = AgentResult(success=False, agent_name=self.metadata.name, session_id=context.session_id)
        
        try:
            # 1. Build Isolated Context
            search_query = context.planner_output.get("query", context.task)
            b_res = await self.invoke_tool("solution_context_builder", {"query": search_query}, context)
            b_data = getattr(b_res["output"], "data", b_res["output"])
            if hasattr(b_data, "model_dump"): b_data = b_data.model_dump()
            
            if not b_data.get("found"):
                result.final_output = f"Task Failed: {b_data.get('error', 'Context missing.')}"
                return result
            iso_context = b_data["isolated_context"]

            # 2. Design Architecture Blueprint
            e_res = await self.invoke_tool("solution_evaluator", {"context": iso_context}, context)
            e_data = getattr(e_res["output"], "data", e_res["output"])
            if hasattr(e_data, "model_dump"): e_data = e_data.model_dump()
            artifact = e_data["artifact"]

            # 3. Save & Register Artifact
            s_res = await self.invoke_tool("solution_artifact_saver", {"artifact": artifact}, context)
            s_data = getattr(s_res["output"], "data", s_res["output"])
            if hasattr(s_data, "model_dump"): s_data = s_data.model_dump()

            # 4. Strict Execution Grounding
            if s_data.get("success") and s_data.get("registered"):
                result.success = True
                status_msg = "Completed"
            else:
                result.success = False
                status_msg = "Persistence Failed"

            # 5. Generate Formatted Report
            summary = (
                f"**Business Solution Execution Report**\n\n"
                f"Business Type: {iso_context.get('category')}\n"
                f"Recommended Solution: {artifact.get('solution_type')}\n\n"
                f"**Architecture Summary:**\n"
                f"- Modules: {', '.join([m['name'] for m in artifact.get('modules', [])])}\n"
                f"- Features: {len(artifact.get('features', []))}\n"
                f"- Integrations: {', '.join([i['name'] for i in artifact.get('integrations', [])])}\n"
                f"- Technology: {', '.join(artifact.get('technology_stack', []))}\n"
                f"- Complexity: {artifact.get('complexity')}\n\n"
                f"**Business Benefits:**\n"
                f"- " + "\n- ".join(artifact.get("business_benefits", [])) + "\n\n"
                f"**Artifact Generation:**\n"
                f"Artifact Created: BusinessSolutionArtifact\n"
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
            logger.error(f"[Solution Agent] Execution failed: {str(e)}", exc_info=True)
            result.errors.append(str(e))
            return result