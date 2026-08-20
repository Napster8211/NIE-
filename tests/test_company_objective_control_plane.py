import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.engine.mission_engine import PersistentMission
from app.repositories.company_objective_repository import (
    CompanyObjectiveRepository,
    ObjectiveInvariantError,
    ObjectivePersistenceError,
)
from app.schemas.company_objective import CompanyObjectiveStatus
from app.schemas.shared_artifacts import DirectorAgentContext
from app.services.company_objective_service import CompanyObjectiveService
from app.services.director_command_resolver import resolve_director_command
from app.services.director_engine import DirectorEngine


class CompanyObjectiveControlPlaneTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.path = Path(self.temp_dir.name) / "objectives.json"
        self.repository = CompanyObjectiveRepository(str(self.path))
        self.service = CompanyObjectiveService(self.repository)

    def tearDown(self):
        self.temp_dir.cleanup()

    def _create(self, **bounds):
        return self.service.create_from_request(
            "Acquire 3 qualified restaurant prospects in Accra.", **bounds
        )

    def test_create_company_objective(self):
        objective = self._create()
        self.assertEqual(CompanyObjectiveStatus.ACTIVE, objective.status)
        self.assertEqual(3, objective.success_criteria.required)
        self.assertEqual("verified_qualified_prospects", objective.success_criteria.criterion)
        self.assertEqual([], objective.linked_mission_ids)
        self.assertEqual(0, objective.progress)

    def test_persist_and_reload(self):
        created = self._create()
        reloaded = CompanyObjectiveRepository(str(self.path)).get(created.objective_id)
        self.assertIsNotNone(reloaded)
        self.assertEqual(created.model_dump(), reloaded.model_dump())

    def test_version_increments_monotonically(self):
        created = self._create()
        updated = self.repository.update(
            created.objective_id, {"priority": "HIGH"}, expected_version=1
        )
        self.assertEqual(2, updated.version)
        with self.assertRaisesRegex(ObjectiveInvariantError, "OBJECTIVE_VERSION_CONFLICT"):
            self.repository.update(
                created.objective_id, {"priority": "LOW"}, expected_version=1
            )

    def test_repeated_atomic_updates_persist_latest_version(self):
        created = self._create()
        for index in range(5):
            self.repository.update(
                created.objective_id, {"metadata": {"save_index": index}}
            )
        reloaded = CompanyObjectiveRepository(str(self.path)).get(created.objective_id)
        self.assertEqual(6, reloaded.version)
        self.assertEqual(4, reloaded.metadata["save_index"])

    def test_link_mission(self):
        created = self._create()
        linked = self.repository.link_mission(created.objective_id, "mis_12345678")
        self.assertEqual(["mis_12345678"], linked.linked_mission_ids)
        self.assertEqual(2, linked.version)

    def test_duplicate_mission_link_rejected(self):
        created = self._create()
        self.repository.link_mission(created.objective_id, "mis_12345678")
        with self.assertRaisesRegex(
            ObjectiveInvariantError, "OBJECTIVE_DUPLICATE_MISSION_LINK"
        ):
            self.repository.link_mission(created.objective_id, "mis_12345678")

    def test_objective_progress_is_bounded_and_derived(self):
        created = self._create()
        progress = self.repository.set_verified_success_count(created.objective_id, 2)
        self.assertEqual(66.67, progress.progress)
        completed_target = self.repository.set_verified_success_count(
            created.objective_id, 100
        )
        self.assertEqual(100, completed_target.progress)
        with self.assertRaises(ObjectiveInvariantError):
            self.repository.update(created.objective_id, {"progress": 101})

    def test_completed_child_mission_does_not_complete_objective(self):
        created = self._create()
        linked = self.repository.link_mission(created.objective_id, "mis_completed")
        self.assertEqual(CompanyObjectiveStatus.ACTIVE, linked.status)
        self.assertEqual(0, linked.verified_success_count)
        self.assertEqual(0, linked.progress)

    def test_objective_completes_only_after_verified_success(self):
        created = self._create()
        with self.assertRaises(ObjectiveInvariantError):
            self.repository.terminal_transition(
                created.objective_id,
                CompanyObjectiveStatus.COMPLETED,
                "SUCCESS_CRITERIA_VERIFIED",
            )
        verified = self.repository.set_verified_success_count(created.objective_id, 3)
        self.assertEqual(CompanyObjectiveStatus.ACTIVE, verified.status)
        completed = self.repository.terminal_transition(
            created.objective_id,
            CompanyObjectiveStatus.COMPLETED,
            "SUCCESS_CRITERIA_VERIFIED",
        )
        self.assertEqual(CompanyObjectiveStatus.COMPLETED, completed.status)
        self.assertEqual(100, completed.progress)

    def test_max_missions_enforced(self):
        created = self._create(max_missions=1)
        self.repository.link_mission(created.objective_id, "mis_first")
        with self.assertRaisesRegex(
            ObjectiveInvariantError, "OBJECTIVE_MAX_MISSIONS_REACHED"
        ):
            self.repository.link_mission(created.objective_id, "mis_second")

    def test_max_strategy_changes_enforced(self):
        created = self._create(max_strategy_changes=1)
        changed = self.repository.record_strategy_change(created.objective_id)
        self.assertEqual(2, changed.current_strategy_version)
        with self.assertRaisesRegex(
            ObjectiveInvariantError, "OBJECTIVE_MAX_STRATEGY_CHANGES_REACHED"
        ):
            self.repository.record_strategy_change(created.objective_id)

    def test_zero_progress_limit_enforced(self):
        created = self._create(max_zero_progress_cycles=1)
        self.repository.record_zero_progress_cycle(created.objective_id)
        with self.assertRaisesRegex(
            ObjectiveInvariantError, "OBJECTIVE_MAX_ZERO_PROGRESS_CYCLES_REACHED"
        ):
            self.repository.record_zero_progress_cycle(created.objective_id)

    def test_terminal_objective_cannot_create_new_work(self):
        created = self._create()
        self.repository.terminal_transition(
            created.objective_id, CompanyObjectiveStatus.CANCELLED, "OWNER_CANCELLED"
        )
        with self.assertRaisesRegex(
            ObjectiveInvariantError, "TERMINAL_OBJECTIVE_CANNOT_CREATE_WORK"
        ):
            self.repository.link_mission(created.objective_id, "mis_forbidden")

    def test_persistence_failure_fails_closed_without_phantom(self):
        with patch(
            "app.repositories.company_objective_repository.os.replace",
            side_effect=PermissionError(5, "Access is denied"),
        ):
            with self.assertRaisesRegex(
                ObjectivePersistenceError, "COMPANY_OBJECTIVE_PERSISTENCE_WRITE_FAILED"
            ):
                self._create()
        self.assertEqual({}, self.repository.snapshot())
        self.assertFalse(self.path.exists())
        self.assertEqual([], list(Path(self.temp_dir.name).glob(".objectives-*.tmp")))

    def test_replacement_failure_preserves_prior_objective(self):
        created = self._create()
        before = self.path.read_bytes()
        with patch(
            "app.repositories.company_objective_repository.os.replace",
            side_effect=PermissionError(5, "Access is denied"),
        ):
            with self.assertRaises(ObjectivePersistenceError):
                self.repository.update(created.objective_id, {"priority": "HIGH"})
        self.assertEqual(before, self.path.read_bytes())
        self.assertEqual("NORMAL", self.repository.get(created.objective_id).priority)
        self.assertEqual([], list(Path(self.temp_dir.name).glob(".objectives-*.tmp")))

    def test_serialization_failure_preserves_prior_objective(self):
        created = self._create()
        before = self.path.read_bytes()
        with self.assertRaisesRegex(
            ObjectivePersistenceError, "COMPANY_OBJECTIVE_PERSISTENCE_WRITE_FAILED"
        ):
            self.repository.update(
                created.objective_id, {"metadata": {"not_json": object()}}
            )
        self.assertEqual(before, self.path.read_bytes())
        self.assertEqual(
            {"source": "director_intake"},
            self.repository.get(created.objective_id).metadata,
        )
        self.assertEqual([], list(Path(self.temp_dir.name).glob(".objectives-*.tmp")))

    def test_read_only_inspection_causes_no_mutation(self):
        created = self._create()
        digest = self.repository.persisted_digest()
        version = created.version
        inspected = self.service.inspect(created.objective_id)
        self.assertEqual(created.objective_id, inspected["objective_id"])
        self.assertEqual(digest, self.repository.persisted_digest())
        self.assertEqual(version, self.repository.get(created.objective_id).version)

    def test_legacy_mission_without_objective_id_remains_valid(self):
        legacy = PersistentMission(
            mission_id="mis_legacy01",
            original_request="Legacy request",
            title="Legacy mission",
            objective="Legacy objective",
            status="ACTIVE",
            priority="HIGH",
            autonomy_level="SEMI-AUTONOMOUS (Level 2)",
            progress=0,
            health="HEALTHY",
            success_criteria_progress="0 / 1 verified LeadArtifact",
            current_phase="Discovery",
        )
        self.assertIsNone(legacy.objective_id)

    def test_mission_record_can_carry_objective_id(self):
        mission = PersistentMission(
            mission_id="mis_linked01",
            objective_id="obj_12345678",
            original_request="Linked request",
            title="Linked mission",
            objective="Linked objective",
            status="ACTIVE",
            priority="HIGH",
            autonomy_level="SEMI-AUTONOMOUS (Level 2)",
            progress=0,
            health="HEALTHY",
            success_criteria_progress="0 / 1 verified LeadArtifact",
            current_phase="Discovery",
        )
        self.assertEqual("obj_12345678", mission.objective_id)

    def test_director_resolves_measurable_objective_without_mission_launch(self):
        route = resolve_director_command(
            "Acquire 3 qualified restaurant prospects in Accra."
        )
        self.assertEqual("OBJECTIVE_CREATE", route["command_class"])
        self.assertEqual("INTERNAL_COMPANY_OBJECTIVE_STATE", route["authority_scope"])
        self.assertTrue(route["objective_creation_allowed"])
        self.assertFalse(route["mission_creation_allowed"])

    def test_director_engine_creates_objective_only(self):
        context = DirectorAgentContext(
            company_id="internal_napstertec",
            query="Acquire 3 qualified restaurant prospects in Accra.",
            operating_mode="OBJECTIVE CREATION MODE",
            command_class="OBJECTIVE_CREATE",
            intent_category="OBJECTIVE_CREATE",
            authority_mode="OBJECTIVE_MUTATION",
            authority_scope="INTERNAL_COMPANY_OBJECTIVE_STATE",
            mutation_allowed=True,
            objective_creation_allowed=True,
            execution_context="COMPANY_OBJECTIVE",
            objective_action="CREATE",
            objective_read_only=False,
            mission_read_only=False,
            coo_artifact_status="NOT_AVAILABLE",
            cfo_artifact_status="NOT_AVAILABLE",
            cro_artifact_status="NOT_AVAILABLE",
            governance_status="Active",
        )
        artifact = asyncio.run(
            DirectorEngine(self.service).execute_director(context, "test_session")
        )
        self.assertEqual("CREATE", artifact.objective_action)
        self.assertEqual(1, len(self.repository.list()))
        self.assertIn("without launching mission work", artifact.executive_summary)

    def test_director_engine_read_only_inspection(self):
        created = self._create()
        digest = self.repository.persisted_digest()
        context = DirectorAgentContext(
            company_id="internal_napstertec",
            query=f"Inspect objective {created.objective_id}",
            operating_mode="OBJECTIVE STATUS MODE",
            command_class="OBJECTIVE_INSPECT",
            intent_category="OBJECTIVE_INSPECT",
            authority_mode="READ_ONLY",
            authority_scope="NONE",
            mutation_allowed=False,
            execution_context="COMPANY_OBJECTIVE",
            objective_id=created.objective_id,
            objective_action="STATUS",
            objective_read_only=True,
            mission_read_only=True,
            coo_artifact_status="NOT_AVAILABLE",
            cfo_artifact_status="NOT_AVAILABLE",
            cro_artifact_status="NOT_AVAILABLE",
            governance_status="Active",
        )
        artifact = asyncio.run(
            DirectorEngine(self.service).execute_director(context, "test_session")
        )
        self.assertTrue(artifact.read_only)
        self.assertEqual(created.objective_id, artifact.objective_id)
        self.assertEqual(digest, self.repository.persisted_digest())


if __name__ == "__main__":
    unittest.main()
