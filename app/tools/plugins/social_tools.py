"""
NapsterTec AI - Social Intelligence Tools
Module: app/tools/plugins/social_tools.py
"""
import logging
from typing import Dict, Any, Optional
from pydantic import BaseModel, Field

from app.tools.base_tool import BaseTool
from app.database import AsyncSessionLocal
from app.schemas.shared_artifacts import SocialAgentContext, SocialArtifact
from app.services.social_engine import SocialEngine
from app.repositories.social_repository import SocialRepository

from app.engine.artifact_registry import artifact_registry

logger = logging.getLogger(__name__)

# --- 1. Social Context Builder ---
class SocContextInput(BaseModel):
    query: str = Field(...)

class SocContextOutput(BaseModel):
    found: bool
    isolated_context: Optional[Dict[str, Any]] = Field(default_factory=dict)
    error: Optional[str] = None

class SocialContextBuilderTool(BaseTool):
    name: str = "social_context_builder"
    description: str = "Loads the Content Strategy Artifact to build a Social Context."
    input_schema = SocContextInput
    output_schema = SocContextOutput
    capabilities = ["database"]
    permissions = ["read"]

    async def execute(self, query: str, **kwargs) -> dict:
        clean_query = query.lower()

        # Deterministic Internal Context Load for NapsterTec
        if "napstertec" in clean_query or "assets" in clean_query or "social" in clean_query:
            
            # Robust mock context that survives Uvicorn restarts
            context = SocialAgentContext(
                company_id="internal_napstertec",
                business_objective="Brand Awareness & B2B Lead Generation",
                campaigns=[
                    {"name": "Launch Spotlight: NapsterTec Intelligence Engine OS", "objective": "Product Launch"},
                    {"name": "Client Success: Tacorabama Restaurant", "objective": "Case Study"}
                ],
                formats=["LinkedIn Article", "Short Video Demo", "Case Study PDF"],
                brand_tone=["Professional", "Authoritative", "Innovative"]
            )
            return {"found": True, "isolated_context": context.model_dump()}

        return {"found": False, "error": "Insufficient Context: Could not identify target strategy artifact."}

# --- 2. Social Evaluator ---
class SocEvalInput(BaseModel):
    context: Dict[str, Any] = Field(...)

class SocEvalOutput(BaseModel):
    artifact: Dict[str, Any]

class SocialEvaluatorTool(BaseTool):
    name: str = "social_evaluator"
    description: str = "Generates deterministic, platform-specific social media assets."
    input_schema = SocEvalInput
    output_schema = SocEvalOutput
    capabilities = ["platform_adaptation"]
    permissions = ["read"]

    async def execute(self, context: Dict[str, Any], **kwargs) -> dict:
        engine = SocialEngine()
        valid_context = SocialAgentContext(**context)
        artifact = engine.prepare_assets(valid_context, "agent_session")
        return {"artifact": artifact.model_dump(mode="json")}

# --- 3. Social Saver ---
class SocSaverInput(BaseModel):
    artifact: Dict[str, Any] = Field(...)

class SocSaverOutput(BaseModel):
    success: bool
    artifact_id: Optional[str] = None
    version: int = 0
    validation: str = "Pending"
    registered: bool = False

class SocialSaverTool(BaseTool):
    name: str = "social_artifact_saver"
    description: str = "Persists SocialArtifact and registers it."
    input_schema = SocSaverInput
    output_schema = SocSaverOutput
    capabilities = ["write"]
    permissions = ["write", "database"]

    async def execute(self, artifact: Dict[str, Any], **kwargs) -> dict:
        from app.schemas.shared_artifacts import SocialArtifact
        artifact_obj = SocialArtifact(**artifact)
        
        is_valid = artifact_registry.validate(artifact_obj)
        if not is_valid:
            return {"success": False, "validation": "Failed", "registered": False}

        async with AsyncSessionLocal() as db:
            repo = SocialRepository(db)
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