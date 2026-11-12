"""
NapsterTec AI - Client Acquisition Tools
Module: app/tools/plugins/acquisition_tools.py
"""
import logging
from typing import Dict, Any, Optional
from pydantic import BaseModel, Field

from app.tools.base_tool import BaseTool
from app.database import AsyncSessionLocal
from sqlalchemy import select
from app.models.lead import Lead
from app.schemas.shared_artifacts import AcquisitionAgentContext, ClientAcquisitionArtifact
from app.services.acquisition_engine import AcquisitionEngine
from app.repositories.acquisition_repository import AcquisitionRepository

from app.engine.artifact_registry import artifact_registry

logger = logging.getLogger(__name__)

# --- 1. Acquisition Context Builder ---
class AcqContextInput(BaseModel):
    query: str = Field(...)

class AcqContextOutput(BaseModel):
    found: bool
    isolated_context: Optional[Dict[str, Any]] = Field(default_factory=dict)
    error: Optional[str] = None

class AcquisitionContextBuilderTool(BaseTool):
    name: str = "acquisition_context_builder"
    description: str = "Merges Deployments and Business artifacts for outreach preparation."
    input_schema = AcqContextInput
    output_schema = AcqContextOutput
    capabilities = ["database"]
    permissions = ["read", "database"]

    async def execute(self, query: str, **kwargs) -> dict:
        original_query = query.lower()
        clean_query = original_query
        
        # Check if query requests the top/latest lead
        is_top_lead_query = any(k in original_query for k in ("lead #1", "top lead", "latest lead", "first lead"))

        # Simple parsing for actual database queries
        if " for " in clean_query:
            if clean_query.startswith("prepare outreach") or clean_query.startswith("prepare acquisition"):
                clean_query = query[clean_query.index(" for ") + 4:].strip().strip('.!?"\'')
            else:
                clean_query = clean_query.replace("prepare", "").split(" for ")[0].strip().strip('.!?"\'')

        async with AsyncSessionLocal() as db:
            try:
                lead = None
                if is_top_lead_query:
                    stmt = select(Lead).filter(~Lead.business_name.ilike("%mock%")).order_by(Lead.created_at.desc()).limit(1)
                    lead = (await db.execute(stmt)).scalar_one_or_none()

                if not lead and clean_query:
                    stmt = select(Lead).filter(Lead.business_name.ilike(f"%{clean_query}%")).limit(1)
                    lead = (await db.execute(stmt)).scalar_one_or_none()

                if not lead:
                    stmt = select(Lead).filter(~Lead.business_name.ilike("%mock%")).order_by(Lead.created_at.desc()).limit(1)
                    lead = (await db.execute(stmt)).scalar_one_or_none()

                if not lead:
                    return {"found": False, "error": f"Lead not found for '{clean_query}'."}

                opp_art = artifact_registry.get_latest(lead.id, "OpportunityArtifact")
                if not opp_art:
                    from app.models.opportunity import OpportunityIntelligence
                    opp_stmt = select(OpportunityIntelligence).where(OpportunityIntelligence.lead_id == lead.id).order_by(OpportunityIntelligence.version.desc()).limit(1)
                    opp_rec = (await db.execute(opp_stmt)).scalar_one_or_none()
                    if opp_rec and opp_rec.report:
                        from app.schemas.shared_artifacts import OpportunityArtifact
                        opp_art = OpportunityArtifact(**opp_rec.report)

                if not opp_art:
                    from app.services.opportunity_engine import OpportunityEngine
                    from app.schemas.shared_artifacts import OpportunityAgentContext
                    ctx = OpportunityAgentContext(
                        lead_id=lead.id,
                        business_identity={"business_name": lead.business_name, "category": lead.business.get("category", "Restaurant")},
                        website_status="reachable",
                        technology=[],
                        seo_findings={},
                        business_signals=[]
                    )
                    opp_art = OpportunityEngine().evaluate(ctx, "agent_session")
                    artifact_registry.register(opp_art)

                sol_art = artifact_registry.get_latest(lead.id, "BusinessSolutionArtifact")
                if not sol_art:
                    from app.models.solution import BusinessSolutionBlueprint
                    sol_stmt = select(BusinessSolutionBlueprint).where(BusinessSolutionBlueprint.lead_id == lead.id).order_by(BusinessSolutionBlueprint.version.desc()).limit(1)
                    sol_rec = (await db.execute(sol_stmt)).scalar_one_or_none()
                    if sol_rec and sol_rec.report:
                        from app.schemas.shared_artifacts import BusinessSolutionArtifact
                        sol_art = BusinessSolutionArtifact(**sol_rec.report)

                if not sol_art:
                    from app.services.solution_engine import SolutionEngine
                    from app.schemas.shared_artifacts import SolutionAgentContext
                    s_ctx = SolutionAgentContext(
                        lead_id=lead.id,
                        business_name=lead.business_name,
                        category=lead.business.get("category", "Unknown"),
                        opportunity_level=opp_art.opportunity_level,
                        verified_issues=opp_art.verified_issues,
                        recommended_services=[s.service_name for s in opp_art.recommended_services]
                    )
                    sol_art = SolutionEngine().design_blueprint(s_ctx, "agent_session")
                    artifact_registry.register(sol_art)

                dep_art = artifact_registry.get_latest(lead.id, "DeploymentArtifact")
                preview_url = dep_art.preview_package.preview_url if dep_art and hasattr(dep_art, "preview_package") else f"https://preview.napstertec.com/{lead.id}"
                deployment_status = dep_art.deployment_status if dep_art else "Success"

                context = AcquisitionAgentContext(
                    lead_id=lead.id,
                    business_name=lead.business_name,
                    category=lead.business.get("category", "Unknown"),
                    phone=(lead.contact.get("phone") if isinstance(lead.contact, dict) else None) or lead.business.get("phone"),
                    website=lead.website_domain or (lead.contact.get("website") if isinstance(lead.contact, dict) else None) or lead.business.get("website"),
                    verified_issues=opp_art.verified_issues,
                    solution_type=sol_art.solution_type,
                    preview_url=preview_url,
                    deployment_status=deployment_status
                )
                return {"found": True, "isolated_context": context.model_dump()}
            except ValueError as ve:
                return {"found": False, "error": str(ve)}

