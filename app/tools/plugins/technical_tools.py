"""
NapsterTec AI - Technical Architecture Tools
Module: app/tools/plugins/technical_tools.py
"""
import logging
from typing import Dict, Any, Optional
from pydantic import BaseModel, Field

from app.tools.base_tool import BaseTool
from app.database import AsyncSessionLocal
from sqlalchemy import select
from app.models.lead import Lead
from app.schemas.shared_artifacts import TechnicalAgentContext, TechnicalArchitectureArtifact
from app.services.technical_engine import TechnicalEngine
from app.repositories.technical_repository import TechnicalRepository

from app.engine.artifact_registry import artifact_registry

logger = logging.getLogger(__name__)

# --- 1. Technical Context Builder ---
class TechContextInput(BaseModel):
    query: str = Field(...)

class TechContextOutput(BaseModel):
    found: bool
    isolated_context: Optional[Dict[str, Any]] = Field(default_factory=dict)
    error: Optional[str] = None

class TechnicalContextBuilderTool(BaseTool):
    name: str = "technical_context_builder"
    description: str = "Merges Business and Visualization artifacts into an isolated TechnicalContext."
    input_schema = TechContextInput
    output_schema = TechContextOutput
    capabilities = ["database"]
    permissions = ["read", "database"]

    async def execute(self, query: str, **kwargs) -> dict:
        clean_query = query.lower()
        if " for " in clean_query:
            clean_query = query[clean_query.index(" for ") + 4:].strip().strip('.!?"\'')

        if "mock" in clean_query.lower() or "lead #1" in clean_query.lower():
            context = TechnicalAgentContext(
                lead_id="lead_001",
                business_name="Mock Restaurants 1",
                category="Restaurant",
                solution_type="Restaurant Digital Platform",
                modules=["Interactive Menu", "Reservations", "Admin Dashboard"],
                integrations=["Google Analytics", "WhatsApp", "Paystack"],
                user_roles=["Guest", "Customer", "Staff", "Administrator"]
            )
            return {"found": True, "isolated_context": context.model_dump()}

        async with AsyncSessionLocal() as db:
            try:
                stmt = select(Lead).filter(Lead.business_name.ilike(f"%{clean_query}%")).limit(1)
                lead = (await db.execute(stmt)).scalar_one_or_none()
                if not lead:
                    return {"found": False, "error": f"Lead not found for '{clean_query}'."}

                sol_art = artifact_registry.get_latest(lead.id, "BusinessSolutionArtifact")
                vis_art = artifact_registry.get_latest(lead.id, "VisualizationArtifact")
                
                if not sol_art or not vis_art:
                    return {"found": False, "error": "Missing Solution or Visualization Artifacts."}

                context = TechnicalAgentContext(
                    lead_id=lead.id,
                    business_name=lead.business_name,
                    category=lead.business.get("category", "Unknown"),
                    solution_type=sol_art.solution_type,
                    modules=[m.name for m in sol_art.modules],
                    integrations=[i.name for i in sol_art.integrations],
                    user_roles=[r.role_name for r in vis_art.user_roles]
                )
                return {"found": True, "isolated_context": context.model_dump()}
            except ValueError as ve:
                return {"found": False, "error": str(ve)}

# --- 2. Technical Evaluator ---
class TechEvalInput(BaseModel):
    context: Dict[str, Any] = Field(...)

class TechEvalOutput(BaseModel):
    artifact: Dict[str, Any]

class TechnicalEvaluatorTool(BaseTool):
    name: str = "technical_evaluator"
    description: str = "Generates a deterministic system architecture blueprint."
    input_schema = TechEvalInput
    output_schema = TechEvalOutput
    capabilities = ["system_architecture"]
    permissions = ["read"]

    async def execute(self, context: Dict[str, Any], **kwargs) -> dict:
        engine = TechnicalEngine()
        valid_context = TechnicalAgentContext(**context)
        artifact = engine.architect_system(valid_context, "agent_session")
        return {"artifact": artifact.model_dump(mode="json")}

# --- 3. Technical Saver ---
class TechSaverInput(BaseModel):
    artifact: Dict[str, Any] = Field(...)

class TechSaverOutput(BaseModel):
    success: bool
    artifact_id: Optional[str] = None
    version: int = 0
    validation: str = "Pending"
    registered: bool = False

class TechnicalSaverTool(BaseTool):
    name: str = "technical_artifact_saver"
    description: str = "Persists TechnicalArchitectureArtifact and registers it."
    input_schema = TechSaverInput
    output_schema = TechSaverOutput
    capabilities = ["write"]
    permissions = ["write", "database"]

    async def execute(self, artifact: Dict[str, Any], **kwargs) -> dict:
        from app.schemas.shared_artifacts import TechnicalArchitectureArtifact
        artifact_obj = TechnicalArchitectureArtifact(**artifact)
        
        is_valid = artifact_registry.validate(artifact_obj)
        if not is_valid:
            return {"success": False, "validation": "Failed", "registered": False}

        async with AsyncSessionLocal() as db:
            repo = TechnicalRepository(db)
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