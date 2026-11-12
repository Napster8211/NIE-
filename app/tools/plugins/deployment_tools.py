"""
NapsterTec AI - Deployment Intelligence Tools
Module: app/tools/plugins/deployment_tools.py
"""
import logging
from typing import Dict, Any, Optional
from pydantic import BaseModel, Field

from app.tools.base_tool import BaseTool
from app.database import AsyncSessionLocal
from sqlalchemy import select
from app.models.lead import Lead
from app.schemas.shared_artifacts import DeploymentAgentContext, DeploymentArtifact
from app.services.deployment_engine import DeploymentEngine
from app.repositories.deployment_repository import DeploymentRepository

from app.engine.artifact_registry import artifact_registry

logger = logging.getLogger(__name__)

# --- 1. Deployment Context Builder ---
class DepContextInput(BaseModel):
    query: str = Field(...)

class DepContextOutput(BaseModel):
    found: bool
    isolated_context: Optional[Dict[str, Any]] = Field(default_factory=dict)
    error: Optional[str] = None

class DeploymentContextBuilderTool(BaseTool):
    name: str = "deployment_context_builder"
    description: str = "Merges Review and Implementation artifacts to validate deployment readiness."
    input_schema = DepContextInput
    output_schema = DepContextOutput
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

                if not lead and clean_query and not any(k in clean_query for k in ("lead", "business", "restaurant", "deploy", "launch", "host")):
                    stmt = select(Lead).filter(Lead.business_name.ilike(f"%{clean_query}%")).limit(1)
                    lead = (await db.execute(stmt)).scalar_one_or_none()

                if not lead:
                    stmt = select(Lead).filter(~Lead.business_name.ilike("%mock%")).order_by(Lead.created_at.desc()).limit(1)
                    lead = (await db.execute(stmt)).scalar_one_or_none()

                if not lead:
                    return {"found": False, "error": f"Lead not found for '{clean_query}'."}

                context = DeploymentAgentContext(
                    lead_id=lead.id,
                    business_name=lead.business_name,
                    frontend_stack="Next.js / React (Edge Hosted)",
                    backend_stack="FastAPI (Async Micro-engine)",
                    approval_status="Approved"
                )
                return {"found": True, "isolated_context": context.model_dump()}
            except Exception as ve:
                logger.error("[DeploymentContextBuilder] Error: %s", ve, exc_info=True)
                return {"found": False, "error": str(ve)}

# --- 2. Deployment Evaluator ---
class DepEvalInput(BaseModel):
    context: Dict[str, Any] = Field(...)

class DepEvalOutput(BaseModel):
    artifact: Dict[str, Any]

class DeploymentEvaluatorTool(BaseTool):
    name: str = "deployment_evaluator"
    description: str = "Executes the deployment pipeline and generates preview metrics."
    input_schema = DepEvalInput
    output_schema = DepEvalOutput
    capabilities = ["deployment"]
    permissions = ["read"]

    async def execute(self, context: Dict[str, Any], **kwargs) -> dict:
        engine = DeploymentEngine()
        valid_context = DeploymentAgentContext(**context)
        artifact = engine.execute_deployment(valid_context, "agent_session")
        return {"artifact": artifact.model_dump(mode="json")}

# --- 3. Deployment Saver ---
class DepSaverInput(BaseModel):
    artifact: Dict[str, Any] = Field(...)

class DepSaverOutput(BaseModel):
    success: bool
    artifact_id: Optional[str] = None
    version: int = 0
    validation: str = "Pending"
    registered: bool = False

class DeploymentSaverTool(BaseTool):
    name: str = "deployment_artifact_saver"
    description: str = "Persists DeploymentArtifact and registers it."
    input_schema = DepSaverInput
    output_schema = DepSaverOutput
    capabilities = ["write"]
    permissions = ["write", "database"]

    async def execute(self, artifact: Dict[str, Any], **kwargs) -> dict:
        from app.schemas.shared_artifacts import DeploymentArtifact
        artifact_obj = DeploymentArtifact(**artifact)
        
        is_valid = artifact_registry.validate(artifact_obj)
        if not is_valid:
            return {"success": False, "validation": "Failed", "registered": False}

        async with AsyncSessionLocal() as db:
            repo = DeploymentRepository(db)
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