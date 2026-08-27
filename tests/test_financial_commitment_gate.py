import unittest
import os
import tempfile
from decimal import Decimal
from unittest.mock import MagicMock

from app.agent.agent_models import AgentContext, AgentPermission
from app.services.authorization import AuthorizationGate
from app.repositories.approval_repository import approval_repository
from app.schemas.shared_artifacts import ApprovalStatus, ApprovalRequest, ApprovalType
from app.tools.tool_executor import ToolExecutor
from app.tools.tool_registry import ToolRegistry
from app.tools.base_tool import BaseTool
from pydantic import BaseModel, Field

class FinancialInput(BaseModel):
    objective_id: str = Field(...)
    mission_id: str = Field(...)
    amount: float = Field(...)
    currency: str = Field(...)
    purpose: str = Field(...)

class FinancialOutput(BaseModel):
    transaction_id: str
    status: str

class MockFinancialTool(BaseTool):
    name = "mock_financial_tool"
    description = "Executes a mock financial commitment."
    capabilities = ["finance"]
    permissions = ["financial_commitment"]
    input_schema = FinancialInput
    output_schema = FinancialOutput
    
    @property
    def approval_required(self) -> bool:
        return True
        
    @property
    def operation_type(self) -> str:
        return "FINANCIAL_COMMITMENT"
        
    async def execute(self, objective_id: str, mission_id: str, amount: float, currency: str, purpose: str, **kwargs):
        return {"transaction_id": "tx_mock_123456", "status": "CONFIRMED"}

