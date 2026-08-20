"""
NapsterTec AI - Communication Intelligence Agent
Module: app/agent/definitions/communication_agent.py
"""
import logging
from typing import Dict, Any

from app.agent.base_agent import BaseAgent
from app.agent.agent_models import AgentMetadata, AgentContext, AgentResult, AgentPermission, AgentCapability

logger = logging.getLogger(__name__)

class CommunicationIntelligenceAgent(BaseAgent):
    def __init__(self, **kwargs):
        metadata = AgentMetadata(
            name="communication_intelligence",
            display_name="Communication Intelligence Gateway",
            description="Enterprise unified communication hub managing outbound delivery, identity tracking, and event monitoring.",
            version="2.0.0",
            category="communications",
            capabilities={AgentCapability.RESEARCH}, 
            supported_task_types=["deliver proposal", "monitor engagement", "send communication", "outbound delivery", "contact client"],
            allowed_tools={"communication_context_builder", "communication_evaluator", "communication_artifact_saver"},
            required_permissions={AgentPermission.READ, AgentPermission.WRITE, AgentPermission.EXTERNAL_API} 
        )
        super().__init__(metadata=metadata, **kwargs)

    async def execute(self, context: AgentContext) -> AgentResult:
        result = AgentResult(success=False, agent_name=self.metadata.name, session_id=context.session_id)
        
        try:
            search_query = context.planner_output.get("query", context.task)
            
            # 1. Context Build & Governance Firewall
            b_res = await self.invoke_tool("communication_context_builder", {"query": search_query}, context)
            b_data = getattr(b_res["output"], "data", b_res["output"])
            if hasattr(b_data, "model_dump"): b_data = b_data.model_dump()
            if not isinstance(b_data, dict): b_data = {}
            
            if not b_data.get("found"):
                result.final_output = f"Task Failed: {b_data.get('error', 'Context missing.')}"
                return result
            iso_context = b_data["isolated_context"]

            if not iso_context.get("cto_approved") or not iso_context.get("deployment_successful"):
                result.final_output = "Task Failed: Communication Blocked. Governance validation failed."
                return result

            # 2. Evaluator & Monitoring (Event Bus Publication)
            e_res = await self.invoke_tool("communication_evaluator", {"context": iso_context}, context)
            e_data = getattr(e_res["output"], "data", e_res["output"])
            if hasattr(e_data, "model_dump"): e_data = e_data.model_dump()
            if not isinstance(e_data, dict): e_data = {}
            
            if not e_data or "artifact" not in e_data:
                result.final_output = "Task Failed: Evaluator failed to generate artifact."
                return result
            artifact = e_data["artifact"]

            # 3. Save Artifact
            s_res = await self.invoke_tool("communication_artifact_saver", {"artifact": artifact}, context)
            s_data = getattr(s_res["output"], "data", s_res["output"])
            if hasattr(s_data, "model_dump"): s_data = s_data.model_dump()
            if not isinstance(s_data, dict): s_data = {}

            if s_data.get("success") and s_data.get("registered"):
                result.success = True
                status_msg = "Completed"
            else:
                result.success = False
                status_msg = "Persistence Failed"

            identity = artifact.get('identity', {})
            tracking = artifact.get('tracking_info', {})

            summary = (
                f"**Communication Intelligence Enterprise Execution Report**\n\n"
                f"Communication Delivered: {artifact.get('recipient')} via {artifact.get('channel')}\n"
                f"Conversation ID: {identity.get('conversation_id')}\n"
                f"Thread ID: {identity.get('thread_id')}\n"
                f"Correlation ID: {identity.get('correlation_id')}\n"
                f"Workflow ID: {identity.get('workflow_id')}\n"
                f"Tracking Enabled: {'Yes' if tracking.get('tracking_enabled') else 'No'}\n"
                f"Events Published: {len(artifact.get('published_events', []))} (Proposal Opened, Demo Viewed)\n"
                f"Subscribers Notified: {', '.join(artifact.get('subscriber_notifications', []))}\n"
                f"CRM Timeline Updated: {len(artifact.get('crm_timeline_events', []))} chronological events appended\n\n"
                f"**Artifact Generation:**\n"
                f"Artifact Created: CommunicationArtifact\n"
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
            logger.error(f"[Communication Agent] Execution failed: {str(e)}", exc_info=True)
            result.errors.append(str(e))
            return result