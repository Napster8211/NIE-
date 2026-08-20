import os
import tempfile
import unittest
import asyncio
from pathlib import Path
from unittest.mock import patch

from app.schemas.shared_artifacts import (
    ApprovalRequest, ApprovalStatus, ApprovalType, DirectorAgentContext
)
from app.repositories.approval_repository import approval_repository, ApprovalInvariantError, ApprovalPersistenceError
from app.engine.mission_engine import mission_registry, MissionEngine
from app.services.director_command_resolver import resolve_director_command, DirectorCommandClass
from app.services.approval_service import ApprovalDecisionService
from app.tools.plugins.director_tools import DirectorContextBuilderTool, DirectorEvaluatorTool

class TestHumanApprovalControlPlane(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        
        # Isolate Registries
        self.app_path = Path(self.temp_dir.name) / "approvals.json"
        self.mis_path = Path(self.temp_dir.name) / "missions.json"
        
        with approval_repository.locked(reload=False):
            approval_repository.storage_path = str(self.app_path)
            approval_repository._approvals = {}
            
        with mission_registry.locked(reload=False):
            mission_registry.mission_file = str(self.mis_path)
            mission_registry.missions = {}

        self.service = ApprovalDecisionService()

    def tearDown(self):
        self.temp_dir.cleanup()

    def _setup_blocked_mission(self):
        """Creates a mission that is paused waiting for communication approval."""
        engine = MissionEngine()
        mission = mission_registry.create_mission("Acquire 1 client")
        
        with mission_registry.locked():
            m = mission_registry.missions[mission.mission_id]
            m.progression_decisions.append({
                "decision_id": "dec_1",
                "milestone_id": "m1",
                "plan_version": "v1",
                "selected_action": "Send automated email outreach",
                "target_intelligence": "communication_intelligence",
                "expected_artifact": "CommunicationArtifact"
            })
            mission_registry.save_mission(m)
            
        # Materializing a communication action automatically triggers APPROVAL_REQUIRED
        from app.engine.mission_engine import MissionProgressionMaterializer
        MissionProgressionMaterializer().materialize(m, m.progression_decisions[-1])
        
        # Save to disk so that get_mission retrieves the updated state containing the materialization
        with mission_registry.locked():
            mission_registry.save_mission(m)
        
        m = mission_registry.get_mission(mission.mission_id)
        mat = m.progression_materializations[0]
        approval_id = mat.get("approval_id")
        return m, mat, approval_id

    def _build_director_context(self, command_class: str, approval_id: str, reason: str = "Test Reason", is_mutation: bool = True) -> DirectorAgentContext:
        """Helper to construct a valid DirectorAgentContext matching production semantics."""
        return DirectorAgentContext(
            company_id="napstertec", 
            query=f"{command_class.split('_')[1]} approval {approval_id}" if is_mutation else f"Inspect approval {approval_id}",
            operating_mode="HUMAN DECISION CONTROL MODE", 
            command_class=command_class,
            authority_mode="APPROVAL_MUTATION" if is_mutation else "READ_ONLY",
            authority_scope="HUMAN_APPROVAL_STATE" if is_mutation else "NONE", 
            mutation_allowed=is_mutation,
            execution_context="HUMAN_APPROVAL",
            coo_artifact_status="FRESH",
            cfo_artifact_status="FRESH",
            cro_artifact_status="FRESH",
            governance_status="Active",
            aggregated_metrics={"approval_id": approval_id, "approval_reason": reason}
        )

    # --- 1. ROUTING & COMMAND CLASSIFICATION TESTS ---

    def test_routing_approval_commands(self):
        cases = [
            ("Director, list pending approvals.", DirectorCommandClass.APPROVAL_INSPECT, "READ_ONLY", False),
            ("Show approval app_12345678", DirectorCommandClass.APPROVAL_INSPECT, "READ_ONLY", False),
            ("Approve approval app_12345678", DirectorCommandClass.APPROVAL_APPROVE, "APPROVAL_MUTATION", True),
            ("Reject approval app_12345678 because risk is too high", DirectorCommandClass.APPROVAL_REJECT, "APPROVAL_MUTATION", True),
            ("Revoke approval app_12345678 because scope changed", DirectorCommandClass.APPROVAL_REVOKE, "APPROVAL_MUTATION", True),
        ]
        
        for query, expected_class, expected_auth, is_mutation in cases:
            with self.subTest(query=query):
                route = resolve_director_command(query)
                self.assertEqual(route["command_class"], expected_class.value)
                self.assertEqual(route["authority_mode"], expected_auth)
                self.assertEqual(route["mutation_allowed"], is_mutation)
                self.assertEqual(route["authority_scope"], "HUMAN_APPROVAL_STATE" if is_mutation else "NONE")
                # Ensure it didn't widen into mission authority
                self.assertFalse(route["internal_mission_mutation_allowed"])
                self.assertFalse(route["external_side_effect_allowed"])

    async def test_director_tools_preserve_approval_routing(self):
        query = "Approve approval app_12345678"
        route = resolve_director_command(query)
        route["granted_permissions"] = ["read", "write"]
        
        built = await DirectorContextBuilderTool().execute(query, authority_context=route)
        ctx = built["isolated_context"]
        
        self.assertEqual(ctx["command_class"], "APPROVAL_APPROVE")
        self.assertEqual(ctx["authority_scope"], "HUMAN_APPROVAL_STATE")
        self.assertTrue(ctx["mutation_allowed"])
        self.assertEqual(ctx["aggregated_metrics"]["approval_id"], "app_12345678")

    # --- 2. APPROVAL CREATION & LINEAGE TESTS ---

    def test_materialization_creates_approval_request(self):
        mission, mat, approval_id = self._setup_blocked_mission()
        
        self.assertIsNotNone(approval_id)
        self.assertEqual(mat["status"], "APPROVAL_REQUIRED")
        self.assertEqual(mission.progression_state, "WAITING")
        self.assertEqual(mission.auto_continue_status, "WAITING_APPROVAL")
        
        # Verify execution request is NOT created yet
        self.assertEqual(len(mission.execution_requests), 0)
        
        # Verify approval is persisted
        approval = approval_repository.get(approval_id)
        self.assertIsNotNone(approval)
        self.assertEqual(approval.mission_id, mission.mission_id)
        self.assertEqual(approval.materialization_id, mat["materialization_id"])
        self.assertEqual(approval.status, ApprovalStatus.PENDING)

    # --- 3. AI SELF-APPROVAL PREVENTION TESTS ---

    async def test_ai_agent_cannot_self_approve(self):
        mission, mat, approval_id = self._setup_blocked_mission()
        ctx = self._build_director_context("APPROVAL_APPROVE", approval_id, "Ok")
        
        # Fails if session_id is a system/agent marker
        for invalid_session in ["agent_session", "system_director", "system"]:
            with self.subTest(session=invalid_session):
                with self.assertRaisesRegex(PermissionError, "AI self-approval is forbidden"):
                    await self.service.execute_decision(ctx, session_id=invalid_session)

    # --- 4. APPROVE WORKFLOW TESTS ---

    async def test_trusted_human_approve_releases_work(self):
        mission, mat, approval_id = self._setup_blocked_mission()
        ctx = self._build_director_context("APPROVAL_APPROVE", approval_id, "Looks good")
        
        artifact = await self.service.execute_decision(ctx, session_id="user_cto_123")
        
        self.assertIn("APPROVED", artifact.executive_summary)
        
        # Verify approval repository state
        approval = approval_repository.get(approval_id)
        self.assertEqual(approval.status, ApprovalStatus.APPROVED)
        
        # Verify exact work was released
        m = mission_registry.get_mission(mission.mission_id)
        updated_mat = m.progression_materializations[0]
        self.assertEqual(updated_mat["status"], "EXECUTION_READY")
        self.assertEqual(m.progression_state, "READY")
        
        # Verify an execution request was cleanly generated
        self.assertEqual(len(m.execution_requests), 1)
        req = m.execution_requests[0]
        self.assertEqual(req["status"], "READY")
        self.assertEqual(req["materialization_id"], updated_mat["materialization_id"])

    # --- 5. REJECT & REVOKE WORKFLOW TESTS ---

    async def test_trusted_human_reject_blocks_work(self):
        mission, mat, approval_id = self._setup_blocked_mission()
        ctx = self._build_director_context("APPROVAL_REJECT", approval_id, "Not ready")
        
        artifact = await self.service.execute_decision(ctx, session_id="user_cto_123")
        
        # Verify state
        approval = approval_repository.get(approval_id)
        self.assertEqual(approval.status, ApprovalStatus.REJECTED)
        
        m = mission_registry.get_mission(mission.mission_id)
        self.assertEqual(m.progression_materializations[0]["status"], "REJECTED")
        self.assertEqual(m.status, "WAITING_DIRECTOR") # Escalated
        self.assertIn("APPROVAL_REJECTED", m.last_error)

    async def test_trusted_human_revoke_blocks_work(self):
        mission, mat, approval_id = self._setup_blocked_mission()
        
        # First approve it
        approval_repository.resolve_approval(approval_id, ApprovalStatus.APPROVED, "Ok")
        await MissionEngine().approve_materialization(mission.mission_id, mat["materialization_id"])
        
        # Now revoke it
        ctx = self._build_director_context("APPROVAL_REVOKE", approval_id, "Hold on")
        await self.service.execute_decision(ctx, session_id="user_cto_123")
        
        approval = approval_repository.get(approval_id)
        self.assertEqual(approval.status, ApprovalStatus.CANCELLED)
        
        m = mission_registry.get_mission(mission.mission_id)
        self.assertEqual(m.progression_materializations[0]["status"], "REVOKED")
        self.assertEqual(m.status, "WAITING_DIRECTOR")
        self.assertEqual(m.execution_requests[0]["status"], "BLOCKED")

    # --- 6. IDEMPOTENCY & ISOLATION TESTS ---

    async def test_idempotent_double_approve_fails_closed(self):
        mission, mat, approval_id = self._setup_blocked_mission()
        ctx = self._build_director_context("APPROVAL_APPROVE", approval_id, "Ok")
        
        await self.service.execute_decision(ctx, session_id="user_123")
        
        with self.assertRaisesRegex(ValueError, "APPROVAL_ALREADY_RESOLVED"):
            await self.service.execute_decision(ctx, session_id="user_123")

    async def test_approval_does_not_grant_operational_authority(self):
        query = "Approve approval app_12345678"
        route = resolve_director_command(query)
        route["granted_permissions"] = ["read", "write"]
        
        built = await DirectorContextBuilderTool().execute(query, authority_context=route)
        ctx = built["isolated_context"]
        
        self.assertEqual(ctx["command_class"], "APPROVAL_APPROVE")
        # Authority remains specifically restricted
        self.assertEqual(ctx["authority_scope"], "HUMAN_APPROVAL_STATE")
        self.assertFalse(ctx["external_side_effect_allowed"])
        self.assertFalse(ctx["mission_execution_allowed"])
        
    async def test_director_evaluator_routes_to_approval_service(self):
        query = "Approve approval app_12345678"
        route = resolve_director_command(query)
        route["granted_permissions"] = ["read", "write"]
        built = await DirectorContextBuilderTool().execute(query, authority_context=route)
        
        with patch.object(ApprovalDecisionService, 'execute_decision', return_value=DirectorAgentContext) as mock_svc:
            # We mock the return just to ensure the routing logic in the Tool triggers the service.
            try:
                await DirectorEvaluatorTool().execute(context=built["isolated_context"])
            except AttributeError:
                pass # Expected because mock_svc returns a class not an artifact
            mock_svc.assert_called_once()

    async def test_persistence_failure_prevents_work_release(self):
        mission, mat, approval_id = self._setup_blocked_mission()
        ctx = self._build_director_context("APPROVAL_APPROVE", approval_id, "Ok")
        
        with patch("app.repositories.approval_repository.os.replace", side_effect=PermissionError("Access denied")):
            with self.assertRaises(ValueError):
                await self.service.execute_decision(ctx, session_id="user_123")
                
        # Mission state MUST remain blocked
        m = mission_registry.get_mission(mission.mission_id)
        self.assertEqual(m.progression_materializations[0]["status"], "APPROVAL_REQUIRED")
        self.assertEqual(len(m.execution_requests), 0)

if __name__ == "__main__":
    unittest.main()