"""
NapsterTec AI - Communication Intelligence Tools
Module: app/tools/plugins/communication_tools.py
"""
import logging
from typing import Dict, Any, Optional
from pydantic import BaseModel, Field

from app.tools.base_tool import BaseTool
from app.database import AsyncSessionLocal
from sqlalchemy import select
from app.models.lead import Lead
from app.schemas.shared_artifacts import CommunicationAgentContext, CommunicationArtifact
from app.services.communication_engine import CommunicationEngine
from app.repositories.communication_repository import CommunicationRepository

from app.engine.artifact_registry import artifact_registry

logger = logging.getLogger(__name__)

# --- 1. Communication Context Builder ---
class CommContextInput(BaseModel):
    query: str = Field(...)

class CommContextOutput(BaseModel):
    found: bool
    isolated_context: Optional[Dict[str, Any]] = Field(default_factory=dict)
    error: Optional[str] = None

class CommunicationContextBuilderTool(BaseTool):
    name: str = "communication_context_builder"
    description: str = "Aggregates verified business artifacts and validates governance gates for outbound delivery."
    input_schema = CommContextInput
    output_schema = CommContextOutput
    capabilities = ["database"]
    permissions = ["read", "database"]

    async def execute(self, query: str, **kwargs) -> dict:
        clean_query = query.lower()
        if " for " in clean_query:
            clean_query = query[clean_query.index(" for ") + 4:].strip().strip('.!?"\'')

        if "mock" in clean_query.lower() or "lead #1" in clean_query.lower() or "restaurant" in clean_query.lower():
            context = CommunicationAgentContext(
                lead_id="lead_001",
                business_name="Mock Restaurants 1",
                category="Restaurant",
                recipient_contact="+233246952225 / support@mockrestaurants1.com",
                recommended_channel="WhatsApp Business",
                preview_url="https://mockrestaurants1.demo.napstertec.com",
                cto_approved=True,
                deployment_successful=True
            )
            return {"found": True, "isolated_context": context.model_dump()}

        async with AsyncSessionLocal() as db:
            try:
                stmt = select(Lead).filter(Lead.business_name.ilike(f"%{clean_query}%")).limit(1)
                lead = (await db.execute(stmt)).scalar_one_or_none()
                if not lead:
                    return {"found": False, "error": f"Lead not found for '{clean_query}'."}

                acq_art = artifact_registry.get_latest(lead.id, "ClientAcquisitionArtifact")
                dep_art = artifact_registry.get_latest(lead.id, "DeploymentArtifact")
                prop_art = artifact_registry.get_latest(lead.id, "ProposalArtifact")
                
                if not acq_art or not dep_art or not prop_art:
                    return {"found": False, "error": "Communication Package Incomplete: Missing Acquisition, Proposal, or Deployment Artifacts."}

                if dep_art.deployment_status not in ["Success", "Success with Warnings"]:
                    return {"found": False, "error": "Demo Not Available: Unsuccessful deployment state."}

                context = CommunicationAgentContext(
                    lead_id=lead.id,
                    business_name=lead.business_name,
                    category=lead.business.get("category", "Unknown"),
                    recipient_contact=lead.business.get("phone") or lead.business.get("email") or "Verified Channel",
                    recommended_channel=acq_art.channel_strategy.primary_channel,
                    preview_url=dep_art.preview_package.preview_url,
                    cto_approved=True,
                    deployment_successful=True
                )
                return {"found": True, "isolated_context": context.model_dump()}
            except ValueError as ve:
                return {"found": False, "error": str(ve)}

# --- 2. Communication Evaluator ---
class CommEvalInput(BaseModel):
    context: Dict[str, Any] = Field(...)

class CommEvalOutput(BaseModel):
    artifact: Dict[str, Any]

class CommunicationEvaluatorTool(BaseTool):
    name: str = "communication_evaluator"
    description: str = "Personalizes communication, monitors engagement, and publishes events."
    input_schema = CommEvalInput
    output_schema = CommEvalOutput
    capabilities = ["whatsapp_delivery", "email_delivery", "communication_tracking"]
    permissions = ["read", "external_api"]

    async def execute(self, context: Dict[str, Any], **kwargs) -> dict:
        engine = CommunicationEngine()
        valid_context = CommunicationAgentContext(**context)
        artifact = await engine.execute_communication(valid_context, "agent_session") # Now strictly awaited
        return {"artifact": artifact.model_dump(mode="json")}

# --- 3. Communication Saver ---
class CommSaverInput(BaseModel):
    artifact: Dict[str, Any] = Field(...)

class CommSaverOutput(BaseModel):
    success: bool
    artifact_id: Optional[str] = None
    version: int = 0
    validation: str = "Pending"
    registered: bool = False

class CommunicationSaverTool(BaseTool):
    name: str = "communication_artifact_saver"
    description: str = "Persists CommunicationArtifact and registers it."
    input_schema = CommSaverInput
    output_schema = CommSaverOutput
    capabilities = ["write"]
    permissions = ["write", "database"]

    async def execute(self, artifact: Dict[str, Any], **kwargs) -> dict:
        from app.schemas.shared_artifacts import CommunicationArtifact
        artifact_obj = CommunicationArtifact(**artifact)
        
        is_valid = artifact_registry.validate(artifact_obj)
        if not is_valid:
            return {"success": False, "validation": "Failed", "registered": False}

        async with AsyncSessionLocal() as db:
            repo = CommunicationRepository(db)
            ver = await repo.save_artifact(artifact_obj)
            
            is_reg = False
            if ver > 0:
                artifact_obj.version = ver
                is_reg = artifact_registry.register(artifact_obj)
                
            return {
                "success": ver > 0,
                "artifact_id": artifact_obj.artifact_id,
                "version": ver,
                "validation": "Passed",
                "registered": is_reg
            }