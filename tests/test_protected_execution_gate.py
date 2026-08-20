import unittest
import asyncio
from unittest.mock import MagicMock
from app.agent.agent_models import AgentContext, AgentPermission
from app.services.authorization import AuthorizationGate
from app.repositories.approval_repository import approval_repository
from app.schemas.shared_artifacts import ApprovalStatus, ApprovalRequest, ApprovalType

class TestAuthorizationGate(unittest.TestCase):
    def setUp(self):
        # Isolate the repository
        self.original_get = approval_repository.get
        self.original_list = approval_repository.list_by_mission
        self.original_resolve = approval_repository.resolve_approval
        
        # FIX: The approval_id must match ^app_[a-z0-9]{8,64}$
        self.mock_approval = ApprovalRequest(
            approval_id="app_test123456",
            mission_id="mis_test",
            materialization_id="mat_test",
            action="Test Outreach",
            status=ApprovalStatus.APPROVED,
            approval_type=ApprovalType.MATERIALIZATION,
            risk_level="HIGH"
        )
        
        approval_repository.list_by_mission = MagicMock(return_value=[self.mock_approval])
        approval_repository.resolve_approval = MagicMock()

    def tearDown(self):
        approval_repository.get = self.original_get
        approval_repository.list_by_mission = self.original_list
        approval_repository.resolve_approval = self.original_resolve

    def test_missing_operational_authority_denied(self):
        ctx = AgentContext(task="Test", granted_permissions={AgentPermission.READ})
        
        res = AuthorizationGate.evaluate_execution(
            agent_context=ctx,
            tool_name="test_tool",
            required_permissions=[AgentPermission.OUTREACH],
            approval_required=True,
            operation_type="OUTREACH",
            parameters={}
        )
        
        self.assertEqual(res.status, "AUTHORITY_MISSING")

    def test_missing_approval_denied(self):
        ctx = AgentContext(task="Test", granted_permissions={AgentPermission.OUTREACH})
        
        res = AuthorizationGate.evaluate_execution(
            agent_context=ctx,
            tool_name="test_tool",
            required_permissions=[AgentPermission.OUTREACH],
            approval_required=True,
            operation_type="OUTREACH",
            parameters={}
        )
        
        self.assertEqual(res.status, "LINEAGE_MISMATCH")

    def test_valid_authority_and_approval_succeeds(self):
        ctx = AgentContext(
            task="Test", 
            granted_permissions={AgentPermission.OUTREACH},
            planner_output={"mission_id": "mis_test", "materialization_id": "mat_test"}
        )
        
        res = AuthorizationGate.evaluate_execution(
            agent_context=ctx,
            tool_name="test_tool",
            required_permissions=[AgentPermission.OUTREACH],
            approval_required=True,
            operation_type="OUTREACH",
            parameters={}
        )
        
        self.assertEqual(res.status, "AUTHORIZED")

    def test_revoked_approval_denied(self):
        self.mock_approval.status = ApprovalStatus.CANCELLED
        ctx = AgentContext(
            task="Test", 
            granted_permissions={AgentPermission.OUTREACH},
            planner_output={"mission_id": "mis_test", "materialization_id": "mat_test"}
        )
        
        res = AuthorizationGate.evaluate_execution(
            agent_context=ctx,
            tool_name="test_tool",
            required_permissions=[AgentPermission.OUTREACH],
            approval_required=True,
            operation_type="OUTREACH",
            parameters={}
        )
        
        self.assertEqual(res.status, "APPROVAL_REVOKED")

    def test_consumed_approval_denied_replay(self):
        self.mock_approval.status = ApprovalStatus.CONSUMED
        ctx = AgentContext(
            task="Test", 
            granted_permissions={AgentPermission.OUTREACH},
            planner_output={"mission_id": "mis_test", "materialization_id": "mat_test"}
        )
        
        res = AuthorizationGate.evaluate_execution(
            agent_context=ctx,
            tool_name="test_tool",
            required_permissions=[AgentPermission.OUTREACH],
            approval_required=True,
            operation_type="OUTREACH",
            parameters={}
        )
        
        self.assertEqual(res.status, "APPROVAL_CONSUMED")
        
    def test_fingerprint_mismatch_denied(self):
        self.mock_approval.action_fingerprint = "different_hash"
        ctx = AgentContext(
            task="Test", 
            granted_permissions={AgentPermission.OUTREACH},
            planner_output={"mission_id": "mis_test", "materialization_id": "mat_test"}
        )
        
        res = AuthorizationGate.evaluate_execution(
            agent_context=ctx,
            tool_name="test_tool",
            required_permissions=[AgentPermission.OUTREACH],
            approval_required=True,
            operation_type="OUTREACH",
            parameters={"target": "victim@example.com"} # Will generate a specific hash
        )
        
        self.assertEqual(res.status, "FINGERPRINT_MISMATCH")

if __name__ == "__main__":
    unittest.main()