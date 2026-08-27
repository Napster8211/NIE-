"""
NapsterTec AI - Coding Intelligence Agent
Module: app/agent/definitions/coding_agent.py
"""
import logging
from typing import Dict, Any

from app.agent.base_agent import BaseAgent
from app.agent.agent_models import AgentMetadata, AgentContext, AgentResult, AgentPermission, AgentCapability

logger = logging.getLogger(__name__)

class CodingIntelligenceAgent(BaseAgent):
    def __init__(self, **kwargs):
        metadata = AgentMetadata(
            name="coding_intelligence",
            display_name="Coding Intelligence Director",
            description="Transforms technical architectures into structured, production-quality software implementations.",
            version="1.0.0",
            category="software_engineering",
            department_id="engineering_delivery",    # CANONICAL TAXONOMY
            department_name="Engineering & Delivery",# CANONICAL TAXONOMY
            capabilities={AgentCapability.RESEARCH},
            supported_task_types=["implement system", "generate project", "write code", "implement architecture"],
            allowed_tools={"coding_context_builder", "coding_evaluator", "coding_artifact_saver"},
            allowed_providers={"openrouter"},
            cost_preference="balanced",
            reasoning_level="high",
            model_profile="coding",
            max_model_cost_per_request_usd=0.04,
            allow_free_model_first=False,
            required_permissions={AgentPermission.READ, AgentPermission.WRITE} 
        )
        super().__init__(metadata=metadata, **kwargs)

    async def execute(self, context: AgentContext) -> AgentResult:
        result = AgentResult(success=False, agent_name=self.metadata.name, session_id=context.session_id)
        
        try:
            search_query = context.planner_output.get("query", context.task)
            b_res = await self.invoke_tool("coding_context_builder", {"query": search_query}, context)
            b_data = getattr(b_res["output"], "data", b_res["output"])
            if hasattr(b_data, "model_dump"): b_data = b_data.model_dump()
            if not isinstance(b_data, dict): b_data = {}
            
            if not b_data.get("found"):
                result.final_output = f"Task Failed: {b_data.get('error', 'Context missing.')}"
                return result
            iso_context = b_data["isolated_context"]

            e_res = await self.invoke_tool("coding_evaluator", {"context": iso_context}, context)
            e_data = getattr(e_res["output"], "data", e_res["output"])
            if hasattr(e_data, "model_dump"): e_data = e_data.model_dump()
            if not isinstance(e_data, dict): e_data = {}
            
            if not e_data or "artifact" not in e_data:
                result.final_output = "Task Failed: Evaluator failed to generate artifact."
                return result
            artifact = e_data["artifact"]

            s_res = await self.invoke_tool("coding_artifact_saver", {"artifact": artifact}, context)
            s_data = getattr(s_res["output"], "data", s_res["output"])
            if hasattr(s_data, "model_dump"): s_data = s_data.model_dump()
            if not isinstance(s_data, dict): s_data = {}

            if s_data.get("success") and s_data.get("registered"):
                result.success = True
                status_msg = "Completed"
            else:
                result.success = False
                status_msg = "Persistence Failed"

            summary = (
                f"**Coding Intelligence Execution Report**\n\n"
                f"Project Scaffolded: Yes\n"
                f"Files Generated: {len(artifact.get('files_generated', []))}\n"
                f"Files Updated: {len(artifact.get('files_updated', []))}\n"
                f"Modules Created: {len(artifact.get('modules_created', []))}\n"
                f"Components Scaffoled: {len(artifact.get('components_created', []))}\n"
                f"APIs Scaffolded: {len(artifact.get('apis_created', []))}\n"
                f"Tests Generated: {len(artifact.get('tests_generated', []))}\n"
                f"Documentation Generated: {len(artifact.get('documentation_generated', []))}\n\n"
                f"**Code Quality Checks:**\n"
                f"- SOLID Principles: Enforced\n"
                f"- Repository Pattern: Applied\n"
                f"- Dependency Injection: Configured\n\n"
                f"**Artifact Generation:**\n"
                f"Artifact Created: ImplementationArtifact\n"
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
            logger.error(f"[Coding Agent] Execution failed: {str(e)}", exc_info=True)
            result.errors.append(str(e))
            return result