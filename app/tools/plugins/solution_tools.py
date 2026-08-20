"""
NapsterTec AI - Solution Intelligence Tools
Module: app/tools/plugins/solution_tools.py
"""
import logging
from typing import Dict, Any, Optional
from pydantic import BaseModel, Field

from app.tools.base_tool import BaseTool
from app.database import AsyncSessionLocal
from sqlalchemy import select
from app.models.lead import Lead
from app.schemas.shared_artifacts import SolutionAgentContext, BusinessSolutionArtifact
from app.services.solution_engine import SolutionEngine
from app.repositories.solution_repository import SolutionRepository

# Central Artifact Registry
from app.engine.artifact_registry import artifact_registry

logger = logging.getLogger(__name__)

# --- 1. Solution Context Builder ---
class SolContextInput(BaseModel):
    query: str = Field(...)

class SolContextOutput(BaseModel):
    found: bool
    isolated_context: Optional[Dict[str, Any]] = Field(default_factory=dict)
    error: Optional[str] = None

class SolutionContextBuilderTool(BaseTool):
    name: str = "solution_context_builder"
    description: str = "Merges Lead and Opportunity artifacts into an isolated SolutionContext."
    input_schema = SolContextInput
    output_schema = SolContextOutput
    capabilities = ["database"]
    permissions = ["read", "database"]

    async def execute(self, query: str, **kwargs) -> dict:
        clean_query = query.lower()
        if " for " in clean_query:
            clean_query = query[clean_query.index(" for ") + 4:].strip().strip('.!?"\'')

        # ULTRA-FAST PATH (MOCK)
        if "mock" in clean_query.lower() or "lead #1" in clean_query.lower():
            context = SolutionAgentContext(
                lead_id="lead_001",
                business_name="Mock Restaurants 1",
                category="Restaurant",
                opportunity_level="High",
                verified_issues=["No functional digital presence.", "No Reservation/Ordering System"],
                recommended_services=["Full Website Build", "Reservation System Integration"]
            )
            return {"found": True, "isolated_context": context.model_dump()}

        # Regular Flow via Artifact Registry & Lead Database
        async with AsyncSessionLocal() as db:
            try:
                stmt = select(Lead).filter(Lead.business_name.ilike(f"%{clean_query}%")).limit(1)
                lead = (await db.execute(stmt)).scalar_one_or_none()
                if not lead:
                    return {"found": False, "error": f"Lead not found for '{clean_query}'."}

                # Retrieve Opportunity Artifact via Central Registry
                opp_artifact = artifact_registry.get_latest(lead.id, "OpportunityArtifact")
                if not opp_artifact:
                    return {"found": False, "error": "Insufficient Business Intelligence: Opportunity Artifact missing."}

                context = SolutionAgentContext(
                    lead_id=lead.id,
                    business_name=lead.business_name,
                    category=lead.business.get("category", "Unknown"),
                    opportunity_level=opp_artifact.opportunity_level,
                    verified_issues=opp_artifact.verified_issues,
                    recommended_services=[s.service_name for s in opp_artifact.recommended_services]
                )
                return {"found": True, "isolated_context": context.model_dump()}
            except ValueError as ve:
                return {"found": False, "error": str(ve)}

# --- 2. Solution Evaluator ---
class SolEvalInput(BaseModel):
    context: Dict[str, Any] = Field(...)

class SolEvalOutput(BaseModel):
    artifact: Dict[str, Any]

class SolutionEvaluatorTool(BaseTool):
    name: str = "solution_evaluator"
    description: str = "Generates a deterministic digital architecture blueprint."
    input_schema = SolEvalInput
    output_schema = SolEvalOutput
    capabilities = ["solution_design"]
    permissions = ["read"]

    async def execute(self, context: Dict[str, Any], **kwargs) -> dict:
        engine = SolutionEngine()
        valid_context = SolutionAgentContext(**context)
        artifact = engine.design_blueprint(valid_context, "agent_session")
        return {"artifact": artifact.model_dump(mode="json")}

# --- 3. Solution Saver ---
class SolSaverInput(BaseModel):
    artifact: Dict[str, Any] = Field(...)

class SolSaverOutput(BaseModel):
    success: bool
    artifact_id: Optional[str] = None
    version: int = 0
    validation: str = "Pending"
    registered: bool = False

class SolutionSaverTool(BaseTool):
    name: str = "solution_artifact_saver"
    description: str = "Persists BusinessSolutionArtifact and registers it with the Registry."
    input_schema = SolSaverInput
    output_schema = SolSaverOutput
    capabilities = ["write"]
    permissions = ["write", "database"]

    async def execute(self, artifact: Dict[str, Any], **kwargs) -> dict:
        artifact_obj = BusinessSolutionArtifact(**artifact)
        
        is_valid = artifact_registry.validate(artifact_obj)
        if not is_valid:
            return {"success": False, "validation": "Failed", "registered": False}

        async with AsyncSessionLocal() as db:
            repo = SolutionRepository(db)
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