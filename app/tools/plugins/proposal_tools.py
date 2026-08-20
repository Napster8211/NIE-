"""
NapsterTec AI - Proposal Intelligence Tools
Module: app/tools/plugins/proposal_tools.py
"""
import logging
from typing import Dict, Any, Optional
from pydantic import BaseModel, Field

from app.tools.base_tool import BaseTool
from app.database import AsyncSessionLocal
from sqlalchemy import select
from app.models.lead import Lead
from app.schemas.shared_artifacts import ProposalAgentContext, ProposalArtifact
from app.services.proposal_engine import ProposalEngine
from app.repositories.proposal_repository import ProposalRepository

from app.engine.artifact_registry import artifact_registry

logger = logging.getLogger(__name__)

# --- 1. Proposal Context Builder ---
class PropContextInput(BaseModel):
    query: str = Field(...)

class PropContextOutput(BaseModel):
    found: bool
    isolated_context: Optional[Dict[str, Any]] = Field(default_factory=dict)
    error: Optional[str] = None

class ProposalContextBuilderTool(BaseTool):
    name: str = "proposal_context_builder"
    description: str = "Merges verified artifacts into an isolated ProposalContext."
    input_schema = PropContextInput
    output_schema = PropContextOutput
    capabilities = ["database"]
    permissions = ["read", "database"]

    async def execute(self, query: str, **kwargs) -> dict:
        clean_query = query.lower()
        if " for " in clean_query:
            clean_query = query[clean_query.index(" for ") + 4:].strip().strip('.!?"\'')

        # ULTRA-FAST PATH (MOCK)
        if "mock" in clean_query.lower() or "lead #1" in clean_query.lower():
            context = ProposalAgentContext(
                lead_id="lead_001",
                business_name="Mock Restaurants 1",
                category="Restaurant",
                verified_issues=["No functional digital presence.", "No Reservation System"],
                solution_type="Restaurant Digital Platform",
                modules=[{"name": "Reservations", "justification": "Automate bookings"}, {"name": "Interactive Menu", "justification": "Showcase food"}],
                features=[{"name": "Booking Calendar"}],
                benefits=["Increase Bookings", "Reduce Manual Admin"]
            )
            return {"found": True, "isolated_context": context.model_dump()}

        # Regular Flow via Artifact Registry & Lead Database
        async with AsyncSessionLocal() as db:
            try:
                stmt = select(Lead).filter(Lead.business_name.ilike(f"%{clean_query}%")).limit(1)
                lead = (await db.execute(stmt)).scalar_one_or_none()
                if not lead:
                    return {"found": False, "error": f"Lead not found for '{clean_query}'."}

                opp_artifact = artifact_registry.get_latest(lead.id, "OpportunityArtifact")
                sol_artifact = artifact_registry.get_latest(lead.id, "BusinessSolutionArtifact")
                
                if not opp_artifact or not sol_artifact:
                    return {"found": False, "error": "Insufficient Business Architecture: Missing Opportunity or Solution Artifact."}

                context = ProposalAgentContext(
                    lead_id=lead.id,
                    business_name=lead.business_name,
                    category=lead.business.get("category", "Unknown"),
                    verified_issues=opp_artifact.verified_issues,
                    solution_type=sol_artifact.solution_type,
                    modules=[m.model_dump() for m in sol_artifact.modules],
                    features=[f.model_dump() for f in sol_artifact.features],
                    benefits=sol_artifact.business_benefits
                )
                return {"found": True, "isolated_context": context.model_dump()}
            except ValueError as ve:
                return {"found": False, "error": str(ve)}

# --- 2. Proposal Evaluator ---
class PropEvalInput(BaseModel):
    context: Dict[str, Any] = Field(...)

class PropEvalOutput(BaseModel):
    artifact: Dict[str, Any]

class ProposalEvaluatorTool(BaseTool):
    name: str = "proposal_evaluator"
    description: str = "Generates a deterministic proposal architecture."
    input_schema = PropEvalInput
    output_schema = PropEvalOutput
    capabilities = ["proposal_architecture"]
    permissions = ["read"]

    async def execute(self, context: Dict[str, Any], **kwargs) -> dict:
        engine = ProposalEngine()
        valid_context = ProposalAgentContext(**context)
        artifact = engine.design_architecture(valid_context, "agent_session")
        return {"artifact": artifact.model_dump(mode="json")}

# --- 3. Proposal Saver ---
class PropSaverInput(BaseModel):
    artifact: Dict[str, Any] = Field(...)

class PropSaverOutput(BaseModel):
    success: bool
    artifact_id: Optional[str] = None
    version: int = 0
    validation: str = "Pending"
    registered: bool = False

class ProposalSaverTool(BaseTool):
    name: str = "proposal_artifact_saver"
    description: str = "Persists ProposalArtifact and registers it with the Registry."
    input_schema = PropSaverInput
    output_schema = PropSaverOutput
    capabilities = ["write"]
    permissions = ["write", "database"]

    async def execute(self, artifact: Dict[str, Any], **kwargs) -> dict:
        artifact_obj = ProposalArtifact(**artifact)
        
        is_valid = artifact_registry.validate(artifact_obj)
        if not is_valid:
            return {"success": False, "validation": "Failed", "registered": False}

        async with AsyncSessionLocal() as db:
            repo = ProposalRepository(db)
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