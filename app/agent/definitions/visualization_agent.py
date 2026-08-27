"""
NapsterTec AI - Solution Visualization Architect Agent
Module: app/agent/definitions/visualization_agent.py
"""
import logging
from typing import Dict, Any

from app.agent.base_agent import BaseAgent
from app.agent.agent_models import AgentMetadata, AgentContext, AgentResult, AgentPermission, AgentCapability

logger = logging.getLogger(__name__)

class SolutionVisualizationArchitectAgent(BaseAgent):
    def __init__(self, **kwargs):
        metadata = AgentMetadata(
            name="solution_visualization_architect",
            display_name="Solution Visualization Architect",
            description="Transforms business solutions into complete UX architecture and blueprints.",
            version="1.0.0",
            category="ux_architecture",
            department_id="engineering_delivery",    # CANONICAL TAXONOMY
            department_name="Engineering & Delivery",# CANONICAL TAXONOMY
            capabilities={AgentCapability.RESEARCH},
            supported_task_types=["visualize solution", "design ux", "architecture blueprint"],
            allowed_tools={"visualization_context_builder", "visualization_evaluator", "visualization_artifact_saver"},
            required_permissions={AgentPermission.READ, AgentPermission.WRITE} 
        )
        super().__init__(metadata=metadata, **kwargs)

    async def execute(self, context: AgentContext) -> AgentResult:
        result = AgentResult(success=False, agent_name=self.metadata.name, session_id=context.session_id)
        
        try:
            # 1. Build Context
            search_query = context.planner_output.get("query", context.task)
            b_res = await self.invoke_tool("visualization_context_builder", {"query": search_query}, context)
            b_data = getattr(b_res["output"], "data", b_res["output"])
            if hasattr(b_data, "model_dump"): b_data = b_data.model_dump()
            if not isinstance(b_data, dict): b_data = {}
            
            if not b_data.get("found"):
                result.final_output = f"Task Failed: {b_data.get('error', 'Context missing.')}"
                return result
            iso_context = b_data["isolated_context"]

            # 2. Design UX Architecture
            e_res = await self.invoke_tool("visualization_evaluator", {"context": iso_context}, context)
            e_data = getattr(e_res["output"], "data", e_res["output"])
            if hasattr(e_data, "model_dump"): e_data = e_data.model_dump()
            if not isinstance(e_data, dict): e_data = {}
            
            if not e_data or "artifact" not in e_data:
                result.final_output = "Task Failed: Evaluator failed to generate artifact."
                return result

            artifact = e_data["artifact"]

            # 3. Save & Register Artifact
            s_res = await self.invoke_tool("visualization_artifact_saver", {"artifact": artifact}, context)
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
                f"**Solution Visualization Execution Report**\n\n"
                f"Information Architecture: {len(artifact.get('information_architecture', []))} Hubs\n"
                f"Pages: {len(artifact.get('page_hierarchy', []))}\n"
                f"User Roles: {len(artifact.get('user_roles', []))}\n"
                f"User Journeys: {len(artifact.get('user_journeys', []))}\n"
                f"Components: {len(artifact.get('component_architecture', []))}\n"
                f"Dashboard Sections: {len(artifact.get('dashboard_architecture', []))}\n"
                f"Accessibility Rules: {len(artifact.get('accessibility_strategy', []))}\n\n"
                f"**Artifact Generation:**\n"
                f"Artifact Created: VisualizationArtifact\n"
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
            logger.error(f"[Visualization Agent] Execution failed: {str(e)}", exc_info=True)
            result.errors.append(str(e))
            return result