import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.engine.event_bus import EnterpriseEventBus
from app.engine.mission_engine import MissionRegistry, PersistentMission
from app.repositories.company_objective_repository import CompanyObjectiveRepository
from app.repositories.executive_decision_repository import (
    DuplicateExecutiveDecisionError,
    ExecutiveDecisionPersistenceError,
    ExecutiveDecisionRepository,
)
from app.schemas.company_objective import (
    CompanyObjective,
    CompanyObjectiveStatus,
    CompanyObjectiveSuccessCriteria,
)
from app.schemas.shared_artifacts import DirectorAgentContext, ExecutiveDecisionType
from app.services.company_objective_service import CompanyObjectiveService
from app.services.director_engine import DirectorEngine
from app.services.post_mission_evaluation import (
    MissionTerminalEvent,
    PostMissionEvaluationCoordinator,
    PostMissionEvaluationError,
)


class FakeMissionSource:
    def __init__(self):
        self.missions = {}

    def add(self, mission):
        self.missions[mission.mission_id] = mission
        return mission

    def get_mission(self, mission_id):
        return self.missions.get(mission_id)


def make_artifact(mission_id, artifact_id="art_lead_1", **overrides):
    artifact = {
        "artifact_id": artifact_id,
        "artifact_type": "LeadArtifact",
        "verified": True,
        "mission_id": mission_id,
        "plan_version": "v1",
        "milestone_id": "m1",
        "decision_id": "dec_1",
        "materialization_id": "mat_1",
        "execution_request_id": "mer_1",
        "delegation_id": "del_1",
        "worker_claim_id": "wcl_1",
        "specialist": "lead_intelligence",
        "evidence_source": "LIVE_EXTERNAL",
        "simulation_evidence": False,
        "source_provider": "duckduckgo",
        "source_metadata": {"request_succeeded": True},
        "entity_qualification": {
            "status": "VERIFIED_BUSINESS",
            "qualified": True,
        },
        "created_at": "2026-08-17T00:00:00+00:00",
    }
    artifact.update(overrides)
    return artifact


def make_mission(
    mission_id,
    objective_id,
    *,
    status="COMPLETED",
    artifact_id="art_lead_1",
    artifact=None,
    achieved=True,
    revision=1,
):
    artifacts = []
    if artifact is not False:
        artifacts = [artifact or make_artifact(mission_id, artifact_id)]
    return PersistentMission(
        mission_id=mission_id,
        objective_id=objective_id,
        original_request="Find one qualified prospect.",
        title="Qualified prospect",
        objective="Find one qualified prospect.",
        status=status,
        priority="HIGH",
        autonomy_level="SUPERVISED",
        progress=100 if status == "COMPLETED" else 0,
        health="HEALTHY",
        success_criteria_progress="1 / 1 verified LeadArtifact" if achieved else "0 / 1 verified LeadArtifact",
        mission_type="ARTIFACT_PRODUCTION",
        verification_mode="QUALIFIED_LEAD_CANARY",
        simulation_mode=False,
        mission_objective_achieved=achieved,
        current_phase="Qualification",
        success_criteria={
            "criterion": "verified_artifacts",
            "required": 1,
            "artifact_type": "LeadArtifact",
            "verification_mode": "QUALIFIED_LEAD_CANARY",
        },
        artifact_lineage=artifacts,
        external_operations=[],
        terminal_reason="SUCCESS_CRITERIA_VERIFIED" if status == "COMPLETED" else status,
        state_revision=revision,
    )


class ExecutiveDecisionLoopTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.objectives = CompanyObjectiveRepository(str(root / "objectives.json"))
        self.decisions = ExecutiveDecisionRepository(str(root / "decisions.json"))
        self.missions = FakeMissionSource()
        self.coordinator = PostMissionEvaluationCoordinator(
            objective_repository=self.objectives,
            decision_repository=self.decisions,
            mission_source=self.missions,
        )

    def tearDown(self):
        self.temp.cleanup()

    def create_objective(
        self,
        objective_id="obj_test0001",
        required=3,
        max_missions=5,
        max_strategy_changes=3,
        max_zero_progress_cycles=3,
        status=CompanyObjectiveStatus.ACTIVE,
    ):
        return self.objectives.create(CompanyObjective(
            objective_id=objective_id,
            title="Acquire qualified restaurant prospects",
            objective="Acquire qualified restaurant prospects in Accra.",
            status=status,
            success_criteria=CompanyObjectiveSuccessCriteria(
                criterion="verified_qualified_prospects",
                required=required,
                unit="qualified_prospects",
                evidence_requirements=["verified_business_entity"],
            ),
            max_missions=max_missions,
            max_strategy_changes=max_strategy_changes,
            max_zero_progress_cycles=max_zero_progress_cycles,
        ))

    def add_linked(self, objective_id, mission):
        self.objectives.link_mission(objective_id, mission.mission_id)
        return self.missions.add(mission)

    def evaluate(self, mission):
        return self.coordinator.evaluate(MissionTerminalEvent.from_mission(mission))

    def test_completed_verified_artifact_advances_objective_zero_to_one(self):
        objective = self.create_objective()
        mission = self.add_linked(
            objective.objective_id,
            make_mission("mis_progress1", objective.objective_id),
        )
        decision = self.evaluate(mission)
        updated = self.objectives.get(objective.objective_id)
        self.assertEqual(updated.verified_success_count, 1)
        self.assertEqual(updated.progress, 33.33)
        self.assertEqual(decision.decision_type, ExecutiveDecisionType.FOLLOW_UP_MISSION)

    def test_child_completion_does_not_complete_unmet_objective(self):
        objective = self.create_objective(required=3)
        mission = self.add_linked(
            objective.objective_id, make_mission("mis_child001", objective.objective_id)
        )
        self.evaluate(mission)
        self.assertEqual(
            self.objectives.get(objective.objective_id).status,
            CompanyObjectiveStatus.ACTIVE,
        )

    def test_final_verified_artifact_completes_objective(self):
        objective = self.create_objective(required=1)
        mission = self.add_linked(
            objective.objective_id, make_mission("mis_final001", objective.objective_id)
        )
        decision = self.evaluate(mission)
        updated = self.objectives.get(objective.objective_id)
        self.assertEqual(decision.decision_type, ExecutiveDecisionType.OBJECTIVE_COMPLETE)
        self.assertEqual(updated.status, CompanyObjectiveStatus.COMPLETED)
        self.assertEqual(updated.progress, 100.0)

    def test_duplicate_terminal_event_returns_same_decision_without_credit(self):
        objective = self.create_objective()
        mission = self.add_linked(
            objective.objective_id, make_mission("mis_duplicate", objective.objective_id)
        )
        event = MissionTerminalEvent.from_mission(mission)
        first = self.coordinator.evaluate(event)
        version = self.objectives.get(objective.objective_id).version
        second = self.coordinator.evaluate(event)
        self.assertEqual(first.decision_id, second.decision_id)
        self.assertEqual(self.objectives.get(objective.objective_id).version, version)
        self.assertEqual(self.objectives.get(objective.objective_id).verified_success_count, 1)

    def test_same_terminal_state_with_new_revision_is_idempotent(self):
        objective = self.create_objective()
        mission = self.add_linked(
            objective.objective_id, make_mission("mis_revision1", objective.objective_id)
        )
        first = self.evaluate(mission)
        mission.state_revision += 1
        mission.updated_at = "2026-08-17T01:00:00+00:00"
        second = self.evaluate(mission)
        self.assertEqual(first.decision_id, second.decision_id)
        self.assertEqual(len(self.decisions.list_by_mission(mission.mission_id)), 1)

    def test_duplicate_artifact_across_missions_is_not_credited_twice(self):
        objective = self.create_objective(required=3)
        first = self.add_linked(
            objective.objective_id,
            make_mission("mis_artdup01", objective.objective_id, artifact_id="art_shared"),
        )
        second = self.add_linked(
            objective.objective_id,
            make_mission("mis_artdup02", objective.objective_id, artifact_id="art_shared"),
        )
        self.evaluate(first)
        duplicate_decision = self.evaluate(second)
        updated = self.objectives.get(objective.objective_id)
        self.assertEqual(updated.verified_success_count, 1)
        self.assertEqual(duplicate_decision.evidence_artifact_ids, [])

    def test_unverified_evidence_does_not_advance_progress(self):
        objective = self.create_objective()
        artifact = make_artifact("mis_unverify", verified=False)
        mission = self.add_linked(
            objective.objective_id,
            make_mission(
                "mis_unverify", objective.objective_id,
                artifact=artifact, achieved=False,
            ),
        )
        decision = self.evaluate(mission)
        self.assertEqual(self.objectives.get(objective.objective_id).verified_success_count, 0)
        self.assertEqual(decision.decision_type, ExecutiveDecisionType.CHANGE_STRATEGY)

    def test_mock_evidence_never_advances_objective(self):
        objective = self.create_objective()
        artifact = make_artifact(
            "mis_mock0001",
            evidence_source="MOCK_FALLBACK",
            simulation_evidence=True,
        )
        mission = make_mission(
            "mis_mock0001", objective.objective_id, artifact=artifact, achieved=True
        )
        mission.verification_mode = "STRUCTURAL_CANARY"
        mission.success_criteria["verification_mode"] = "STRUCTURAL_CANARY"
        self.add_linked(objective.objective_id, mission)
        self.evaluate(mission)
        self.assertEqual(self.objectives.get(objective.objective_id).verified_success_count, 0)

    def test_failed_mission_chooses_bounded_strategy_change(self):
        objective = self.create_objective()
        mission = self.add_linked(
            objective.objective_id,
            make_mission("mis_failed01", objective.objective_id, status="FAILED", artifact=False, achieved=False),
        )
        decision = self.evaluate(mission)
        self.assertEqual(decision.decision_type, ExecutiveDecisionType.CHANGE_STRATEGY)
        updated = self.objectives.get(objective.objective_id)
        self.assertEqual(updated.strategy_change_count, 1)
        self.assertEqual(updated.current_strategy_version, 2)

    def test_blocked_mission_waits(self):
        objective = self.create_objective()
        mission = self.add_linked(
            objective.objective_id,
            make_mission("mis_blocked1", objective.objective_id, status="BLOCKED", artifact=False, achieved=False),
        )
        decision = self.evaluate(mission)
        self.assertEqual(decision.decision_type, ExecutiveDecisionType.WAIT)
        self.assertEqual(
            self.objectives.get(objective.objective_id).status,
            CompanyObjectiveStatus.WAITING_DIRECTOR,
        )

    def test_waiting_director_does_not_authorize_side_effect(self):
        objective = self.create_objective()
        mission = self.add_linked(
            objective.objective_id,
            make_mission("mis_waiting1", objective.objective_id, status="WAITING_DIRECTOR", artifact=False, achieved=False),
        )
        decision = self.evaluate(mission)
        self.assertEqual(decision.decision_type, ExecutiveDecisionType.WAIT)
        self.assertIsNone(decision.selected_follow_up_action)
        self.assertFalse(decision.action_executed)

    def test_exhausted_mission_escalates_and_exhausts_objective(self):
        objective = self.create_objective()
        mission = self.add_linked(
            objective.objective_id,
            make_mission("mis_exhaust1", objective.objective_id, status="EXHAUSTED", artifact=False, achieved=False),
        )
        decision = self.evaluate(mission)
        self.assertEqual(decision.decision_type, ExecutiveDecisionType.ESCALATE)
        self.assertEqual(
            self.objectives.get(objective.objective_id).status,
            CompanyObjectiveStatus.EXHAUSTED,
        )

    def test_cancelled_mission_stops_follow_up(self):
        objective = self.create_objective()
        mission = self.add_linked(
            objective.objective_id,
            make_mission("mis_cancel01", objective.objective_id, status="CANCELLED", artifact=False, achieved=False),
        )
        decision = self.evaluate(mission)
        self.assertEqual(decision.decision_type, ExecutiveDecisionType.STOP)
        self.assertIsNone(decision.selected_follow_up_action)
        self.assertEqual(
            self.objectives.get(objective.objective_id).status,
            CompanyObjectiveStatus.CANCELLED,
        )

    def test_max_strategy_changes_escalates(self):
        objective = self.create_objective(max_strategy_changes=0)
        mission = self.add_linked(
            objective.objective_id,
            make_mission("mis_stratmax", objective.objective_id, status="FAILED", artifact=False, achieved=False),
        )
        decision = self.evaluate(mission)
        self.assertEqual(decision.decision_type, ExecutiveDecisionType.ESCALATE)
        self.assertEqual(self.objectives.get(objective.objective_id).strategy_change_count, 0)

    def test_zero_progress_increments(self):
        objective = self.create_objective()
        mission = self.add_linked(
            objective.objective_id,
            make_mission("mis_zero000", objective.objective_id, status="FAILED", artifact=False, achieved=False),
        )
        self.evaluate(mission)
        self.assertEqual(self.objectives.get(objective.objective_id).zero_progress_cycles, 1)

    def test_verified_progress_resets_zero_progress(self):
        objective = self.create_objective(required=3)
        failed = self.add_linked(
            objective.objective_id,
            make_mission("mis_zero001", objective.objective_id, status="FAILED", artifact=False, achieved=False),
        )
        self.evaluate(failed)
        success = self.add_linked(
            objective.objective_id,
            make_mission("mis_zero002", objective.objective_id, artifact_id="art_reset"),
        )
        self.evaluate(success)
        self.assertEqual(self.objectives.get(objective.objective_id).zero_progress_cycles, 0)

    def test_max_zero_progress_escalates(self):
        objective = self.create_objective(max_zero_progress_cycles=1)
        mission = self.add_linked(
            objective.objective_id,
            make_mission("mis_zeromax1", objective.objective_id, status="FAILED", artifact=False, achieved=False),
        )
        decision = self.evaluate(mission)
        self.assertEqual(decision.decision_type, ExecutiveDecisionType.ESCALATE)
        self.assertEqual(
            self.objectives.get(objective.objective_id).status,
            CompanyObjectiveStatus.ESCALATED,
        )

    def test_mission_limit_prevents_executable_follow_up(self):
        objective = self.create_objective(required=3, max_missions=1)
        mission = self.add_linked(
            objective.objective_id,
            make_mission("mis_limit001", objective.objective_id),
        )
        decision = self.evaluate(mission)
        self.assertEqual(decision.decision_type, ExecutiveDecisionType.ESCALATE)
        self.assertIsNone(decision.selected_follow_up_action)
        self.assertEqual(
            self.objectives.get(objective.objective_id).status,
            CompanyObjectiveStatus.EXHAUSTED,
        )

    def test_terminal_objective_never_authorizes_follow_up(self):
        objective = self.create_objective(required=1, max_missions=2)
        first = self.add_linked(
            objective.objective_id,
            make_mission("mis_term0001", objective.objective_id, artifact_id="art_term1"),
        )
        second = self.add_linked(
            objective.objective_id,
            make_mission("mis_term0002", objective.objective_id, artifact_id="art_term2"),
        )
        self.evaluate(first)
        decision = self.evaluate(second)
        self.assertEqual(decision.decision_type, ExecutiveDecisionType.NO_ACTION)
        self.assertTrue(decision.terminal)
        self.assertIsNone(decision.selected_follow_up_action)

    def test_waiting_approval_never_authorizes_follow_up(self):
        objective = self.create_objective(status=CompanyObjectiveStatus.WAITING_APPROVAL)
        mission = self.add_linked(
            objective.objective_id,
            make_mission("mis_approval", objective.objective_id, status="FAILED", artifact=False, achieved=False),
        )
        decision = self.evaluate(mission)
        self.assertEqual(decision.decision_type, ExecutiveDecisionType.WAIT)
        self.assertTrue(decision.approval_required)
        self.assertIsNone(decision.selected_follow_up_action)
        self.assertEqual(
            self.objectives.get(objective.objective_id).status,
            CompanyObjectiveStatus.WAITING_APPROVAL,
        )

    def test_decision_persistence_failure_does_not_update_objective(self):
        objective = self.create_objective()
        mission = self.add_linked(
            objective.objective_id, make_mission("mis_dec_fail", objective.objective_id)
        )
        version = self.objectives.get(objective.objective_id).version
        with patch.object(
            self.decisions,
            "create",
            side_effect=ExecutiveDecisionPersistenceError("denied"),
        ):
            with self.assertRaisesRegex(
                PostMissionEvaluationError, "EXECUTIVE_DECISION_PERSISTENCE_FAILED"
            ):
                self.evaluate(mission)
        unchanged = self.objectives.get(objective.objective_id)
        self.assertEqual(unchanged.version, version)
        self.assertEqual(unchanged.verified_success_count, 0)

    def test_objective_update_failure_rolls_back_decision(self):
        objective = self.create_objective()
        mission = self.add_linked(
            objective.objective_id, make_mission("mis_obj_fail", objective.objective_id)
        )
        with patch.object(self.objectives, "update", side_effect=RuntimeError("denied")):
            with self.assertRaisesRegex(
                PostMissionEvaluationError, "OBJECTIVE_UPDATE_PERSISTENCE_FAILED"
            ):
                self.evaluate(mission)
        self.assertEqual(self.decisions.list_by_objective(objective.objective_id), [])
        self.assertEqual(self.objectives.get(objective.objective_id).verified_success_count, 0)

    def test_decision_lineage_names_mission_event_and_artifact(self):
        objective = self.create_objective()
        mission = self.add_linked(
            objective.objective_id,
            make_mission("mis_lineage1", objective.objective_id, artifact_id="art_lineage"),
        )
        event = MissionTerminalEvent.from_mission(mission)
        decision = self.coordinator.evaluate(event)
        self.assertEqual(decision.objective_id, objective.objective_id)
        self.assertEqual(decision.mission_id, mission.mission_id)
        self.assertEqual(decision.mission_terminal_event_id, event.event_id)
        self.assertEqual(decision.evidence_artifact_ids, ["art_lineage"])
        self.assertEqual(
            decision.evidence_summary["artifact_provenance"][0]["mission_id"],
            mission.mission_id,
        )

    def test_read_only_status_does_not_mutate_registries(self):
        objective = self.create_objective()
        mission = self.add_linked(
            objective.objective_id, make_mission("mis_inspect1", objective.objective_id)
        )
        self.evaluate(mission)
        objective_digest = self.objectives.persisted_digest()
        decision_digest = self.decisions.persisted_digest()
        status = self.coordinator.inspect_objective(objective.objective_id)
        self.assertEqual(status["latest_executive_decision"]["mission_id"], mission.mission_id)
        self.assertEqual(self.objectives.persisted_digest(), objective_digest)
        self.assertEqual(self.decisions.persisted_digest(), decision_digest)

    def test_director_read_only_status_includes_latest_executive_decision(self):
        objective = self.create_objective()
        mission = self.add_linked(
            objective.objective_id, make_mission("mis_director1", objective.objective_id)
        )
        decision = self.evaluate(mission)
        objective_digest = self.objectives.persisted_digest()
        decision_digest = self.decisions.persisted_digest()
        context = DirectorAgentContext(
            company_id="internal_napstertec",
            query=f"Inspect objective {objective.objective_id}",
            operating_mode="OBJECTIVE STATUS MODE",
            command_class="OBJECTIVE_INSPECT",
            intent_category="OBJECTIVE_INSPECT",
            authority_mode="READ_ONLY",
            authority_scope="NONE",
            mutation_allowed=False,
            execution_context="COMPANY_OBJECTIVE",
            objective_id=objective.objective_id,
            objective_action="STATUS",
            objective_read_only=True,
            mission_read_only=True,
            coo_artifact_status="NOT_AVAILABLE",
            cfo_artifact_status="NOT_AVAILABLE",
            cro_artifact_status="NOT_AVAILABLE",
            governance_status="Active",
        )
        artifact = asyncio.run(DirectorEngine(
            CompanyObjectiveService(self.objectives),
            post_mission_coordinator=self.coordinator,
        ).execute_director(context, "test_session"))
        self.assertEqual(
            artifact.execution_metadata["latest_executive_decision"]["decision_id"],
            decision.decision_id,
        )
        self.assertEqual(self.objectives.persisted_digest(), objective_digest)
        self.assertEqual(self.decisions.persisted_digest(), decision_digest)

    def test_missing_or_mismatched_lineage_fails_closed(self):
        objective = self.create_objective()
        mission = self.missions.add(make_mission("mis_orphan01", objective.objective_id))
        with self.assertRaisesRegex(
            PostMissionEvaluationError, "MISSION_NOT_LINKED_TO_OBJECTIVE"
        ):
            self.evaluate(mission)
        self.assertEqual(self.decisions.list_by_objective(objective.objective_id), [])

    def test_decision_repository_is_persisted_and_immutable(self):
        objective = self.create_objective()
        mission = self.add_linked(
            objective.objective_id, make_mission("mis_persist1", objective.objective_id)
        )
        decision = self.evaluate(mission)
        reloaded = ExecutiveDecisionRepository(self.decisions.storage_path)
        self.assertEqual(reloaded.get(decision.decision_id), decision)
        with self.assertRaises(DuplicateExecutiveDecisionError):
            reloaded.create(decision.model_copy(update={"decision_id": "exd_duplicate"}))

    def test_event_bus_terminal_evaluation_is_idempotent(self):
        objective = self.create_objective()
        mission = self.add_linked(
            objective.objective_id, make_mission("mis_event001", objective.objective_id)
        )
        event = MissionTerminalEvent.from_mission(mission).to_business_event()
        bus = object.__new__(EnterpriseEventBus)
        bus.subscribers = {}
        bus.subscribe("MISSION_TERMINAL", self.coordinator.handle_business_event)
        first = asyncio.run(bus.publish_and_wait(event))
        second = asyncio.run(bus.publish_and_wait(event))
        self.assertEqual(first[0].decision_id, second[0].decision_id)
        self.assertEqual(len(self.decisions.list_by_objective(objective.objective_id)), 1)
        self.assertEqual(self.objectives.get(objective.objective_id).verified_success_count, 1)

    def test_mission_registry_terminal_hook_emits_one_canonical_event(self):
        objective = self.create_objective()
        mission = make_mission("mis_hook0001", objective.objective_id)

        async def exercise():
            publisher = AsyncMock(return_value=[])
            with patch(
                "app.engine.mission_engine.event_bus.publish_and_wait", publisher
            ):
                MissionRegistry._schedule_terminal_objective_evaluation(mission)
                await asyncio.sleep(0)
                await asyncio.sleep(0)
            return publisher

        publisher = asyncio.run(exercise())
        publisher.assert_awaited_once()
        published_event = publisher.await_args.args[0]
        self.assertEqual(published_event.event_type, "MISSION_TERMINAL")
        self.assertEqual(published_event.correlation_id, mission.mission_id)

    def test_malformed_terminal_event_fails_closed(self):
        objective = self.create_objective()
        mission = self.add_linked(
            objective.objective_id, make_mission("mis_bad_event", objective.objective_id)
        )
        event = MissionTerminalEvent.from_mission(mission).to_business_event()
        event.execution_metadata.pop("mission_id")
        with self.assertRaisesRegex(PostMissionEvaluationError, "MALFORMED"):
            asyncio.run(self.coordinator.handle_business_event(event))
        self.assertEqual(self.decisions.list_by_objective(objective.objective_id), [])

    def test_follow_up_is_proposed_not_executed(self):
        objective = self.create_objective(required=3)
        mission = self.add_linked(
            objective.objective_id, make_mission("mis_follow001", objective.objective_id)
        )
        before_links = list(self.objectives.get(objective.objective_id).linked_mission_ids)
        decision = self.evaluate(mission)
        self.assertEqual(decision.decision_type, ExecutiveDecisionType.FOLLOW_UP_MISSION)
        self.assertFalse(decision.selected_follow_up_action["executable"])
        self.assertFalse(decision.action_executed)
        self.assertEqual(
            self.objectives.get(objective.objective_id).linked_mission_ids,
            before_links,
        )

    def test_atomic_decision_replace_failure_preserves_prior_registry(self):
        objective = self.create_objective()
        mission = self.add_linked(
            objective.objective_id, make_mission("mis_atomic01", objective.objective_id)
        )
        decision = self.evaluate(mission)
        storage = Path(self.decisions.storage_path)
        before = storage.read_bytes()
        proposed = decision.model_copy(update={
            "decision_id": "exd_atomic_failure",
            "mission_id": "mis_atomic02",
            "mission_terminal_event_id": "mte_atomic_failure",
            "mission_terminal_state": "FAILED",
        })
        with patch(
            "app.repositories.executive_decision_repository.os.replace",
            side_effect=PermissionError("denied"),
        ):
            with self.assertRaises(ExecutiveDecisionPersistenceError):
                self.decisions.create(proposed)
        self.assertEqual(storage.read_bytes(), before)
        self.assertIsNone(self.decisions.get(proposed.decision_id))
        self.assertEqual(
            list(storage.parent.glob(".executive-decisions-*.tmp")), []
        )


if __name__ == "__main__":
    unittest.main()
