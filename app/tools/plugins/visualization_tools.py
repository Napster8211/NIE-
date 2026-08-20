"""
NapsterTec AI - Visualization Intelligence Tools
Module: app/tools/plugins/visualization_tools.py
"""
import logging
from typing import Dict, Any, Optional
from pydantic import BaseModel, Field

from app.tools.base_tool import BaseTool
from app.database import AsyncSessionLocal
from sqlalchemy import select
from app.models.lead import Lead
from app.schemas.shared_artifacts import VisualizationAgentContext, VisualizationArtifact
from app.services.visualization_engine import VisualizationEngine
from app.repositories.visualization_repository import VisualizationRepository

from app.engine.artifact_registry import artifact_registry

logger = logging.getLogger(__name__)

# --- 1. Visualization Context Builder ---
class VisContextInput(BaseModel):
    query: str = Field(...)

class VisContextOutput(BaseModel):
    found: bool
    isolated_context: Optional[Dict[str, Any]] = Field(default_factory=dict)
    error: Optional[str] = None

class VisualizationContextBuilderTool(BaseTool):
    name: str = "visualization_context_builder"
    description: str = "Merges verified artifacts into an isolated VisualizationContext."
    input_schema = VisContextInput
    output_schema = VisContextOutput
    capabilities = ["database"]
    permissions = ["read", "database"]

    async def execute(self, query: str, **kwargs) -> dict:
        clean_query = query.lower()
        if " for " in clean_query:
            clean_query = query[clean_query.index(" for ") + 4:].strip().strip('.!?"\'')

        if "mock" in clean_query.lower() or "lead #1" in clean_query.lower():
            context = VisualizationAgentContext(
                lead_id="lead_001",
                business_name="Mock Restaurants 1",
                category="Restaurant",
                solution_type="Restaurant Digital Platform",
                modules=["Interactive Menu", "Reservations", "Admin Dashboard"],
                features=["Payment Integration", "Booking Calendar"]
            )
            return {"found": True, "isolated_context": context.model_dump()}

        async with AsyncSessionLocal() as db:
            try:
                stmt = select(Lead).filter(Lead.business_name.ilike(f"%{clean_query}%")).limit(1)
                lead = (await db.execute(stmt)).scalar_one_or_none()
                if not lead:
                    return {"found": False, "error": f"Lead not found for '{clean_query}'."}

                sol_artifact = artifact_registry.get_latest(lead.id, "BusinessSolutionArtifact")
                prop_artifact = artifact_registry.get_latest(lead.id, "ProposalArtifact")
                
                if not sol_artifact or not prop_artifact:
                    return {"found": False, "error": "Missing Solution or Proposal Artifacts."}

                context = VisualizationAgentContext(
                    lead_id=lead.id,
                    business_name=lead.business_name,
                    category=lead.business.get("category", "Unknown"),
                    solution_type=sol_artifact.solution_type,
                    modules=[m.name for m in sol_artifact.modules],
                    features=[f.name for f in sol_artifact.features]
                )
                return {"found": True, "isolated_context": context.model_dump()}
            except ValueError as ve:
                return {"found": False, "error": str(ve)}

# --- 2. Visualization Evaluator ---
class VisEvalInput(BaseModel):
    context: Dict[str, Any] = Field(...)

class VisEvalOutput(BaseModel):
    artifact: Dict[str, Any]

class VisualizationEvaluatorTool(BaseTool):
    name: str = "visualization_evaluator"
    description: str = "Generates UX and UI architecture blueprint."
    input_schema = VisEvalInput
    output_schema = VisEvalOutput
    capabilities = ["ux_architecture"]
    permissions = ["read"]

    async def execute(self, context: Dict[str, Any], **kwargs) -> dict:
        engine = VisualizationEngine()
        valid_context = VisualizationAgentContext(**context)
        artifact = engine.architect_ux(valid_context, "agent_session")
        return {"artifact": artifact.model_dump(mode="json")}

# --- 3. Visualization Saver ---
class VisSaverInput(BaseModel):
    artifact: Dict[str, Any] = Field(...)

class VisSaverOutput(BaseModel):
    success: bool
    artifact_id: Optional[str] = None
    version: int = 0
    validation: str = "Pending"
    registered: bool = False

class VisualizationSaverTool(BaseTool):
    name: str = "visualization_artifact_saver"
    description: str = "Persists VisualizationArtifact and registers it with the Registry."
    input_schema = VisSaverInput
    output_schema = VisSaverOutput
    capabilities = ["write"]
    permissions = ["write", "database"]

    async def execute(self, artifact: Dict[str, Any], **kwargs) -> dict:
        from app.schemas.shared_artifacts import VisualizationArtifact
        artifact_obj = VisualizationArtifact(**artifact)
        
        is_valid = artifact_registry.validate(artifact_obj)
        if not is_valid:
            return {"success": False, "validation": "Failed", "registered": False}

        async with AsyncSessionLocal() as db:
            repo = VisualizationRepository(db)
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