class TestFinancialCommitmentGate(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.storage_path = os.path.join(self.temp_dir.name, "finance_4c.json")
        
        import app.repositories.finance_repository as fr
        import app.services.authorization as auth_mod
        import app.tools.tool_executor as te_mod
        
        self.original_fin_repo_fr = fr.finance_repository
        self.original_fin_repo_auth = auth_mod.finance_repository
        self.original_fin_repo_te = te_mod.finance_repository
        
        self.fin_repo = fr.FinanceRepository(storage_path=self.storage_path)
        fr.finance_repository = self.fin_repo
        auth_mod.finance_repository = self.fin_repo
        te_mod.finance_repository = self.fin_repo
        
        self.original_list_approval = approval_repository.list_by_mission
        self.original_resolve_approval = approval_repository.resolve_approval
        
        self.registry = ToolRegistry()
        self.registry.register(MockFinancialTool())
        self.executor = ToolExecutor()

    def tearDown(self):
        import app.repositories.finance_repository as fr
        import app.services.authorization as auth_mod
        import app.tools.tool_executor as te_mod
        
        fr.finance_repository = self.original_fin_repo_fr
        auth_mod.finance_repository = self.original_fin_repo_auth
        te_mod.finance_repository = self.original_fin_repo_te
        
        approval_repository.list_by_mission = self.original_list_approval
        approval_repository.resolve_approval = self.original_resolve_approval
        self.temp_dir.cleanup()

    async def test_four_key_rule_all_missing(self):
        ctx = AgentContext(task="Pay", granted_permissions={AgentPermission.READ})
        res = await self.executor.execute_tool(MockFinancialTool(), {
            "objective_id": "obj_1",
            "mission_id": "mis_1",
            "amount": 500.0,
            "currency": "GHS",
            "purpose": "Ads",
            "context": ctx
        })
        self.assertEqual(res.status, "FAILURE")
        self.assertIn("AUTHORITY_MISSING", res.error)

    async def test_four_key_rule_missing_approval(self):
        self.fin_repo.create_objective_budget("obj_1", "GHS", 5000.0)
        self.fin_repo.allocate_to_mission("obj_1", "mis_1", 2000.0)
        
        approval_repository.list_by_mission = MagicMock(return_value=[])
        try:
            ctx = AgentContext(
                task="Pay", 
                granted_permissions={AgentPermission.FINANCIAL_COMMITMENT},
                planner_output={"mission_id": "mis_1", "materialization_id": "mat_1"}
            )
            res = await self.executor.execute_tool(MockFinancialTool(), {
                "objective_id": "obj_1",
                "mission_id": "mis_1",
                "amount": 500.0,
                "currency": "GHS",
                "purpose": "Ads",
                "context": ctx
            })
            self.assertEqual(res.status, "FAILURE")
            self.assertIn("APPROVAL_MISSING", res.error)
        finally:
            approval_repository.list_by_mission = self.original_list_approval

    async def test_four_key_rule_missing_budget(self):
        params = {
            "objective_id": "obj_missing",
            "mission_id": "mis_1",
            "amount": 500.0,
            "currency": "GHS",
            "purpose": "Ads"
        }
        fingerprint = AuthorizationGate._build_action_fingerprint("FINANCIAL_COMMITMENT", params)
        mock_app = ApprovalRequest(
            approval_id="app_test123456",
            mission_id="mis_1",
            materialization_id="mat_1",
            action="Pay Ads",
            action_fingerprint=fingerprint,
            status=ApprovalStatus.APPROVED,
            approval_type=ApprovalType.MATERIALIZATION
        )
        approval_repository.list_by_mission = MagicMock(return_value=[mock_app])
        try:
            ctx = AgentContext(
                task="Pay", 
                granted_permissions={AgentPermission.FINANCIAL_COMMITMENT},
                planner_output={"mission_id": "mis_1", "materialization_id": "mat_1"}
            )
            res = await self.executor.execute_tool(MockFinancialTool(), {**params, "context": ctx})
            self.assertEqual(res.status, "FAILURE")
            self.assertIn("BUDGET_NOT_CONFIGURED", res.error)
        finally:
            approval_repository.list_by_mission = self.original_list_approval

    async def test_four_key_rule_fully_eligible_succeeds(self):
        self.fin_repo.create_objective_budget("obj_1", "GHS", 5000.0)
        self.fin_repo.allocate_to_mission("obj_1", "mis_1", 2000.0)

        params = {
            "objective_id": "obj_1",
            "mission_id": "mis_1",
            "amount": 500.0,
            "currency": "GHS",
            "purpose": "Ads"
        }
        fingerprint = AuthorizationGate._build_action_fingerprint("FINANCIAL_COMMITMENT", params)
        mock_app = ApprovalRequest(
            approval_id="app_test123456",
            mission_id="mis_1",
            materialization_id="mat_1",
            action="Pay Ads",
            action_fingerprint=fingerprint,
            status=ApprovalStatus.APPROVED,
            approval_type=ApprovalType.MATERIALIZATION
        )
        approval_repository.list_by_mission = MagicMock(return_value=[mock_app])
        approval_repository.resolve_approval = MagicMock()
        try:
            ctx = AgentContext(
                task="Pay", 
                granted_permissions={AgentPermission.FINANCIAL_COMMITMENT},
                planner_output={"mission_id": "mis_1", "materialization_id": "mat_1"}
            )
            res = await self.executor.execute_tool(MockFinancialTool(), {**params, "context": ctx})
            self.assertEqual(res.status, "SUCCESS")
            
            alloc = self.fin_repo.get_mission_allocation("obj_1", "mis_1")
            self.assertEqual(alloc.spent_amount, Decimal("500.00"))
            self.assertEqual(alloc.available_amount, Decimal("1500.00"))
        finally:
            approval_repository.list_by_mission = self.original_list_approval
            approval_repository.resolve_approval = self.original_resolve_approval

    async def test_insufficient_mission_budget_rejected(self):
        self.fin_repo.create_objective_budget("obj_1", "GHS", 5000.0)
        self.fin_repo.allocate_to_mission("obj_1", "mis_1", 200.0)

        params = {
            "objective_id": "obj_1",
            "mission_id": "mis_1",
            "amount": 500.0,
            "currency": "GHS",
            "purpose": "Ads"
        }
        fingerprint = AuthorizationGate._build_action_fingerprint("FINANCIAL_COMMITMENT", params)
        mock_app = ApprovalRequest(
            approval_id="app_test123456",
            mission_id="mis_1",
            materialization_id="mat_1",
            action="Pay Ads",
            action_fingerprint=fingerprint,
            status=ApprovalStatus.APPROVED,
            approval_type=ApprovalType.MATERIALIZATION
        )
        approval_repository.list_by_mission = MagicMock(return_value=[mock_app])
        try:
            ctx = AgentContext(
                task="Pay", 
                granted_permissions={AgentPermission.FINANCIAL_COMMITMENT},
                planner_output={"mission_id": "mis_1", "materialization_id": "mat_1"}
            )
            res = await self.executor.execute_tool(MockFinancialTool(), {**params, "context": ctx})
            self.assertEqual(res.status, "FAILURE")
            self.assertIn("INSUFFICIENT_MISSION_BUDGET", res.error)
        finally:
            approval_repository.list_by_mission = self.original_list_approval

    async def test_currency_mismatch_rejected(self):
        self.fin_repo.create_objective_budget("obj_1", "GHS", 5000.0)
        self.fin_repo.allocate_to_mission("obj_1", "mis_1", 2000.0)

        params = {
            "objective_id": "obj_1",
            "mission_id": "mis_1",
            "amount": 500.0,
            "currency": "USD",
            "purpose": "Ads"
        }
        fingerprint = AuthorizationGate._build_action_fingerprint("FINANCIAL_COMMITMENT", params)
        mock_app = ApprovalRequest(
            approval_id="app_test123456",
            mission_id="mis_1",
            materialization_id="mat_1",
            action="Pay Ads",
            action_fingerprint=fingerprint,
            status=ApprovalStatus.APPROVED,
            approval_type=ApprovalType.MATERIALIZATION
        )
        approval_repository.list_by_mission = MagicMock(return_value=[mock_app])
        try:
            ctx = AgentContext(
                task="Pay", 
                granted_permissions={AgentPermission.FINANCIAL_COMMITMENT},
                planner_output={"mission_id": "mis_1", "materialization_id": "mat_1"}
            )
            res = await self.executor.execute_tool(MockFinancialTool(), {**params, "context": ctx})
            self.assertEqual(res.status, "FAILURE")
            self.assertIn("CURRENCY_MISMATCH", res.error)
        finally:
            approval_repository.list_by_mission = self.original_list_approval

if __name__ == "__main__":
    unittest.main()