# --- 2. Acquisition Evaluator ---
class AcqEvalInput(BaseModel):
    context: Dict[str, Any] = Field(...)

class AcqEvalOutput(BaseModel):
    artifact: Dict[str, Any]

class AcquisitionEvaluatorTool(BaseTool):
    name: str = "acquisition_evaluator"
    description: str = "Generates CRM strategy and outreach personalization."
    input_schema = AcqEvalInput
    output_schema = AcqEvalOutput
    capabilities = ["prospect_engagement"]
    permissions = ["read"]

    async def execute(self, context: Dict[str, Any], **kwargs) -> dict:
        engine = AcquisitionEngine()
        valid_context = AcquisitionAgentContext(**context)
        artifact = engine.prepare_acquisition(valid_context, "agent_session")
        return {"artifact": artifact.model_dump(mode="json")}

# --- 3. Acquisition Saver ---
class AcqSaverInput(BaseModel):
    artifact: Dict[str, Any] = Field(...)

class AcqSaverOutput(BaseModel):
    success: bool
    artifact_id: Optional[str] = None
    version: int = 0
    validation: str = "Pending"
    registered: bool = False

class AcquisitionSaverTool(BaseTool):
    name: str = "acquisition_artifact_saver"
    description: str = "Persists ClientAcquisitionArtifact and registers it."
    input_schema = AcqSaverInput
    output_schema = AcqSaverOutput
    capabilities = ["write"]
    permissions = ["write", "database"]

    async def execute(self, artifact: Dict[str, Any], **kwargs) -> dict:
        from app.schemas.shared_artifacts import ClientAcquisitionArtifact
        artifact_obj = ClientAcquisitionArtifact(**artifact)
        
        is_valid = artifact_registry.validate(artifact_obj)
        if not is_valid:
            return {"success": False, "validation": "Failed", "registered": False}

        async with AsyncSessionLocal() as db:
            repo = AcquisitionRepository(db)
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