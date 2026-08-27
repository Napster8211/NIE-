import unittest
import os
import tempfile
from fastapi.testclient import TestClient

from app.main import app
from app.repositories.approval_repository import approval_repository
from app.repositories.company_objective_repository import company_objective_repository
from app.engine.mission_engine import mission_registry
from app.schemas.shared_artifacts import ApprovalRequest, ApprovalStatus, ApprovalType
from app.schemas.company_objective import CompanyObjective, CompanyObjectiveStatus, CompanyObjectiveSuccessCriteria
from app.services.authorization import NIE_OWNER_KEY

class TestDirectorOwnerControls(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        
        # Sandbox Repos
        self.temp_dir = tempfile.TemporaryDirectory()
        approval_repository.storage_path = os.path.join(self.temp_dir.name, "app.json")
        approval_repository._approvals = {}
        
        company_objective_repository.storage_path = os.path.join(self.temp_dir.name, "obj.json")
        company_objective_repository._objectives = {}

        mission_registry.mission_file = os.path.join(self.temp_dir.name, "mis.json")
        mission_registry.missions = {}

        # Set Authenticated Headers
        self.headers = {"Authorization": f"Bearer {NIE_OWNER_KEY}"}

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_owner_can_approve_valid_request(self):
        req = ApprovalRequest(
            approval_id="app_12345678", mission_id="mis_12345678", decision_id="dec_12345678", materialization_id="mat_12345678",
            action="Deploy", status=ApprovalStatus.PENDING, approval_type=ApprovalType.MATERIALIZATION,
            risk_level="HIGH", requester="director"
        )
        approval_repository.create(req)
        
        res = self.client.post("/api/v1/director/approvals/app_12345678/resolve", json={
            "decision": "APPROVE", "reason": "Looks good"
        }, headers=self.headers)
        
        self.assertEqual(res.status_code, 200)
        self.assertEqual(approval_repository.get("app_12345678").status, ApprovalStatus.APPROVED)

    def test_non_owner_cannot_approve(self):
        res = self.client.post("/api/v1/director/approvals/app_12345678/resolve", json={
            "decision": "APPROVE", "reason": "Hacked"
        }, headers={"Authorization": "Bearer BAD_KEY"})
        
        self.assertEqual(res.status_code, 403)

    def test_forged_authority_fields_are_ignored(self):
        req = ApprovalRequest(
            approval_id="app_forge123", mission_id="mis_12345678", decision_id="dec_12345678", materialization_id="mat_12345678",
            action="Deploy", status=ApprovalStatus.PENDING, approval_type=ApprovalType.MATERIALIZATION,
            risk_level="HIGH", requester="director"
        )
        approval_repository.create(req)
        
        # Pydantic schema validation ignores "granted_permissions" and "is_owner" securely
        res = self.client.post("/api/v1/director/approvals/app_forge123/resolve", json={
            "decision": "APPROVE", "reason": "Test", "granted_permissions": ["DEPLOY"], "is_owner": True
        }, headers=self.headers)
        
        self.assertEqual(res.status_code, 200)

    def test_already_resolved_approval_cannot_replay(self):
        req = ApprovalRequest(
            approval_id="app_replay12", mission_id="mis_12345678", decision_id="dec_12345678", materialization_id="mat_12345678",
            action="Deploy", status=ApprovalStatus.PENDING, approval_type=ApprovalType.MATERIALIZATION,
            risk_level="HIGH", requester="director"
        )
        approval_repository.create(req)
        approval_repository.resolve_approval("app_replay12", ApprovalStatus.REJECTED, "No")
        
        res = self.client.post("/api/v1/director/approvals/app_replay12/resolve", json={
            "decision": "APPROVE", "reason": "Changed mind"
        }, headers=self.headers)
        
        self.assertEqual(res.status_code, 400)
        self.assertEqual(approval_repository.get("app_replay12").status, ApprovalStatus.REJECTED)

    def test_stale_approval_version_rejected(self):
        req = ApprovalRequest(
            approval_id="app_stale123", mission_id="mis_12345678", decision_id="dec_12345678", materialization_id="mat_12345678",
            action="Deploy", status=ApprovalStatus.PENDING, approval_type=ApprovalType.MATERIALIZATION,
            risk_level="HIGH", requester="director", version=2
        )
        approval_repository._approvals["app_stale123"] = req
        approval_repository._persist(approval_repository._approvals)
        
        res = self.client.post("/api/v1/director/approvals/app_stale123/resolve", json={
            "decision": "APPROVE", "reason": "Test", "expected_version": 1
        }, headers=self.headers)
        
        self.assertEqual(res.status_code, 409)

    def test_owner_pause_and_resume_objective(self):
        obj = CompanyObjective(
            objective_id="obj_12345678", title="Test", objective="Test",
            status=CompanyObjectiveStatus.ACTIVE,
            success_criteria=CompanyObjectiveSuccessCriteria(criterion="revenue", required=10),
            max_missions=5, max_strategy_changes=2, max_zero_progress_cycles=2
        )
        company_objective_repository.create(obj)
        
        res = self.client.post("/api/v1/director/objectives/obj_12345678/pause", json={"reason": "Hold"}, headers=self.headers)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(company_objective_repository.get("obj_12345678").status, CompanyObjectiveStatus.PAUSED)
        
        res = self.client.post("/api/v1/director/objectives/obj_12345678/resume", json={"reason": "Go"}, headers=self.headers)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(company_objective_repository.get("obj_12345678").status, CompanyObjectiveStatus.ACTIVE)

    def test_completed_objective_cannot_resume(self):
        obj = CompanyObjective(
            objective_id="obj_comp1234", title="Test", objective="Test",
            status=CompanyObjectiveStatus.COMPLETED,
            verified_success_count=10,
            progress=100.0,
            success_criteria=CompanyObjectiveSuccessCriteria(criterion="revenue", required=10),
            max_missions=5, max_strategy_changes=2, max_zero_progress_cycles=2
        )
        company_objective_repository.create(obj)
        
        res = self.client.post("/api/v1/director/objectives/obj_comp1234/resume", json={"reason": "Go"}, headers=self.headers)
        self.assertEqual(res.status_code, 400)
        
    def test_cancel_objective_preserves_history(self):
        obj = CompanyObjective(
            objective_id="obj_cancel12", title="Test", objective="Test",
            status=CompanyObjectiveStatus.ACTIVE,
            success_criteria=CompanyObjectiveSuccessCriteria(criterion="revenue", required=10),
            max_missions=5, max_strategy_changes=2, max_zero_progress_cycles=2
        )
        company_objective_repository.create(obj)
        
        res = self.client.post("/api/v1/director/objectives/obj_cancel12/cancel", json={"reason": "Budget cut"}, headers=self.headers)
        self.assertEqual(res.status_code, 200)
        self.assertEqual(company_objective_repository.get("obj_cancel12").status, CompanyObjectiveStatus.CANCELLED)
        self.assertIsNotNone(company_objective_repository.get("obj_cancel12"))

if __name__ == "__main__":
    unittest.main()