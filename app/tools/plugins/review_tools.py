"""
NapsterTec AI - Engineering Review Tools
Module: app/tools/plugins/review_tools.py
"""
import logging
from typing import Dict, Any, Optional
from pydantic import BaseModel, Field

from app.tools.base_tool import BaseTool
from app.database import AsyncSessionLocal
from sqlalchemy import select
from app.models.lead import Lead
from app.schemas.shared_artifacts import ReviewAgentContext, ReviewArtifact
from app.services.review_engine import ReviewEngine
from app.repositories.review_repository import ReviewRepository

from app.engine.artifact_registry import artifact_registry

logger = logging.getLogger(__name__)

# --- 1. Review Context Builder ---
class RevContextInput(BaseModel):
    query: str = Field(...)

class RevContextOutput(BaseModel):
    found: bool
    isolated_context: Optional[Dict[str, Any]] = Field(default_factory=dict)
    error: Optional[str] = None

class ReviewContextBuilderTool(BaseTool):
    name: str = "review_context_builder"
    description: str = "Merges Technical and Implementation artifacts for Governance Review."
    input_schema = RevContextInput
    output_schema = RevContextOutput
    capabilities = ["database"]
    permissions = ["read", "database"]

    async def execute(self, query: str, **kwargs) -> dict:
        clean_query = query.lower()
        if " for " in clean_query:
            clean_query = query[clean_query.index(" for ") + 4:].strip().strip('.!?"\'')

        if "mock" in clean_query.lower() or "lead #1" in clean_query.lower():
            context = ReviewAgentContext(
                lead_id="lead_001",
                business_name="Mock Restaurants 1",
                architecture_pattern="Modular Monolith",
                implemented_files=20,
                implemented_components=7,
                implemented_apis=6
            )
            return {"found": True, "isolated_context": context.model_dump()}

        async with AsyncSessionLocal() as db:
            try:
                stmt = select(Lead).filter(Lead.business_name.ilike(f"%{clean_query}%")).limit(1)
                lead = (await db.execute(stmt)).scalar_one_or_none()
                if not lead:
                    return {"found": False, "error": f"Lead not found for '{clean_query}'."}

                tech_art = artifact_registry.get_latest(lead.id, "TechnicalArchitectureArtifact")
                impl_art = artifact_registry.get_latest(lead.id, "ImplementationArtifact")
                
                if not tech_art or not impl_art:
                    return {"found": False, "error": "Missing Technical or Implementation Artifacts."}

                context = ReviewAgentContext(
                    lead_id=lead.id,
                    business_name=lead.business_name,
                    architecture_pattern=tech_art.architecture_pattern,
                    implemented_files=len(impl_art.files_generated) + len(impl_art.files_updated),
                    implemented_components=len(impl_art.components_created),
                    implemented_apis=len(impl_art.apis_created)
                )
                return {"found": True, "isolated_context": context.model_dump()}
            except ValueError as ve:
                return {"found": False, "error": str(ve)}

# --- 2. Review Evaluator ---
class RevEvalInput(BaseModel):
    context: Dict[str, Any] = Field(...)

class RevEvalOutput(BaseModel):
    artifact: Dict[str, Any]

class ReviewEvaluatorTool(BaseTool):
    name: str = "review_evaluator"
    description: str = "Audits software implementation against technical architecture."
    input_schema = RevEvalInput
    output_schema = RevEvalOutput
    capabilities = ["code_review"]
    permissions = ["read"]

    async def execute(self, context: Dict[str, Any], **kwargs) -> dict:
        engine = ReviewEngine()
        valid_context = ReviewAgentContext(**context)
        artifact = engine.evaluate_implementation(valid_context, "agent_session")
        return {"artifact": artifact.model_dump(mode="json")}

# --- 3. Review Saver ---
class RevSaverInput(BaseModel):
    artifact: Dict[str, Any] = Field(...)

class RevSaverOutput(BaseModel):
    success: bool
    artifact_id: Optional[str] = None
    version: int = 0
    validation: str = "Pending"
    registered: bool = False

class ReviewSaverTool(BaseTool):
    name: str = "review_artifact_saver"
    description: str = "Persists ReviewArtifact and registers it."
    input_schema = RevSaverInput
    output_schema = RevSaverOutput
    capabilities = ["write"]
    permissions = ["write", "database"]

    async def execute(self, artifact: Dict[str, Any], **kwargs) -> dict:
        from app.schemas.shared_artifacts import ReviewArtifact
        artifact_obj = ReviewArtifact(**artifact)
        
        is_valid = artifact_registry.validate(artifact_obj)
        if not is_valid:
            return {"success": False, "validation": "Failed", "registered": False}

        async with AsyncSessionLocal() as db:
            repo = ReviewRepository(db)
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