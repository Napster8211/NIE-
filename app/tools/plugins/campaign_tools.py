"""
NapsterTec AI - Campaign Intelligence Tools
Module: app/tools/plugins/campaign_tools.py
"""
import logging
from typing import Dict, Any, Optional
from pydantic import BaseModel, Field

from app.tools.base_tool import BaseTool
from app.database import AsyncSessionLocal
from app.schemas.shared_artifacts import CampaignAgentContext, CampaignArtifact
from app.services.campaign_engine import CampaignEngine
from app.repositories.campaign_repository import CampaignRepository

from app.engine.artifact_registry import artifact_registry

logger = logging.getLogger(__name__)

# --- 1. Campaign Context Builder ---
class CmpContextInput(BaseModel):
    query: str = Field(...)

class CmpContextOutput(BaseModel):
    found: bool
    isolated_context: Optional[Dict[str, Any]] = Field(default_factory=dict)
    error: Optional[str] = None

class CampaignContextBuilderTool(BaseTool):
    name: str = "campaign_context_builder"
    description: str = "Loads Content and Social Artifacts to build a Campaign Context."
    input_schema = CmpContextInput
    output_schema = CmpContextOutput
    capabilities = ["database"]
    permissions = ["read"]

    async def execute(self, query: str, **kwargs) -> dict:
        clean_query = query.lower()

        # Deterministic Internal Context Load for NapsterTec Campaign Orchestration
        if "napstertec" in clean_query or "campaign" in clean_query:
            
            campaign_name = "Restaurant Digital Transformation Campaign"
            
            # Try to fetch from Artifact Registry first
            cnt_art = artifact_registry.get_latest("internal_napstertec", "ContentArtifact")
            soc_art = artifact_registry.get_latest("internal_napstertec", "SocialArtifact")
            
            # Robust mock fallback if registry is empty due to server restart
            if not cnt_art or not soc_art:
                context = CampaignAgentContext(
                    company_id="internal_napstertec",
                    target_campaign_name=campaign_name,
                    business_objective="Brand Awareness & B2B Lead Generation",
                    target_audience=["SMEs", "Restaurant Owners", "Logistics Managers"],
                    channels=["LinkedIn", "X", "Instagram", "Blog", "Newsletter"],
                    available_content=5,
                    available_social_posts=3
                )
                return {"found": True, "isolated_context": context.model_dump()}

            context = CampaignAgentContext(
                company_id="internal_napstertec",
                target_campaign_name=campaign_name,
                business_objective=cnt_art.business_objective,
                target_audience=cnt_art.target_audience,
                channels=cnt_art.platform_recommendations,
                available_content=len(cnt_art.campaigns),
                available_social_posts=len(soc_art.posts)
            )
            return {"found": True, "isolated_context": context.model_dump()}

        return {"found": False, "error": "Insufficient Context: Could not identify target campaign parameters."}

# --- 2. Campaign Evaluator ---
class CmpEvalInput(BaseModel):
    context: Dict[str, Any] = Field(...)

class CmpEvalOutput(BaseModel):
    artifact: Dict[str, Any]

class CampaignEvaluatorTool(BaseTool):
    name: str = "campaign_evaluator"
    description: str = "Orchestrates marketing assets into a measurable publishing sequence."
    input_schema = CmpEvalInput
    output_schema = CmpEvalOutput
    capabilities = ["campaign_planning"]
    permissions = ["read"]

    async def execute(self, context: Dict[str, Any], **kwargs) -> dict:
        engine = CampaignEngine()
        valid_context = CampaignAgentContext(**context)
        artifact = engine.orchestrate_campaign(valid_context, "agent_session")
        return {"artifact": artifact.model_dump(mode="json")}

# --- 3. Campaign Saver ---
class CmpSaverInput(BaseModel):
    artifact: Dict[str, Any] = Field(...)

class CmpSaverOutput(BaseModel):
    success: bool
    artifact_id: Optional[str] = None
    version: int = 0
    validation: str = "Pending"
    registered: bool = False

class CampaignSaverTool(BaseTool):
    name: str = "campaign_artifact_saver"
    description: str = "Persists CampaignArtifact and registers it."
    input_schema = CmpSaverInput
    output_schema = CmpSaverOutput
    capabilities = ["write"]
    permissions = ["write", "database"]

    async def execute(self, artifact: Dict[str, Any], **kwargs) -> dict:
        from app.schemas.shared_artifacts import CampaignArtifact
        artifact_obj = CampaignArtifact(**artifact)
        
        is_valid = artifact_registry.validate(artifact_obj)
        if not is_valid:
            return {"success": False, "validation": "Failed", "registered": False}

        async with AsyncSessionLocal() as db:
            repo = CampaignRepository(db)
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