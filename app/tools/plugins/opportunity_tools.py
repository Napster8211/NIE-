"""
NapsterTec AI - Opportunity Intelligence Tools
Module: app/tools/plugins/opportunity_tools.py
"""
import logging
from typing import Dict, Any, Optional
from pydantic import BaseModel, Field

from app.tools.base_tool import BaseTool
from app.database import AsyncSessionLocal
from sqlalchemy import select
from app.models.lead import Lead
from app.models.website import WebsiteIntelligence
from app.schemas.shared_artifacts import OpportunityAgentContext, OpportunityArtifact
from app.services.opportunity_engine import OpportunityEngine
from app.repositories.opportunity_repository import OpportunityRepository

# --- NEW IMPORT: The Central Artifact Registry ---
from app.engine.artifact_registry import artifact_registry

logger = logging.getLogger(__name__)

# --- 1. Opportunity Context Builder ---
class OppContextInput(BaseModel):
    query: str = Field(...)

class OppContextOutput(BaseModel):
    found: bool
    isolated_context: Optional[Dict[str, Any]] = Field(default_factory=dict)
    error: Optional[str] = None

class OpportunityContextBuilderTool(BaseTool):
    name: str = "opportunity_context_builder"
    description: str = "Merges Lead and Website artifacts into an isolated OpportunityContext."
    input_schema = OppContextInput
    output_schema = OppContextOutput
    capabilities = ["database"]
    permissions = ["read", "database"]

    async def execute(self, query: str, **kwargs) -> dict:
        clean_query = query.lower()
        if " for " in clean_query:
            # Slice from original but we will lowercase it for the check
            clean_query = query[clean_query.index(" for ") + 4:].strip().strip('.!?"\'')

        # Safely catch the mock string regardless of capitalization
        if "mock" in clean_query.lower() or "lead #1" in clean_query.lower():
            context = OpportunityAgentContext(
                lead_id="lead_001",
                business_identity={"business_name": "Mock Restaurants 1", "category": "Restaurant"},
                website_status="reachable",
                business_signals=[{"name": "HTTPS Enabled", "present": True}, {"name": "Online Booking Present", "present": False}],
                seo_findings={"description": None}
            )
            return {"found": True, "isolated_context": context.model_dump()}

        async with AsyncSessionLocal() as db:
            try:
                stmt = select(Lead).filter(Lead.business_name.ilike(f"%{clean_query}%")).limit(1)
                lead = (await db.execute(stmt)).scalar_one_or_none()
                if not lead:
                    return {"found": False, "error": f"Lead not found for '{clean_query}'."}

                w_stmt = select(WebsiteIntelligence).where(WebsiteIntelligence.lead_id == lead.id).order_by(WebsiteIntelligence.version.desc()).limit(1)
                web_intel = (await db.execute(w_stmt)).scalar_one_or_none()
                
                if not web_intel:
                    return {"found": False, "error": "Insufficient Evidence: Website Artifact missing."}

                rep = web_intel.report
                context = OpportunityAgentContext(
                    lead_id=lead.id,
                    business_identity={"business_name": lead.business_name, "category": lead.business.get("category")},
                    website_status=rep.get("status", "unknown"),
                    technology=rep.get("technology", []),
                    seo_findings=rep.get("seo", {}),
                    business_signals=rep.get("business_signals", [])
                )
                return {"found": True, "isolated_context": context.model_dump()}
            except ValueError as ve:
                return {"found": False, "error": str(ve)}

# --- 2. Opportunity Evaluator ---
class OppEvalInput(BaseModel):
    context: Dict[str, Any] = Field(...)

class OppEvalOutput(BaseModel):
    artifact: Dict[str, Any]

class OpportunityEvaluatorTool(BaseTool):
    name: str = "opportunity_evaluator"
    description: str = "Runs deterministic rules to map evidence to recommended services."
    input_schema = OppEvalInput
    output_schema = OppEvalOutput
    capabilities = ["opportunity_analysis"]
    permissions = ["read"]

    async def execute(self, context: Dict[str, Any], **kwargs) -> dict:
        engine = OpportunityEngine()
        valid_context = OpportunityAgentContext(**context)
        artifact = engine.evaluate(valid_context, "agent_session")
        return {"artifact": artifact.model_dump(mode="json")}

# --- 3. Opportunity Saver ---
class OppSaverInput(BaseModel):
    artifact: Dict[str, Any] = Field(...)

class OppSaverOutput(BaseModel):
    success: bool
    artifact_id: Optional[str] = None
    version: int = 0
    validation: str = "Pending"
    registered: bool = False

class OpportunitySaverTool(BaseTool):
    name: str = "opportunity_artifact_saver"
    description: str = "Persists OpportunityArtifact and registers it with the Artifact Registry."
    input_schema = OppSaverInput
    output_schema = OppSaverOutput
    capabilities = ["write"]
    permissions = ["write", "database"]

    async def execute(self, artifact: Dict[str, Any], **kwargs) -> dict:
        artifact_obj = OpportunityArtifact(**artifact)
        
        # 1. Pre-registration Validation
        is_valid = artifact_registry.validate(artifact_obj)
        if not is_valid:
            return {"success": False, "validation": "Failed", "registered": False, "error": "Artifact Validation Failed"}

        async with AsyncSessionLocal() as db:
            repo = OpportunityRepository(db)
            
            # 2. Database Persistence
            ver = await repo.save_artifact(artifact_obj)
            
            # 3. Artifact Registration
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