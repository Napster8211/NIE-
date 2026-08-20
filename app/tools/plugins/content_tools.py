"""
NapsterTec AI - Content Intelligence Tools
Module: app/tools/plugins/content_tools.py
"""
import logging
from typing import Dict, Any, Optional
from pydantic import BaseModel, Field

from app.tools.base_tool import BaseTool
from app.database import AsyncSessionLocal
from app.schemas.shared_artifacts import ContentAgentContext, ContentArtifact
from app.services.content_engine import ContentEngine
from app.repositories.content_repository import ContentRepository

from app.engine.artifact_registry import artifact_registry

logger = logging.getLogger(__name__)

# --- 1. Content Context Builder ---
class CntContextInput(BaseModel):
    query: str = Field(...)

class CntContextOutput(BaseModel):
    found: bool
    isolated_context: Optional[Dict[str, Any]] = Field(default_factory=dict)
    error: Optional[str] = None

class ContentContextBuilderTool(BaseTool):
    name: str = "content_context_builder"
    description: str = "Loads company state, milestones, and CRM data to build a strategic content context."
    input_schema = CntContextInput
    output_schema = CntContextOutput
    capabilities = ["database"]
    permissions = ["read"]

    async def execute(self, query: str, **kwargs) -> dict:
        clean_query = query.lower()

        # Deterministic Internal Context Load for NapsterTec
        if "napstertec" in clean_query or "strategy" in clean_query:
            context = ContentAgentContext(
                company_id="internal_napstertec",
                company_name="NapsterTec AI",
                active_projects=[
                    "NapsterTec Intelligence Engine OS", 
                    "LeadEngine AI B2B Dashboard", 
                    "VoiceCom Nexus Commerce Platform"
                ],
                recent_deployments=[
                    "Tacorabama Restaurant Digital Platform",
                    "Napster Data Hub Reselling Portal"
                ],
                crm_insights="High engagement from restaurant sector; increasing demand for automated lead generation.",
                brand_tone=["Professional", "Authoritative", "Innovative", "Educational"]
            )
            return {"found": True, "isolated_context": context.model_dump()}

        return {"found": False, "error": "Insufficient Marketing Context: Could not identify target company or business entity."}

# --- 2. Content Evaluator ---
class CntEvalInput(BaseModel):
    context: Dict[str, Any] = Field(...)

class CntEvalOutput(BaseModel):
    artifact: Dict[str, Any]

class ContentEvaluatorTool(BaseTool):
    name: str = "content_evaluator"
    description: str = "Generates a deterministic marketing strategy and content calendar."
    input_schema = CntEvalInput
    output_schema = CntEvalOutput
    capabilities = ["content_strategy"]
    permissions = ["read"]

    async def execute(self, context: Dict[str, Any], **kwargs) -> dict:
        engine = ContentEngine()
        valid_context = ContentAgentContext(**context)
        artifact = engine.plan_strategy(valid_context, "agent_session")
        return {"artifact": artifact.model_dump(mode="json")}

# --- 3. Content Saver ---
class CntSaverInput(BaseModel):
    artifact: Dict[str, Any] = Field(...)

class CntSaverOutput(BaseModel):
    success: bool
    artifact_id: Optional[str] = None
    version: int = 0
    validation: str = "Pending"
    registered: bool = False

class ContentSaverTool(BaseTool):
    name: str = "content_artifact_saver"
    description: str = "Persists ContentArtifact and registers it."
    input_schema = CntSaverInput
    output_schema = CntSaverOutput
    capabilities = ["write"]
    permissions = ["write", "database"]

    async def execute(self, artifact: Dict[str, Any], **kwargs) -> dict:
        from app.schemas.shared_artifacts import ContentArtifact
        artifact_obj = ContentArtifact(**artifact)
        
        is_valid = artifact_registry.validate(artifact_obj)
        if not is_valid:
            return {"success": False, "validation": "Failed", "registered": False}

        async with AsyncSessionLocal() as db:
            repo = ContentRepository(db)
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