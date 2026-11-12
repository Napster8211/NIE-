"""
NapsterTec AI - Customer Success Intelligence Tools
Module: app/tools/plugins/customer_success_tools.py
"""
import logging
import re
from typing import Dict, Any, Optional
from pydantic import BaseModel, Field

from app.tools.base_tool import BaseTool
from app.database import AsyncSessionLocal
from sqlalchemy import select
from app.models.lead import Lead
from app.schemas.shared_artifacts import CustomerSuccessAgentContext, CustomerSuccessArtifact
from app.services.customer_success_engine import CustomerSuccessEngine
from app.repositories.customer_success_repository import CustomerSuccessRepository

from app.engine.artifact_registry import artifact_registry

logger = logging.getLogger(__name__)

# --- 1. Customer Success Context Builder ---
class CSContextInput(BaseModel):
    query: str = Field(...)

class CSContextOutput(BaseModel):
    found: bool
    isolated_context: Optional[Dict[str, Any]] = Field(default_factory=dict)
    error: Optional[str] = None

class CustomerSuccessContextBuilderTool(BaseTool):
    name: str = "customer_success_context_builder"
    description: str = "Loads post-sale artifacts and CRM timeline to evaluate customer health."
    input_schema = CSContextInput
    output_schema = CSContextOutput
    capabilities = ["database"]
    permissions = ["read", "database"]

    async def execute(self, query: str, **kwargs) -> dict:
        clean_query = query.lower()
        if " for " in clean_query:
            clean_query = query[clean_query.index(" for ") + 4:].strip().strip('.!?"\'')

        async with AsyncSessionLocal() as db:
            try:
                lead = None
                if any(k in clean_query for k in ("lead #1", "top lead", "latest", "first lead", "top")):
                    stmt = select(Lead).filter(~Lead.business_name.ilike("%mock%")).order_by(Lead.created_at.desc()).limit(1)
                    lead = (await db.execute(stmt)).scalar_one_or_none()

                if not lead and clean_query and not any(k in clean_query for k in ("lead", "business", "restaurant", "client", "customer")):
                    stmt = select(Lead).filter(Lead.business_name.ilike(f"%{clean_query}%")).limit(1)
                    lead = (await db.execute(stmt)).scalar_one_or_none()

                if not lead:
                    stmt = select(Lead).filter(~Lead.business_name.ilike("%mock%")).order_by(Lead.created_at.desc()).limit(1)
                    lead = (await db.execute(stmt)).scalar_one_or_none()

                if not lead:
                    return {"found": False, "error": f"Customer not found for '{clean_query}'."}

                dep_art = artifact_registry.get_latest(lead.id, "DeploymentArtifact")
                if not dep_art:
                    from app.models.deployment import DeploymentBlueprint
                    from app.schemas.shared_artifacts import DeploymentArtifact
                    dep_stmt = select(DeploymentBlueprint).where(DeploymentBlueprint.lead_id == lead.id).order_by(DeploymentBlueprint.version.desc()).limit(1)
                    dep_rec = (await db.execute(dep_stmt)).scalar_one_or_none()
                    if dep_rec and dep_rec.report:
                        dep_art = DeploymentArtifact(**dep_rec.report)

                comm_art = artifact_registry.get_latest(lead.id, "CommunicationArtifact")
                if not comm_art:
                    from app.models.communication import CommunicationOperationsBlueprint
                    from app.schemas.shared_artifacts import CommunicationArtifact
                    comm_stmt = select(CommunicationOperationsBlueprint).where(CommunicationOperationsBlueprint.lead_id == lead.id).order_by(CommunicationOperationsBlueprint.version.desc()).limit(1)
                    comm_rec = (await db.execute(comm_stmt)).scalar_one_or_none()
                    if comm_rec and comm_rec.report:
                        comm_art = CommunicationArtifact(**comm_rec.report)

                safe_slug = re.sub(r'[^a-z0-9]', '', lead.business_name.lower())
                crm_events = comm_art.crm_timeline_events if comm_art else [
                    f"Outreach package prepared for {lead.business_name}",
                    f"Digital footprint audited for {lead.business_name}",
                    f"Edge demo deployed on https://{safe_slug}.demo.napstertec.com"
                ]

                deployment_status = dep_art.deployment_status if dep_art else "Live Active"

                context = CustomerSuccessAgentContext(
                    lead_id=lead.id,
                    business_name=lead.business_name,
                    deployment_status=deployment_status,
                    sales_stage="Closed Won / Onboarding",
                    crm_timeline_events=crm_events,
                    communication_history_count=len(crm_events)
                )
                return {"found": True, "isolated_context": context.model_dump()}
            except Exception as ve:
                logger.error("[CustomerSuccessTool] Error loading context: %s", ve)
                return {"found": False, "error": str(ve)}

# --- 2. Customer Success Evaluator ---
class CSEvalInput(BaseModel):
    context: Dict[str, Any] = Field(...)

class CSEvalOutput(BaseModel):
    artifact: Dict[str, Any]

class CustomerSuccessEvaluatorTool(BaseTool):
    name: str = "customer_success_evaluator"
    description: str = "Calculates health scores, onboarding progress, and expansion opportunities."
    input_schema = CSEvalInput
    output_schema = CSEvalOutput
    capabilities = ["customer_health", "onboarding_management", "churn_detection"]
    permissions = ["read"]

    async def execute(self, context: Dict[str, Any], **kwargs) -> dict:
        engine = CustomerSuccessEngine()
        valid_context = CustomerSuccessAgentContext(**context)
        artifact = engine.evaluate_customer(valid_context, "agent_session")
        return {"artifact": artifact.model_dump(mode="json")}

# --- 3. Customer Success Saver ---
class CSSaverInput(BaseModel):
    artifact: Dict[str, Any] = Field(...)

class CSSaverOutput(BaseModel):
    success: bool
    artifact_id: Optional[str] = None
    version: int = 0
    validation: str = "Pending"
    registered: bool = False

class CustomerSuccessSaverTool(BaseTool):
    name: str = "customer_success_artifact_saver"
    description: str = "Persists CustomerSuccessArtifact and registers it."
    input_schema = CSSaverInput
    output_schema = CSSaverOutput
    capabilities = ["write"]
    permissions = ["write", "database"]

    async def execute(self, artifact: Dict[str, Any], **kwargs) -> dict:
        from app.schemas.shared_artifacts import CustomerSuccessArtifact
        artifact_obj = CustomerSuccessArtifact(**artifact)
        
        is_valid = artifact_registry.validate(artifact_obj)
        if not is_valid:
            return {"success": False, "validation": "Failed", "registered": False}

        async with AsyncSessionLocal() as db:
            repo = CustomerSuccessRepository(db)
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
