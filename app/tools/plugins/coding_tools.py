"""
NapsterTec AI - Coding Intelligence Tools
Module: app/tools/plugins/coding_tools.py
"""
import logging
from typing import Dict, Any, Optional
from pydantic import BaseModel, Field

from app.tools.base_tool import BaseTool
from app.database import AsyncSessionLocal
from sqlalchemy import select
from app.models.lead import Lead
from app.schemas.shared_artifacts import CodingAgentContext, ImplementationArtifact
from app.services.coding_engine import CodingEngine
from app.repositories.coding_repository import CodingRepository

from app.engine.artifact_registry import artifact_registry

logger = logging.getLogger(__name__)

# --- 1. Coding Context Builder ---
class CodeContextInput(BaseModel):
    query: str = Field(...)

class CodeContextOutput(BaseModel):
    found: bool
    isolated_context: Optional[Dict[str, Any]] = Field(default_factory=dict)
    error: Optional[str] = None

class CodingContextBuilderTool(BaseTool):
    name: str = "coding_context_builder"
    description: str = "Merges Technical and Visualization artifacts into an isolated CodingContext."
    input_schema = CodeContextInput
    output_schema = CodeContextOutput
    capabilities = ["database"]
    permissions = ["read", "database"]

    async def execute(self, query: str, **kwargs) -> dict:
        clean_query = query.lower()
        if " for " in clean_query:
            clean_query = query[clean_query.index(" for ") + 4:].strip().strip('.!?"\'')

        if "mock" in clean_query.lower() or "lead #1" in clean_query.lower():
            context = CodingAgentContext(
                lead_id="lead_001",
                business_name="Mock Restaurants 1",
                architecture_pattern="Modular Monolith",
                frontend_stack="React (Next.js)",
                backend_stack="FastAPI",
                database="PostgreSQL",
                modules=["Reservations", "Menu", "Dashboard"],
                pages=["Home", "Booking", "Admin"],
                api_architecture={"style": "REST"}
            )
            return {"found": True, "isolated_context": context.model_dump()}

        async with AsyncSessionLocal() as db:
            try:
                stmt = select(Lead).filter(Lead.business_name.ilike(f"%{clean_query}%")).limit(1)
                lead = (await db.execute(stmt)).scalar_one_or_none()
                if not lead:
                    return {"found": False, "error": f"Lead not found for '{clean_query}'."}

                tech_art = artifact_registry.get_latest(lead.id, "TechnicalArchitectureArtifact")
                vis_art = artifact_registry.get_latest(lead.id, "VisualizationArtifact")
                
                if not tech_art or not vis_art:
                    return {"found": False, "error": "Missing Technical or Visualization Artifacts."}

                context = CodingAgentContext(
                    lead_id=lead.id,
                    business_name=lead.business_name,
                    architecture_pattern=tech_art.architecture_pattern,
                    frontend_stack=tech_art.frontend_architecture.get("framework", "React"),
                    backend_stack=tech_art.backend_architecture.get("framework", "FastAPI"),
                    database=tech_art.database_architecture.get("type", "PostgreSQL"),
                    modules=[m.get("name") for m in vis_art.dashboard_architecture],
                    pages=vis_art.page_hierarchy,
                    api_architecture=tech_art.api_architecture
                )
                return {"found": True, "isolated_context": context.model_dump()}
            except ValueError as ve:
                return {"found": False, "error": str(ve)}

# --- 2. Coding Evaluator ---
class CodeEvalInput(BaseModel):
    context: Dict[str, Any] = Field(...)

class CodeEvalOutput(BaseModel):
    artifact: Dict[str, Any]

class CodingEvaluatorTool(BaseTool):
    name: str = "coding_evaluator"
    description: str = "Generates a deterministic project codebase implementation artifact."
    input_schema = CodeEvalInput
    output_schema = CodeEvalOutput
    capabilities = ["project_generation"]
    permissions = ["read"]

    async def execute(self, context: Dict[str, Any], **kwargs) -> dict:
        engine = CodingEngine()
        valid_context = CodingAgentContext(**context)
        artifact = engine.implement_architecture(valid_context, "agent_session")
        return {"artifact": artifact.model_dump(mode="json")}

# --- 3. Coding Saver ---
class CodeSaverInput(BaseModel):
    artifact: Dict[str, Any] = Field(...)

class CodeSaverOutput(BaseModel):
    success: bool
    artifact_id: Optional[str] = None
    version: int = 0
    validation: str = "Pending"
    registered: bool = False

class CodingSaverTool(BaseTool):
    name: str = "coding_artifact_saver"
    description: str = "Persists ImplementationArtifact and registers it with the Registry."
    input_schema = CodeSaverInput
    output_schema = CodeSaverOutput
    capabilities = ["write"]
    permissions = ["write", "database"]

    async def execute(self, artifact: Dict[str, Any], **kwargs) -> dict:
        from app.schemas.shared_artifacts import ImplementationArtifact
        artifact_obj = ImplementationArtifact(**artifact)
        
        is_valid = artifact_registry.validate(artifact_obj)
        if not is_valid:
            return {"success": False, "validation": "Failed", "registered": False}

        async with AsyncSessionLocal() as db:
            repo = CodingRepository(db)
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