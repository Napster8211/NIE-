import logging
from typing import Dict, Any, Optional
from pydantic import BaseModel, Field

from app.tools.base_tool import BaseTool
from app.schemas.shared_artifacts import FinanceAgentContext, FinanceArtifact
from app.services.finance_engine import FinanceEngine
from app.engine.artifact_registry import artifact_registry

logger = logging.getLogger(__name__)

class FinContextInput(BaseModel):
    query: str = Field(...)

class FinContextOutput(BaseModel):
    found: bool
    isolated_context: Optional[Dict[str, Any]] = Field(default_factory=dict)
    error: Optional[str] = None

class FinanceContextBuilderTool(BaseTool):
    name: str = "finance_context_builder"
    description: str = "Loads Revenue, Operations, and Expense repositories for CFO analysis."
    input_schema = FinContextInput
    output_schema = FinContextOutput
    capabilities = ["database"]
    permissions = ["read"]

    async def execute(self, query: str, **kwargs) -> dict:
        clean_query = query.lower()
        if "napstertec" in clean_query or "finance" in clean_query or "financial" in clean_query:
            context = FinanceAgentContext(
                company_id="internal_napstertec",
                verified_revenue_pipeline=0.0,
                verified_expenses=0.0,
                monitoring_status="Active"
            )
            return {"found": True, "isolated_context": context.model_dump()}

        return {"found": False, "error": "Financial Assessment Incomplete: RevenueArtifact unavailable or entity not recognized."}

class FinEvalInput(BaseModel):
    context: Dict[str, Any] = Field(...)

class FinEvalOutput(BaseModel):
    artifact: Dict[str, Any]

class FinanceEvaluatorTool(BaseTool):
    name: str = "finance_evaluator"
    description: str = "Evaluates runway, profitability, budgets, and ROI."
    input_schema = FinEvalInput
    output_schema = FinEvalOutput
    capabilities = ["cash_flow_analysis", "runway_forecasting", "budget_management"]
    permissions = ["read"]

    async def execute(self, context: Dict[str, Any], **kwargs) -> dict:
        engine = FinanceEngine()
        valid_context = FinanceAgentContext(**context)
        artifact = engine.evaluate_finances(valid_context, "agent_session")
        return {"artifact": artifact.model_dump(mode="json")}

class FinSaverInput(BaseModel):
    artifact: Dict[str, Any] = Field(...)

class FinSaverOutput(BaseModel):
    success: bool
    artifact_id: Optional[str] = None
    version: int = 0
    validation: str = "Pending"
    registered: bool = False

class FinanceSaverTool(BaseTool):
    name: str = "finance_artifact_saver"
    description: str = "Persists FinanceArtifact and registers it."
    input_schema = FinSaverInput
    output_schema = FinSaverOutput
    capabilities = ["write"]
    permissions = ["write", "database"]

    async def execute(self, artifact: Dict[str, Any], **kwargs) -> dict:
        from app.schemas.shared_artifacts import FinanceArtifact
        artifact_obj = FinanceArtifact(**artifact)
        
        is_valid = artifact_registry.validate(artifact_obj)
        if not is_valid:
            return {"success": False, "validation": "Failed", "registered": False}

        is_reg = artifact_registry.register(artifact_obj)
            
        return {
            "success": True,
            "artifact_id": artifact_obj.artifact_id,
            "version": 1,
            "validation": "Passed",
            "registered": is_reg
        }

# Sprint 4B Observational Tools
class ObjectiveFinanceInput(BaseModel):
    objective_id: str = Field(...)

class ObjectiveFinanceOutput(BaseModel):
    snapshot: Dict[str, Any]
    assessment: Dict[str, Any]

class GetObjectiveFinancialSummaryTool(BaseTool):
    name: str = "get_objective_financial_summary"
    description: str = "Retrieves a safe, read-only financial snapshot of a specific Objective."
    input_schema = ObjectiveFinanceInput
    output_schema = ObjectiveFinanceOutput
    capabilities = ["budget_management"]
    permissions = ["read"]
    
    async def execute(self, objective_id: str, **kwargs) -> dict:
        engine = FinanceEngine()
        snap = engine.generate_snapshot(objective_id)
        ass = engine.assess_finances(snap)
        return {"snapshot": snap.model_dump(mode="json"), "assessment": ass.model_dump(mode="json")}

class AffordabilityInput(BaseModel):
    objective_id: str = Field(...)
    mission_id: str = Field(...)
    estimated_cost: float = Field(...)
    currency: str = Field(...)

class AffordabilityOutput(BaseModel):
    affordable: bool
    reason: str

class AssessAffordabilityTool(BaseTool):
    name: str = "assess_affordability"
    description: str = "Checks if an estimated cost can be absorbed by the mission budget without reserving funds."
    input_schema = AffordabilityInput
    output_schema = AffordabilityOutput
    capabilities = ["budget_management"]
    permissions = ["read"]

    async def execute(self, objective_id: str, mission_id: str, estimated_cost: float, currency: str, **kwargs) -> dict:
        from app.repositories.finance_repository import finance_repository
        from decimal import Decimal
        alloc = finance_repository.get_mission_allocation(objective_id, mission_id)
        if not alloc:
            return {"affordable": False, "reason": "No mission allocation found."}
        if alloc.currency.upper() != currency.upper():
            return {"affordable": False, "reason": f"CURRENCY_MISMATCH: Mission budget is in {alloc.currency}."}
        
        cost_dec = Decimal(str(estimated_cost))
        affordable = alloc.available_amount >= cost_dec
        reason = "Mission has sufficient available budget." if affordable else f"Mission available budget ({alloc.available_amount}) is less than estimated cost ({cost_dec})."
        return {"affordable": affordable, "reason": reason}