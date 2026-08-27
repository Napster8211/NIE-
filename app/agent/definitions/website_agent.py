"""
NapsterTec AI - Website Intelligence Agent
Module: app/agent/definitions/website_agent.py
"""
import logging
from typing import Dict, Any

from app.agent.base_agent import BaseAgent
from app.agent.agent_models import AgentMetadata, AgentContext, AgentResult, AgentPermission, AgentCapability
from app.schemas.shared_artifacts import WebsiteAgentContext

logger = logging.getLogger(__name__)

class WebsiteIntelligenceAgent(BaseAgent):
    def __init__(self, **kwargs):
        metadata = AgentMetadata(
            name="website_intelligence",
            display_name="Website Intelligence Director",
            description="Analyzes digital presence, generating structured evidence-backed Artifacts.",
            version="1.2.0",
            category="business_intelligence",
            department_id="growth_marketing",        # CANONICAL TAXONOMY
            department_name="Growth & Marketing",    # CANONICAL TAXONOMY
            capabilities={AgentCapability.RESEARCH},
            supported_task_types=["analyze website", "audit website"],
            allowed_tools={"website_context_builder", "website_inspector", "website_artifact_saver"},
            required_permissions={AgentPermission.EXTERNAL_API, AgentPermission.WRITE} 
        )
        super().__init__(metadata=metadata, **kwargs)

    async def execute(self, context: AgentContext) -> AgentResult:
        result = AgentResult(success=False, agent_name=self.metadata.name, session_id=context.session_id)
        
        try:
            # 1. Look up & Build Context
            search_query = context.planner_output.get("query", context.task)
            
            builder_res = await self.invoke_tool("website_context_builder", {"query": search_query}, context)
            b_out = builder_res["output"]
            b_data = getattr(b_out, "data", b_out)
            if hasattr(b_data, "model_dump"): b_data = b_data.model_dump()
            if not isinstance(b_data, dict): b_data = {}
            
            if not b_data.get("found"):
                err_msg = b_data.get("error", "Could not isolate Context. Lead not found.")
                result.final_output = f"Task Failed: {err_msg}"
                return result

            # 2. Strict Artifact Purity Validation
            try:
                isolated_context = WebsiteAgentContext(**b_data.get("isolated_context", {}))
            except Exception as ve:
                result.final_output = f"InvalidAgentContext: {str(ve)}"
                return result

            # 3. Inspect Website (Using only validated context fields)
            inspect_res = await self.invoke_tool("website_inspector", {"url": isolated_context.website or "unknown"}, context)
            i_out = inspect_res["output"]
            raw_audit = getattr(i_out, "data", i_out)
            if hasattr(raw_audit, "model_dump"): raw_audit = raw_audit.model_dump()
            if not isinstance(raw_audit, dict): raw_audit = {}

            # 4. Generate & Persist Shared Artifact
            upsert_res = await self.invoke_tool("website_artifact_saver", {"lead_id": isolated_context.lead_id, "raw_audit": raw_audit}, context)
            u_out = upsert_res["output"]
            stats = getattr(u_out, "data", u_out)
            if hasattr(stats, "model_dump"): stats = stats.model_dump()
            if not isinstance(stats, dict): stats = {}

            # 5. Format Execution Report (Pure Repository Representation)
            if stats.get("success") and stats.get("transaction_committed"):
                result.success = True
                status_msg = "Completed"
            else:
                result.success = False
                status_msg = "Failed Artifact Persistence"
                
            artifact = stats.get("artifact", {})
            evidence_count = len(artifact.get("technology", [])) + len(artifact.get("business_signals", [])) + len(artifact.get("recommendations", []))

            summary = (
                f"**Website Intelligence Execution Report**\n\n"
                f"**Lead Context**\n"
                f"Business Name: {isolated_context.business_name}\n"
                f"Lead ID: {isolated_context.lead_id}\n"
                f"Website: {isolated_context.website or 'None'}\n"
                f"Category: {isolated_context.category or 'Unknown'}\n"
                f"Provider: {isolated_context.metadata.get('provider', 'Database')}\n\n"
                f"**Artifact Generation:**\n"
                f"Artifact Created: WebsiteArtifact\n"
                f"Repository Saved: Version {stats.get('version_saved', 0)}\n"
                f"Total Evidence Collected: {evidence_count}\n\n"
                f"**Extracted Profiles:**\n"
                f"- Technology Count: {len(artifact.get('technology', []))}\n"
                f"- Business Signals: {len(artifact.get('business_signals', []))}\n"
                f"- Recommendations: {len(artifact.get('recommendations', []))}\n\n"
                f"Execution Status: {status_msg}"
            )
            
            result.final_output = summary
            result.tool_calls.extend([builder_res, inspect_res, upsert_res])
            return result

        except Exception as e:
            logger.error(f"[Website Agent] Execution failed: {str(e)}", exc_info=True)
            result.errors.append(str(e))
            return result