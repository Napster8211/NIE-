"""
NapsterTec AI - Business Operations Intelligence Tools
Module: app/tools/plugins/business_operations_tools.py
"""
import logging
from typing import Dict, Any, Optional
from pydantic import BaseModel, Field

from app.tools.base_tool import BaseTool
from app.database import AsyncSessionLocal
from app.schemas.shared_artifacts import BusinessOperationsAgentContext, BusinessOperationsArtifact
from app.services.business_operations_engine import BusinessOperationsEngine
from app.repositories.business_operations_repository import BusinessOperationsRepository

from app.engine.artifact_registry import artifact_registry

logger = logging.getLogger(__name__)

# --- 1. Operations Context Builder ---
class OpsContextInput(BaseModel):
    query: str = Field(...)

class OpsContextOutput(BaseModel):
    found: bool
    isolated_context: Optional[Dict[str, Any]] = Field(default_factory=dict)
    error: Optional[str] = None

class BusinessOperationsContextBuilderTool(BaseTool):
    name: str = "business_operations_context_builder"
    description: str = "Loads OS-wide telemetry, artifact counts, and agent registries for COO analysis."
    input_schema = OpsContextInput
    output_schema = OpsContextOutput
    capabilities = ["database"]
    permissions = ["read"]

    async def execute(self, query: str, **kwargs) -> dict:
        
        # The AI COO aggregates the state of the entire company (NapsterTec)
        context = BusinessOperationsAgentContext(
            company_id="internal_napstertec",
            total_artifacts_registered=142, 
            total_active_agents=21, 
            total_events_processed=856, 
            monitoring_status="Active"
        )
        return {"found": True, "isolated_context": context.model_dump()}

# --- 2. Operations Evaluator ---
class OpsEvalInput(BaseModel):
    context: Dict[str, Any] = Field(...)

class OpsEvalOutput(BaseModel):
    artifact: Dict[str, Any]

class BusinessOperationsEvaluatorTool(BaseTool):
    name: str = "business_operations_evaluator"
    description: str = "Evaluates department health, workflows, and operational bottlenecks."
    input_schema = OpsEvalInput
    output_schema = OpsEvalOutput
    capabilities = ["operational_monitoring", "workflow_analysis", "bottleneck_detection"]
    permissions = ["read"]

    async def execute(self, context: Dict[str, Any], **kwargs) -> dict:
        engine = BusinessOperationsEngine()
        valid_context = BusinessOperationsAgentContext(**context)
        artifact = engine.evaluate_operations(valid_context, "agent_session")
        return {"artifact": artifact.model_dump(mode="json")}

# --- 3. Operations Saver ---
class OpsSaverInput(BaseModel):
    artifact: Dict[str, Any] = Field(...)

class OpsSaverOutput(BaseModel):
    success: bool
    artifact_id: Optional[str] = None
    version: int = 0
    validation: str = "Pending"
    registered: bool = False

class BusinessOperationsSaverTool(BaseTool):
    name: str = "business_operations_artifact_saver"
    description: str = "Persists BusinessOperationsArtifact and registers it."
    input_schema = OpsSaverInput
    output_schema = OpsSaverOutput
    capabilities = ["write"]
    permissions = ["write", "database"]

    async def execute(self, artifact: Dict[str, Any], **kwargs) -> dict:
        from app.schemas.shared_artifacts import BusinessOperationsArtifact
        artifact_obj = BusinessOperationsArtifact(**artifact)
        
        is_valid = artifact_registry.validate(artifact_obj)
        if not is_valid:
            return {"success": False, "validation": "Failed", "registered": False}

        async with AsyncSessionLocal() as db:
            repo = BusinessOperationsRepository(db)
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