import tempfile
import unittest
from pathlib import Path

from app.agent.agent_models import AgentPermission, AgentResult
from app.engine.autonomous_worker import autonomous_worker
from app.engine.mission_engine import (
    MissionEngine,
    MissionWorkCoordinator,
    mission_registry,
)
from app.schemas.evidence import EvidenceSource


QUALIFIED_CANARY_OBJECTIVE = (
    "Run exactly one qualified-lead canary using genuine LIVE_EXTERNAL evidence. "
    "Research exactly one qualified business prospect and produce exactly one valid LeadArtifact. "
    "Require deterministic business entity qualification and read-only external discovery."
)


class QualifiedLeadRetrySemanticsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_mission_file = mission_registry.mission_file
        with mission_registry.locked(reload=False):
            mission_registry.mission_file = str(Path(self.temp_dir.name) / "missions.json")
            mission_registry.missions = {}
        self.engine = MissionEngine()
        self.coordinator = MissionWorkCoordinator()

    def tearDown(self):
        autonomous_worker.specialist_executor = None
        with mission_registry.locked(reload=False):
            mission_registry.mission_file = self.original_mission_file
            mission_registry.missions = {}
            mission_registry._load_state_unlocked()
        self.temp_dir.cleanup()

    async def _queued_mission(self, objective=QUALIFIED_CANARY_OBJECTIVE):
        mission = mission_registry.create_mission(objective)
        await self.engine._bootstrap_mission(mission, "internal_napstertec")
        request = self.coordinator.claim_ready_request(mission.mission_id)
        delegation = self.coordinator.create_delegation(
            mission.mission_id, request["execution_request_id"]
        )
        return mission, request, delegation

    def _unqualified_artifact(self, source_type, status, reasons):
        source_url = f"https://evidence.example/{source_type.lower()}/candidate"
        return {
            "artifact_id": f"rejected_{source_type.lower()}",
            "artifact_type": "LeadArtifact",
            "verified": False,
            "evidence_source": EvidenceSource.LIVE_EXTERNAL.value,
            "simulation_evidence": False,
            "source_provider": "fixture_live",
            "source_metadata": {
                "provider": "fixture_live",
                "endpoint": "https://search.example.test/",
                "retrieval_type": "read_only_web_search",
                "request_succeeded": True,
                "request_count": 1,
                "result_count": 1,
            },
            "source_url": source_url,
            "source_reference": source_url,
            "source_type": source_type,
            "business_name": "Rejected discovery candidate",
            "entity_qualification": {
                "status": status,
                "source_type": source_type,
                "qualified": False,
                "business_name": "Rejected discovery candidate",
                "source_url": source_url,
                "qualification_reasons": reasons,
            },
            "qualification_reasons": reasons,
        }

    def _qualified_artifact(self):
        source_url = "https://bukarestaurant.com.gh/"
        return {
            **self._unqualified_artifact(
                "OFFICIAL_BUSINESS_SITE",
                "VERIFIED_BUSINESS",
                ["SPECIFIC_BUSINESS_NAME", "DOMAIN_MATCHES_BUSINESS_NAME"],
            ),
            "artifact_id": "qualified_buka",
            "verified": True,
            "source_url": source_url,
            "source_reference": source_url,
            "business_name": "Buka Restaurant",
            "business_category": "restaurant",
            "business_location": "Accra Ghana",
            "business_domain": "bukarestaurant.com.gh",
            "entity_qualification": {
                "status": "VERIFIED_BUSINESS",
                "source_type": "OFFICIAL_BUSINESS_SITE",
                "qualified": True,
                "business_name": "Buka Restaurant",
                "business_category": "restaurant",
                "business_location": "Accra Ghana",
                "business_domain": "bukarestaurant.com.gh",
                "source_url": source_url,
                "qualification_reasons": [
                    "SPECIFIC_BUSINESS_NAME", "DOMAIN_MATCHES_BUSINESS_NAME",
                ],
            },
        }

    async def _run_failed_candidate(self, artifact):
        mission, _, _ = await self._queued_mission()
        calls = []

        async def specialist(mission_id, delegation):
            calls.append((mission_id, delegation["execution_request_id"]))
            return AgentResult(
                success=False,
                agent_name="lead_intelligence",
                session_id="qualified_failure_fixture",
                errors=["BUSINESS_ENTITY_UNVERIFIED"],
                artifacts=[artifact],
            )

        autonomous_worker.specialist_executor = specialist
        processed = await autonomous_worker.process_mission_once(mission.mission_id)
        return mission_registry.get_mission(mission.mission_id), calls, processed

    async def test_live_external_article_is_terminal_without_retry(self):
        current, calls, processed = await self._run_failed_candidate(
            self._unqualified_artifact(
                "ARTICLE", "NON_BUSINESS_SOURCE", ["ARTICLE_OR_INFORMATIONAL_PAGE"]
            )
        )
        self.assertFalse(processed)
        self.assertEqual("WAITING_DIRECTOR", current.status)
        self.assertEqual("BUSINESS_ENTITY_UNVERIFIED", current.terminal_reason)
        self.assertEqual(0, current.retry_count)
        self.assertEqual(1, len(calls))

    async def test_live_external_aggregator_is_terminal_without_retry(self):
        current, calls, _ = await self._run_failed_candidate(
            self._unqualified_artifact(
                "AGGREGATOR", "UNVERIFIED", ["GENERIC_LIST_OR_INFORMATIONAL_TITLE"]
            )
        )
        self.assertEqual("BUSINESS_ENTITY_UNVERIFIED", current.terminal_reason)
        self.assertEqual("TERMINAL_BUSINESS_REJECTION", current.execution_requests[0]["failure_classification"])
        self.assertEqual(0, current.retry_count)
        self.assertEqual(1, len(calls))

    async def test_live_external_insufficient_evidence_is_terminal_without_retry(self):
        current, _, _ = await self._run_failed_candidate(
            self._unqualified_artifact(
                "UNKNOWN", "INSUFFICIENT_EVIDENCE", ["BUSINESS_NAME_MISSING"]
            )
        )
        self.assertEqual("BUSINESS_ENTITY_UNVERIFIED", current.terminal_reason)
        self.assertEqual(1, len(current.execution_requests))
        self.assertEqual(1, len(current.delegation_history))
        self.assertEqual(1, len(current.worker_claims))

    async def test_valid_qualified_business_completes_once(self):
        mission, _, _ = await self._queued_mission()
        calls = []

        async def specialist(mission_id, delegation):
            calls.append((mission_id, delegation["execution_request_id"]))
            return AgentResult(
                success=True,
                agent_name="lead_intelligence",
                session_id="qualified_success_fixture",
                artifacts=[self._qualified_artifact()],
            )

        autonomous_worker.specialist_executor = specialist
        self.assertTrue(await autonomous_worker.process_mission_once(mission.mission_id))
        current = mission_registry.get_mission(mission.mission_id)
        self.assertEqual("COMPLETED", current.status)
        self.assertEqual(100, current.progress)
        self.assertEqual("1 / 1 verified LeadArtifact", current.success_criteria_progress)
        self.assertEqual(0, current.retry_count)
        self.assertEqual(1, len(current.execution_requests))
        self.assertEqual(1, len(current.artifact_lineage))
        self.assertEqual(1, len(calls))

    async def test_qualified_scope_survives_complete_execution_lineage(self):
        mission, request, delegation = await self._queued_mission()
        expected_scope = {
            "category": "restaurant",
            "location": "Accra Ghana",
            "max_results": 1,
            "candidate_scan_limit": 5,
            "query_mode": "QUALIFIED_ENTITY_SEARCH",
        }
        current = mission_registry.get_mission(mission.mission_id)

        self.assertEqual(expected_scope, current.discovery_scope)
        self.assertEqual(expected_scope, current.milestones[0]["discovery_scope"])
        self.assertEqual(expected_scope, current.progression_decisions[0]["discovery_scope"])
        self.assertEqual(expected_scope, current.progression_materializations[0]["discovery_scope"])
        self.assertEqual(expected_scope, request["discovery_scope"])
        self.assertEqual(expected_scope, delegation["discovery_scope"])

        claimed_delegation, claim = self.coordinator.claim_pending_delegation(
            mission.mission_id, "scope_worker"
        )
        self.assertEqual(expected_scope, claim["discovery_scope"])
        context = autonomous_worker._build_specialist_context(
            mission.mission_id, claimed_delegation
        )
        self.assertEqual(expected_scope, context.planner_output["discovery_scope"])
        self.assertEqual("restaurant", context.planner_output["query"])
        self.assertEqual("Accra Ghana", context.planner_output["location"])
        self.assertEqual("QUALIFIED_ENTITY_SEARCH", context.planner_output["query_mode"])
        self.assertEqual(1, context.planner_output["target_count"])
        self.assertNotIn("LeadArtifact production", context.planner_output["query"])

    async def test_incomplete_qualified_scope_is_terminal_without_retry(self):
        mission, request, delegation = await self._queued_mission()
        self.coordinator.claim_pending_delegation(mission.mission_id, "scope_failure_worker")

        await self.engine.process_execution_failure(
            mission.mission_id,
            delegation_id=delegation["delegation_id"],
            error="DISCOVERY_SCOPE_INCOMPLETE",
        )

        current = mission_registry.get_mission(mission.mission_id)
        self.assertEqual("WAITING_DIRECTOR", current.status)
        self.assertEqual("DISCOVERY_SCOPE_INCOMPLETE", current.terminal_reason)
        self.assertEqual("DISCOVERY_SCOPE_INCOMPLETE", current.last_error)
        self.assertEqual("TERMINAL_SCOPE_CONFIGURATION_FAILURE", request.get("failure_classification") or current.execution_requests[0]["failure_classification"])
        self.assertEqual(0, current.retry_count)
        self.assertEqual(1, len(current.execution_requests))
        self.assertEqual([], current.artifact_lineage)

    async def test_qualified_provider_timeout_is_terminal_after_one_request(self):
        mission, _, _ = await self._queued_mission()
        calls = []
        discovery_failure = {
            "provider_used": "fixture_live",
            "provider_mode": "unavailable",
            "evidence_source": EvidenceSource.UNKNOWN.value,
            "simulation_evidence": False,
            "source_metadata": {
                "provider": "fixture_live",
                "endpoint": "https://search.example.test/",
                "retrieval_type": "read_only_web_search",
                "request_succeeded": False,
                "request_count": 1,
                "result_count": 0,
            },
            "results": [],
            "error_code": "LIVE_EVIDENCE_UNAVAILABLE",
            "error": "TimeoutError",
        }

        async def specialist(mission_id, delegation):
            calls.append((mission_id, delegation["execution_request_id"]))
            return AgentResult(
                success=False,
                agent_name="lead_intelligence",
                session_id="qualified_timeout_fixture",
                errors=["LIVE_EVIDENCE_UNAVAILABLE:TimeoutError"],
                tool_calls=[{"output": discovery_failure}],
            )

        autonomous_worker.specialist_executor = specialist
        self.assertFalse(await autonomous_worker.process_mission_once(mission.mission_id))
        current = mission_registry.get_mission(mission.mission_id)
        self.assertEqual("LIVE_EVIDENCE_UNAVAILABLE", current.terminal_reason)
        self.assertEqual(0, current.retry_count)
        self.assertEqual(1, len(current.execution_requests))
        self.assertEqual(1, len(current.delegation_history))
        self.assertEqual(1, len(current.worker_claims))
        self.assertEqual(1, len(calls))
        failure = current.execution_requests[0]["failure_evidence"]
        self.assertEqual(EvidenceSource.UNKNOWN.value, failure["evidence_source"])
        self.assertEqual("fixture_live", failure["source_provider"])
        self.assertEqual(1, failure["source_metadata"]["request_count"])

    async def test_transient_timeout_retries_when_non_canary_policy_allows(self):
        mission, _, delegation = await self._queued_mission(
            "Create a mission to acquire 1 new restaurant client."
        )
        claimed, _ = self.coordinator.claim_pending_delegation(
            mission.mission_id, "transient_worker"
        )
        self.coordinator.mark_delegation_running(
            mission.mission_id, claimed["delegation_id"]
        )
        await self.engine.process_execution_failure(
            mission.mission_id,
            delegation_id=delegation["delegation_id"],
            error="TRANSIENT_DISCOVERY_TIMEOUT",
        )
        current = mission_registry.get_mission(mission.mission_id)
        self.assertEqual(1, current.retry_count)
        self.assertTrue(any(r["attempt"] == 2 and r["status"] == "READY" for r in current.execution_requests))
        self.assertEqual("TRANSIENT_RETRYABLE", current.execution_requests[0]["failure_classification"])

    async def test_retry_limit_requires_repeated_transient_failures(self):
        mission = mission_registry.create_mission(
            "Create a mission to acquire 1 new restaurant client."
        )
        await self.engine._bootstrap_mission(mission, "internal_napstertec")
        for attempt in range(1, 4):
            request = self.coordinator.claim_ready_request(mission.mission_id)
            delegation = self.coordinator.create_delegation(
                mission.mission_id, request["execution_request_id"]
            )
            claimed, _ = self.coordinator.claim_pending_delegation(
                mission.mission_id, f"transient_worker_{attempt}"
            )
            self.coordinator.mark_delegation_running(
                mission.mission_id, claimed["delegation_id"]
            )
            await self.engine.process_execution_failure(
                mission.mission_id,
                delegation_id=delegation["delegation_id"],
                error="TEMPORARY_PROVIDER_FAILURE",
            )

        current = mission_registry.get_mission(mission.mission_id)
        self.assertEqual("EXECUTION_RETRY_LIMIT_REACHED", current.terminal_reason)
        self.assertEqual(2, current.retry_count)
        self.assertEqual(3, len(current.execution_requests))
        self.assertTrue(all(
            request["failure_classification"] == "TRANSIENT_RETRYABLE"
            for request in current.execution_requests
        ))

    async def test_rejected_entity_preserves_live_evidence_diagnostics(self):
        current, _, _ = await self._run_failed_candidate(
            self._unqualified_artifact(
                "REPORT", "NON_BUSINESS_SOURCE", ["REPORT_OR_RESEARCH_PAGE"]
            )
        )
        evidence = current.execution_requests[0]["failure_evidence"]
        self.assertEqual(EvidenceSource.LIVE_EXTERNAL.value, evidence["evidence_source"])
        self.assertEqual("fixture_live", evidence["source_provider"])
        self.assertEqual("REPORT", evidence["source_type"])
        self.assertTrue(evidence["source_url"].startswith("https://"))
        self.assertEqual(["REPORT_OR_RESEARCH_PAGE"], evidence["qualification_reasons"])
        self.assertEqual("NON_BUSINESS_SOURCE", evidence["entity_qualification"]["status"])
        report = await self.engine.process_mission_request(
            "MISSION STATUS MODE", current.mission_id, "failure_evidence_report"
        )
        self.assertEqual(EvidenceSource.LIVE_EXTERNAL.value, report.evidence_source)
        self.assertEqual(evidence, report.execution_metadata["failure_evidence"])

    async def test_entity_rejection_has_exactly_one_execution_chain(self):
        current, calls, _ = await self._run_failed_candidate(
            self._unqualified_artifact(
                "SEARCH_PAGE", "NON_BUSINESS_SOURCE", ["SEARCH_RESULTS_PAGE"]
            )
        )
        self.assertEqual(1, len(calls))
        self.assertEqual(1, len(current.execution_requests))
        self.assertEqual(1, len(current.delegation_history))
        self.assertEqual(1, len(current.worker_claims))
        self.assertEqual(1, current.execution_requests[0]["failure_evidence"]["source_metadata"]["request_count"])
        self.assertEqual(0, current.replan_count)

    async def test_qualified_canary_preserves_external_safety(self):
        mission, _, delegation = await self._queued_mission()
        context = autonomous_worker._build_specialist_context(
            mission.mission_id, delegation
        )
        self.assertIn("lead_upsert", context.runtime_metadata["blocked_tools"])
        self.assertFalse(context.runtime_metadata["external_write_allowed"])
        self.assertFalse(context.runtime_metadata["outreach_allowed"])
        self.assertNotIn(AgentPermission.OUTREACH, context.granted_permissions)
        self.assertEqual([], mission_registry.get_mission(mission.mission_id).external_operations)

    async def test_legacy_quarantine_record_is_unchanged(self):
        legacy = mission_registry.create_mission(
            "Create a mission to acquire 1 new restaurant client."
        )
        with mission_registry.locked():
            current = mission_registry.missions[legacy.mission_id]
            current.status = "WAITING_DIRECTOR"
            current.execution_state = "BLOCKED"
            current.terminal_reason = "LEGACY_QUARANTINE"
            current.escalation_reason = "LEGACY_QUARANTINE"
            current.auto_continue_status = "STOPPED"
            mission_registry.save_mission(current)
        before = mission_registry.get_mission(legacy.mission_id).model_dump(mode="json")

        await self._run_failed_candidate(
            self._unqualified_artifact(
                "ARTICLE", "NON_BUSINESS_SOURCE", ["ARTICLE_OR_INFORMATIONAL_PAGE"]
            )
        )

        self.assertEqual(
            before,
            mission_registry.get_mission(legacy.mission_id).model_dump(mode="json"),
        )


if __name__ == "__main__":
    unittest.main()
