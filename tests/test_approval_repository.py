import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.schemas.shared_artifacts import (
    ApprovalRequest,
    ApprovalStatus,
    ApprovalType,
)
from app.repositories.approval_repository import (
    ApprovalRepository,
    ApprovalInvariantError,
    ApprovalPersistenceError,
)


class TestApprovalRepository(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name) / "approvals.json"
        self.repo = ApprovalRepository(str(self.path))

    def tearDown(self):
        self.temp_dir.cleanup()

    def _make_approval(
        self, 
        app_id="app_12345678", 
        mission_id="mis_0001", 
        status=ApprovalStatus.PENDING
    ) -> ApprovalRequest:
        """Helper to create a valid ApprovalRequest matching the regex pattern."""
        return ApprovalRequest(
            approval_id=app_id,
            mission_id=mission_id,
            action="Deploy application to production",
            status=status,
            approval_type=ApprovalType.MATERIALIZATION,
            risk_level="HIGH"
        )

    def test_create_approval_success(self):
        approval = self._make_approval()
        created = self.repo.create(approval)
        
        self.assertEqual(created.approval_id, "app_12345678")
        self.assertEqual(created.status, ApprovalStatus.PENDING)
        self.assertEqual(created.version, 1)
        self.assertIsNone(created.resolved_at)
        
        # Verify persistence
        reloaded = ApprovalRepository(str(self.path))
        self.assertIsNotNone(reloaded.get("app_12345678"))

    def test_create_duplicate_approval_rejected(self):
        approval = self._make_approval()
        self.repo.create(approval)
        
        with self.assertRaisesRegex(ApprovalInvariantError, "APPROVAL_ALREADY_EXISTS"):
            self.repo.create(approval)

    def test_create_non_pending_approval_rejected(self):
        # We must bypass Pydantic validation momentarily to test the repo layer constraint
        # because the Pydantic model itself will complain about resolved_at.
        approval = self._make_approval(status=ApprovalStatus.APPROVED)
        approval.resolved_at = "2026-08-19T00:00:00+00:00"
        
        with self.assertRaisesRegex(ApprovalInvariantError, "NEW_APPROVAL_MUST_BE_PENDING"):
            self.repo.create(approval)

    def test_resolve_approval_success(self):
        approval = self.repo.create(self._make_approval())
        
        resolved = self.repo.resolve_approval(
            approval_id=approval.approval_id,
            status=ApprovalStatus.APPROVED,
            reason="CTO reviewed and approved the architecture."
        )
        
        self.assertEqual(resolved.status, ApprovalStatus.APPROVED)
        self.assertEqual(resolved.resolution_reason, "CTO reviewed and approved the architecture.")
        self.assertEqual(resolved.version, 2)
        self.assertIsNotNone(resolved.resolved_at)

    def test_resolve_approval_requires_reason(self):
        approval = self.repo.create(self._make_approval())
        
        for empty_reason in ["", "   ", None]:
            with self.subTest(reason=empty_reason):
                with self.assertRaisesRegex(ApprovalInvariantError, "RESOLUTION_REASON_REQUIRED"):
                    self.repo.resolve_approval(
                        approval_id=approval.approval_id,
                        status=ApprovalStatus.APPROVED,
                        reason=empty_reason
                    )

    def test_cannot_resolve_already_resolved_approval(self):
        approval = self.repo.create(self._make_approval())
        self.repo.resolve_approval(approval.approval_id, ApprovalStatus.APPROVED, "First approval")
        
        with self.assertRaisesRegex(ApprovalInvariantError, "APPROVAL_ALREADY_RESOLVED"):
            self.repo.resolve_approval(approval.approval_id, ApprovalStatus.REJECTED, "Changed mind")

    def test_cannot_resolve_to_pending(self):
        approval = self.repo.create(self._make_approval())
        
        with self.assertRaisesRegex(ApprovalInvariantError, "CANNOT_RESOLVE_TO_PENDING"):
            self.repo.resolve_approval(approval.approval_id, ApprovalStatus.PENDING, "Still thinking")

    def test_resolve_approval_version_conflict_rejected(self):
        approval = self.repo.create(self._make_approval())
        
        with self.assertRaisesRegex(ApprovalInvariantError, "APPROVAL_VERSION_CONFLICT"):
            self.repo.resolve_approval(
                approval_id=approval.approval_id,
                status=ApprovalStatus.APPROVED,
                reason="Race condition test",
                expected_version=99  # Current is 1
            )

    def test_list_pending_and_list_by_mission(self):
        self.repo.create(self._make_approval(app_id="app_11111111", mission_id="mis_A"))
        self.repo.create(self._make_approval(app_id="app_22222222", mission_id="mis_A"))
        self.repo.create(self._make_approval(app_id="app_33333333", mission_id="mis_B"))
        
        # Resolve one
        self.repo.resolve_approval("app_11111111", ApprovalStatus.APPROVED, "Approved")
        
        pending = self.repo.list_pending()
        self.assertEqual(len(pending), 2)
        self.assertNotIn("app_11111111", [p.approval_id for p in pending])
        
        mission_a_approvals = self.repo.list_by_mission("mis_A")
        self.assertEqual(len(mission_a_approvals), 2)
        
        mission_b_approvals = self.repo.list_by_mission("mis_B")
        self.assertEqual(len(mission_b_approvals), 1)

    def test_persistence_failure_preserves_prior_state_without_corruption(self):
        # Create initial valid state
        created = self.repo.create(self._make_approval())
        before_bytes = self.path.read_bytes()
        
        # Mock os.replace to simulate an atomic write failure (e.g., Windows file lock)
        with patch("app.repositories.approval_repository.os.replace", side_effect=PermissionError("Access denied")):
            with self.assertRaisesRegex(ApprovalPersistenceError, "APPROVAL_PERSISTENCE_WRITE_FAILED"):
                self.repo.resolve_approval(created.approval_id, ApprovalStatus.APPROVED, "Should fail")
        
        # The file on disk must remain uncorrupted and identical to its state before the failure
        self.assertEqual(self.path.read_bytes(), before_bytes)
        
        # The in-memory state must remain exactly as it was
        unchanged = self.repo.get(created.approval_id)
        self.assertEqual(unchanged.status, ApprovalStatus.PENDING)
        self.assertEqual(unchanged.version, 1)

if __name__ == "__main__":
    unittest.main()