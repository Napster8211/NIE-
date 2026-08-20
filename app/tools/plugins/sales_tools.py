"""
NapsterTec AI - Sales Intelligence Tools
Module: app/tools/plugins/sales_tools.py
"""
import logging
from typing import Dict, Any, Optional
from pydantic import BaseModel, Field

from app.tools.base_tool import BaseTool
from app.database import AsyncSessionLocal
from sqlalchemy import select
from app.models.lead import Lead
from app.schemas.shared_artifacts import SalesAgentContext, SalesArtifact
from app.services.sales_engine import SalesEngine
from app.repositories.sales_repository import SalesRepository

from app.engine.artifact_registry import artifact_registry

logger = logging.getLogger(__name__)

# --- 1. Sales Context Builder ---
class SalesContextInput(BaseModel):
    query: str = Field(...)

class SalesContextOutput(BaseModel):
    found: bool
    isolated_context: Optional[Dict[str, Any]] = Field(default_factory=dict)
    error: Optional[str] = None

class SalesContextBuilderTool(BaseTool):
    name: str = "sales_context_builder"
    description: str = "Aggregates Lead, Opportunity, Proposal, and Deployment artifacts for sales intelligence."
    input_schema = SalesContextInput
    output_schema = SalesContextOutput
    capabilities = ["database"]
    permissions = ["read", "database"]

    async def execute(self, query: str, **kwargs) -> dict:
        clean_query = query.lower()
        if " for " in clean_query:
            clean_query = query[clean_query.index(" for ") + 4:].strip().strip('.!?"\'')

        if "mock" in clean_query.lower() or "lead #1" in clean_query.lower():
            context = SalesAgentContext(
                lead_id="lead_001",
                business_name="Mock Restaurants 1",
                category="Restaurant",
                verified_issues=["No functional digital presence", "No Reservation System"],
                solution_type="Restaurant Digital Platform",
                preview_url="https://mockrestaurants1.demo.napstertec.com"
            )
            return {"found": True, "isolated_context": context.model_dump()}

        async with AsyncSessionLocal() as db:
            try:
                stmt = select(Lead).filter(Lead.business_name.ilike(f"%{clean_query}%")).limit(1)
                lead = (await db.execute(stmt)).scalar_one_or_none()
                if not lead:
                    return {"found": False, "error": f"Lead not found for '{clean_query}'."}

                prop_art = artifact_registry.get_latest(lead.id, "ProposalArtifact")
                dep_art = artifact_registry.get_latest(lead.id, "DeploymentArtifact")
                opp_art = artifact_registry.get_latest(lead.id, "OpportunityArtifact")
                sol_art = artifact_registry.get_latest(lead.id, "BusinessSolutionArtifact")
                
                if not prop_art or not dep_art or not opp_art or not sol_art:
                    return {"found": False, "error": "Sales Preparation Incomplete: Missing Proposal, Deployment, or Opportunity Artifacts."}

                context = SalesAgentContext(
                    lead_id=lead.id,
                    business_name=lead.business_name,
                    category=lead.business.get("category", "Unknown"),
                    verified_issues=opp_art.verified_issues,
                    solution_type=sol_art.solution_type,
                    preview_url=dep_art.preview_package.preview_url
                )
                return {"found": True, "isolated_context": context.model_dump()}
            except ValueError as ve:
                return {"found": False, "error": str(ve)}

# --- 2. Sales Evaluator ---
class SalesEvalInput(BaseModel):
    context: Dict[str, Any] = Field(...)

class SalesEvalOutput(BaseModel):
    artifact: Dict[str, Any]

class SalesEvaluatorTool(BaseTool):
    name: str = "sales_evaluator"
    description: str = "Evaluates opportunity buying intent and prepares meeting agendas."
    input_schema = SalesEvalInput
    output_schema = SalesEvalOutput
    capabilities = ["opportunity_prioritization"]
    permissions = ["read"]

    async def execute(self, context: Dict[str, Any], **kwargs) -> dict:
        engine = SalesEngine()
        valid_context = SalesAgentContext(**context)
        artifact = engine.evaluate_opportunity(valid_context, "agent_session")
        return {"artifact": artifact.model_dump(mode="json")}

# --- 3. Sales Saver ---
class SalesSaverInput(BaseModel):
    artifact: Dict[str, Any] = Field(...)

class SalesSaverOutput(BaseModel):
    success: bool
    artifact_id: Optional[str] = None
    version: int = 0
    validation: str = "Pending"
    registered: bool = False

class SalesSaverTool(BaseTool):
    name: str = "sales_artifact_saver"
    description: str = "Persists SalesArtifact and registers it."
    input_schema = SalesSaverInput
    output_schema = SalesSaverOutput
    capabilities = ["write"]
    permissions = ["write", "database"]

    async def execute(self, artifact: Dict[str, Any], **kwargs) -> dict:
        from app.schemas.shared_artifacts import SalesArtifact
        artifact_obj = SalesArtifact(**artifact)
        
        is_valid = artifact_registry.validate(artifact_obj)
        if not is_valid:
            return {"success": False, "validation": "Failed", "registered": False}

        async with AsyncSessionLocal() as db:
            repo = SalesRepository(db)
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