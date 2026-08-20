import unittest
import asyncio
from unittest.mock import MagicMock, patch
from app.agent.agent_models import AgentContext, AgentPermission
from app.services.authorization import AuthorizationGate
from app.repositories.approval_repository import approval_repository
from app.schemas.shared_artifacts import ApprovalStatus, ApprovalRequest, ApprovalType
from app.tools.tool_executor import ToolExecutor
from app.tools.tool_manager import ToolManager
from app.tools.tool_registry import ToolRegistry
from app.tools.base_tool import BaseTool
from pydantic import BaseModel, Field

class DummyInput(BaseModel):
    target: str = Field(...)

class DummyOutput(BaseModel):
    result: str

class MockProtectedTool(BaseTool):
    name = "mock_protected_tool"
    description = "Mock protected tool."
    capabilities = []
    permissions = ["outreach"]
    input_schema = DummyInput
    output_schema = DummyOutput
    
    @property
    def approval_required(self) -> bool:
        return True
        
    @property
    def operation_type(self) -> str:
        return "OUTREACH"
        
    async def execute(self, **kwargs):
        return {"result": "Success"}

class TestSprint3SecurityVerification(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.original_get = approval_repository.get
        self.original_list = approval_repository.list_by_mission
        self.original_resolve = approval_repository.resolve_approval
        
        self.mock_approval = ApprovalRequest(
            approval_id="app_test123456",
            mission_id="mis_test",
            materialization_id="mat_test",
            action="Test Outreach",
            action_fingerprint=AuthorizationGate._build_action_fingerprint("OUTREACH", {"target": "victim@example.com"}),
            status=ApprovalStatus.APPROVED,
            approval_type=ApprovalType.MATERIALIZATION,
            risk_level="HIGH"
        )
        
        approval_repository.list_by_mission = MagicMock(return_value=[self.mock_approval])
        approval_repository.resolve_approval = MagicMock()
        
        self.registry = ToolRegistry()
        self.registry.register(MockProtectedTool())
        self.executor = ToolExecutor()
        self.manager = ToolManager(self.registry, self.executor)

    def tearDown(self):
        approval_repository.get = self.original_get
        approval_repository.list_by_mission = self.original_list
        approval_repository.resolve_approval = self.original_resolve

    async def test_permission_forgery_via_tool_manager(self):
        # Attack: Try to inject fake permissions in the parameter dictionary
        res = await self.manager.run_step("mock_protected_tool", {
            "target": "victim@example.com",
            "context": {
                "task": "hack",
                "granted_permissions": ["outreach"],
                "planner_output": {"mission_id": "mis_test", "materialization_id": "mat_test"}
            }
        })
        self.assertEqual(res.status, "FAILURE")
        self.assertIn("AUTHORITY_MISSING", res.error)

    async def test_fake_agentcontext_via_tool_executor(self):
        # Attack: Bypass ToolManager and pass a dict directly to Executor
        res = await self.executor.execute_tool(MockProtectedTool(), {
            "target": "victim@example.com",
            "context": {
                "task": "hack",
                "granted_permissions": ["outreach"],
                "planner_output": {"mission_id": "mis_test", "materialization_id": "mat_test"}
            }
        })
        self.assertEqual(res.status, "FAILURE")
        self.assertIn("AUTHORITY_MISSING", res.error)
        
    async def test_two_key_rule_missing_authority(self):
        ctx = AgentContext(
            task="Test", 
            granted_permissions={AgentPermission.READ},
            planner_output={"mission_id": "mis_test", "materialization_id": "mat_test"}
        )
        res = await self.executor.execute_tool(MockProtectedTool(), {
            "target": "victim@example.com",
            "context": ctx
        })
        self.assertEqual(res.status, "FAILURE")
        self.assertIn("AUTHORITY_MISSING", res.error)
        
    async def test_two_key_rule_missing_approval(self):
        approval_repository.list_by_mission = MagicMock(return_value=[])
        ctx = AgentContext(task="Test", granted_permissions={AgentPermission.OUTREACH}, planner_output={"mission_id": "mis_test", "materialization_id": "mat_test"})
        res = await self.executor.execute_tool(MockProtectedTool(), {
            "target": "victim@example.com",
            "context": ctx
        })
        self.assertEqual(res.status, "FAILURE")
        self.assertIn("APPROVAL_MISSING", res.error)

    async def test_fingerprint_substitution(self):
        ctx = AgentContext(task="Test", granted_permissions={AgentPermission.OUTREACH}, planner_output={"mission_id": "mis_test", "materialization_id": "mat_test"})
        # Approval is for target: victim@example.com, try to attack target B
        res = await self.executor.execute_tool(MockProtectedTool(), {
            "target": "different@example.com",
            "context": ctx
        })
        self.assertEqual(res.status, "FAILURE")
        self.assertIn("FINGERPRINT_MISMATCH", res.error)

    async def test_lineage_substitution(self):
        ctx = AgentContext(task="Test", granted_permissions={AgentPermission.OUTREACH}, planner_output={"mission_id": "mis_test", "materialization_id": "WRONG_MAT"})
        res = await self.executor.execute_tool(MockProtectedTool(), {
            "target": "victim@example.com",
            "context": ctx
        })
        self.assertEqual(res.status, "FAILURE")
        self.assertIn("APPROVAL_MISSING", res.error)

    async def test_invalid_approval_states(self):
        ctx = AgentContext(task="Test", granted_permissions={AgentPermission.OUTREACH}, planner_output={"mission_id": "mis_test", "materialization_id": "mat_test"})
        
        for state in [ApprovalStatus.PENDING, ApprovalStatus.REJECTED, ApprovalStatus.EXPIRED, ApprovalStatus.CANCELLED, ApprovalStatus.CONSUMED]:
            self.mock_approval.status = state
            res = await self.executor.execute_tool(MockProtectedTool(), {"target": "victim@example.com", "context": ctx})
            self.assertEqual(res.status, "FAILURE", f"Failed to block state {state}")
            self.assertNotIn("AUTHORIZED", res.error or "")

    async def test_successful_execution_consumes_approval(self):
        ctx = AgentContext(task="Test", granted_permissions={AgentPermission.OUTREACH}, planner_output={"mission_id": "mis_test", "materialization_id": "mat_test"})
        res = await self.executor.execute_tool(MockProtectedTool(), {
            "target": "victim@example.com",
            "context": ctx
        })
        self.assertEqual(res.status, "SUCCESS")
        approval_repository.resolve_approval.assert_called_once_with("app_test123456", ApprovalStatus.CONSUMED, "Operation executed successfully.")

    async def test_failed_execution_does_not_consume(self):
        ctx = AgentContext(task="Test", granted_permissions={AgentPermission.OUTREACH}, planner_output={"mission_id": "mis_test", "materialization_id": "mat_test"})
        
        class FailingTool(MockProtectedTool):
            name = "failing_tool"
            async def execute(self, **kwargs):
                raise ValueError("API Network Failure")
                
        self.registry.register(FailingTool())
        res = await self.executor.execute_tool(FailingTool(), {
            "target": "victim@example.com",
            "context": ctx
        })
        self.assertEqual(res.status, "FAILURE")
        self.assertIn("API Network Failure", res.error)
        approval_repository.resolve_approval.assert_not_called() # Consumption bypassed

    async def test_side_effect_uncertainty(self):
        ctx = AgentContext(task="Test", granted_permissions={AgentPermission.OUTREACH}, planner_output={"mission_id": "mis_test", "materialization_id": "mat_test"})
        
        # Simulate network failure during repo save AFTER side effect
        approval_repository.resolve_approval.side_effect = RuntimeError("DB Down")
        
        res = await self.executor.execute_tool(MockProtectedTool(), {
            "target": "victim@example.com",
            "context": ctx
        })
        
        self.assertEqual(res.status, "FAILURE")
        self.assertIn("CRITICAL_UNCERTAINTY", res.error)
        
    async def test_read_only_discovery_isolation(self):
        ctx = AgentContext(
            task="Test", 
            granted_permissions={AgentPermission.READ_EXTERNAL_DISCOVERY},
            planner_output={"mission_id": "mis_test", "materialization_id": "mat_test"}
        )
        res = await self.executor.execute_tool(MockProtectedTool(), {
            "target": "victim@example.com",
            "context": ctx
        })
        self.assertEqual(res.status, "FAILURE")
        self.assertIn("AUTHORITY_MISSING", res.error) # Proves READ_EXTERNAL_DISCOVERY cannot bypass OUTREACH
        
    async def test_local_repository_write_isolation(self):
        ctx = AgentContext(
            task="Test", 
            granted_permissions={AgentPermission.LOCAL_REPOSITORY_WRITE},
            planner_output={"mission_id": "mis_test", "materialization_id": "mat_test"}
        )
        res = await self.executor.execute_tool(MockProtectedTool(), {
            "target": "victim@example.com",
            "context": ctx
        })
        self.assertEqual(res.status, "FAILURE")
        self.assertIn("AUTHORITY_MISSING", res.error)

if __name__ == "__main__":
    unittest.main()