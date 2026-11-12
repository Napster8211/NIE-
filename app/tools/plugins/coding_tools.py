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

        async with AsyncSessionLocal() as db:
            try:
                lead = None
                if any(k in clean_query for k in ("lead #1", "top lead", "latest", "first lead", "top")):
                    stmt = select(Lead).filter(~Lead.business_name.ilike("%mock%")).order_by(Lead.created_at.desc()).limit(1)
                    lead = (await db.execute(stmt)).scalar_one_or_none()

                if not lead and clean_query and not any(k in clean_query for k in ("lead", "business", "restaurant", "code", "prototype", "build")):
                    stmt = select(Lead).filter(Lead.business_name.ilike(f"%{clean_query}%")).limit(1)
                    lead = (await db.execute(stmt)).scalar_one_or_none()

                if not lead:
                    stmt = select(Lead).filter(~Lead.business_name.ilike("%mock%")).order_by(Lead.created_at.desc()).limit(1)
                    lead = (await db.execute(stmt)).scalar_one_or_none()

                if not lead:
                    return {"found": False, "error": f"Lead not found for '{clean_query}'."}

                # Retrieve Solution Blueprint or on-demand synthesis
                sol_artifact = artifact_registry.get_latest(lead.id, "BusinessSolutionArtifact")
                if not sol_artifact:
                    from app.models.solution import BusinessSolutionIntelligence
                    from app.schemas.shared_artifacts import BusinessSolutionArtifact
                    sol_stmt = select(BusinessSolutionIntelligence).where(BusinessSolutionIntelligence.lead_id == lead.id).order_by(BusinessSolutionIntelligence.version.desc()).limit(1)
                    sol_rec = (await db.execute(sol_stmt)).scalar_one_or_none()
                    if sol_rec and sol_rec.report:
                        sol_artifact = BusinessSolutionArtifact(**sol_rec.report)

                module_names = [m.name for m in sol_artifact.modules] if sol_artifact else ["Branding & Landing", "Interactive Menu", "Direct Ordering", "Admin Portal"]

                context = CodingAgentContext(
                    lead_id=lead.id,
                    business_name=lead.business_name,
                    architecture_pattern="Modular Edge Application (Next.js + FastAPI)",
                    frontend_stack="Next.js / React (TailwindCSS)",
                    backend_stack="FastAPI (Async Python)",
                    database="PostgreSQL (Supabase)",
                    modules=module_names,
                    pages=["Home", "Services", "Menu", "Booking", "Admin"],
                    api_architecture={"style": "RESTful OpenAPI with SSL Encryption"}
                )
                return {"found": True, "isolated_context": context.model_dump()}
            except Exception as ve:
                logger.error("[CodingContextBuilder] Error: %s", ve, exc_info=True)
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