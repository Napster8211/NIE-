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
    name = "mock_sec_financial_tool"
    description = "Secure test financial tool."
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
        return {"transaction_id": "tx_sec_999", "status": "CONFIRMED"}

class TestSprint4FinancialSecurityVerification(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.storage_path = os.path.join(self.temp_dir.name, "finance_4v.json")
        
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

    async def test_four_gate_matrix_deny_cases(self):
        # 1. NO authority, NO approval, NO budget -> DENY
        ctx_no = AgentContext(task="Pay", granted_permissions={AgentPermission.READ})
        res = await self.executor.execute_tool(MockFinancialTool(), {
            "objective_id": "obj_1", "mission_id": "mis_1", "amount": 100.0, "currency": "GHS", "purpose": "Test", "context": ctx_no
        })
        self.assertEqual(res.status, "FAILURE")

        # 2. YES authority, NO approval, YES budget -> DENY (Missing Approval)
        self.fin_repo.create_objective_budget("obj_1", "GHS", 1000.0)
        self.fin_repo.allocate_to_mission("obj_1", "mis_1", 500.0)
        approval_repository.list_by_mission = MagicMock(return_value=[])
        
        ctx_yes = AgentContext(task="Pay", granted_permissions={AgentPermission.FINANCIAL_COMMITMENT}, planner_output={"mission_id": "mis_1", "materialization_id": "mat_1"})
        res = await self.executor.execute_tool(MockFinancialTool(), {
            "objective_id": "obj_1", "mission_id": "mis_1", "amount": 100.0, "currency": "GHS", "purpose": "Test", "context": ctx_yes
        })
        self.assertEqual(res.status, "FAILURE")
        self.assertIn("APPROVAL_MISSING", res.error)

    async def test_authority_forgery_rejected(self):
        # Forging permission via parameters must fail closed
        ctx = AgentContext(task="Pay", granted_permissions={AgentPermission.READ})
        res = await self.executor.execute_tool(MockFinancialTool(), {
            "objective_id": "obj_1", "mission_id": "mis_1", "amount": 100.0, "currency": "GHS", "purpose": "Test", 
            "financial_authority": True, "context": ctx
        })
        self.assertEqual(res.status, "FAILURE")
        self.assertIn("AUTHORITY_MISSING", res.error)

    async def test_amount_substitution_rejected(self):
        self.fin_repo.create_objective_budget("obj_1", "GHS", 1000.0)
        self.fin_repo.allocate_to_mission("obj_1", "mis_1", 500.0)

        params_approved = {"objective_id": "obj_1", "mission_id": "mis_1", "amount": 100.0, "currency": "GHS", "purpose": "Test"}
        fingerprint = AuthorizationGate._build_action_fingerprint("FINANCIAL_COMMITMENT", params_approved)
        
        mock_app = ApprovalRequest(
            approval_id="app_testsecurity1", mission_id="mis_1", materialization_id="mat_1",
            action="Pay Test", action_fingerprint=fingerprint, status=ApprovalStatus.APPROVED, approval_type=ApprovalType.MATERIALIZATION
        )
        approval_repository.list_by_mission = MagicMock(return_value=[mock_app])

        ctx = AgentContext(task="Pay", granted_permissions={AgentPermission.FINANCIAL_COMMITMENT}, planner_output={"mission_id": "mis_1", "materialization_id": "mat_1"})
        
        # Attack: Substitute amount from 100.0 to 100.01
        params_attack = {**params_approved, "amount": 100.01}
        res = await self.executor.execute_tool(MockFinancialTool(), {**params_attack, "context": ctx})
        self.assertEqual(res.status, "FAILURE")
        self.assertIn("FINGERPRINT_MISMATCH", res.error)

    async def test_currency_substitution_rejected(self):
        self.fin_repo.create_objective_budget("obj_1", "GHS", 1000.0)
        self.fin_repo.allocate_to_mission("obj_1", "mis_1", 500.0)

        params_approved = {"objective_id": "obj_1", "mission_id": "mis_1", "amount": 100.0, "currency": "GHS", "purpose": "Test"}
        fingerprint = AuthorizationGate._build_action_fingerprint("FINANCIAL_COMMITMENT", params_approved)
        
        mock_app = ApprovalRequest(
            approval_id="app_testsecurity2", mission_id="mis_1", materialization_id="mat_1",
            action="Pay Test", action_fingerprint=fingerprint, status=ApprovalStatus.APPROVED, approval_type=ApprovalType.MATERIALIZATION
        )
        approval_repository.list_by_mission = MagicMock(return_value=[mock_app])

        ctx = AgentContext(task="Pay", granted_permissions={AgentPermission.FINANCIAL_COMMITMENT}, planner_output={"mission_id": "mis_1", "materialization_id": "mat_1"})
        
        # Attack: Substitute currency to USD against GHS allocation
        params_attack = {**params_approved, "currency": "USD"}
        res = await self.executor.execute_tool(MockFinancialTool(), {**params_attack, "context": ctx})
        self.assertEqual(res.status, "FAILURE")
        self.assertTrue("CURRENCY_MISMATCH" in res.error or "FINGERPRINT_MISMATCH" in res.error)

    async def test_cross_mission_isolation(self):
        self.fin_repo.create_objective_budget("obj_1", "GHS", 2000.0)
        self.fin_repo.allocate_to_mission("obj_1", "mis_a", 100.0)
        self.fin_repo.allocate_to_mission("obj_1", "mis_b", 1500.0)

        params = {"objective_id": "obj_1", "mission_id": "mis_a", "amount": 500.0, "currency": "GHS", "purpose": "Test"}
        fingerprint = AuthorizationGate._build_action_fingerprint("FINANCIAL_COMMITMENT", params)
        
        mock_app = ApprovalRequest(
            approval_id="app_testsecurity3", mission_id="mis_a", materialization_id="mat_1",
            action="Pay Test", action_fingerprint=fingerprint, status=ApprovalStatus.APPROVED, approval_type=ApprovalType.MATERIALIZATION
        )
        approval_repository.list_by_mission = MagicMock(return_value=[mock_app])

        ctx = AgentContext(task="Pay", granted_permissions={AgentPermission.FINANCIAL_COMMITMENT}, planner_output={"mission_id": "mis_a", "materialization_id": "mat_1"})
        
        # Mission A trying to spend 500 while allocated only 100 (cross-mission borrow denied)
        res = await self.executor.execute_tool(MockFinancialTool(), {**params, "context": ctx})
        self.assertEqual(res.status, "FAILURE")
        self.assertIn("INSUFFICIENT_MISSION_BUDGET", res.error)

if __name__ == "__main__":
    unittest.main()