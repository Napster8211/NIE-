import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from app.engine.mission_engine import (
    MAX_STALL_RECOVERIES,
    MAX_ZERO_PROGRESS_PLANS,
    MissionAuditService,
    MissionCompletionGuard,
    MissionEngine,
    MissionExecutionDispatcher,
    MissionExecutionStateReconciler,
    MissionInvariantValidator,
    MissionProgressionMaterializer,
    MissionSafetyReconciler,
    MissionStallDetector,
    MissionWorkCoordinator,
    mission_registry,
)
from app.agent.agent_models import AgentContext, AgentMetadata, AgentPermission, AgentResult
from app.agent.base_agent import BaseAgent
from app.schemas.evidence import EvidenceSource


REAL_RUNTIME_CANARY_COMMAND = (
    "Director, create exactly one brand-new Mission Intelligence canary mission now and execute it through "
    "the hardened internal pipeline. Use only scoped internal mission-state mutation. Do not touch "
    "quarantined legacy missions. Do not send outreach or deploy anything. External side effects are not authorized.\n\n"
    "MISSION OBJECTIVE: Research exactly one test business prospect and produce exactly one valid LeadArtifact.\n\n"
    "AUTHORIZED INTERNAL ACTIONS: Create and execute only the work required for that artifact."
)

# Compatibility alias retained for the earlier canary regression tests.
REALISTIC_CANARY_COMMAND = REAL_RUNTIME_CANARY_COMMAND

LEAD_CANARY_OBJECTIVE = (
    "Research exactly one test business prospect and produce exactly one valid LeadArtifact."
)

LIVE_EVIDENCE_CANARY_OBJECTIVE = (
    "Run exactly one live-evidence canary using genuine LIVE_EXTERNAL business evidence. "
    "Research exactly one real business prospect and produce exactly one valid LeadArtifact. "
    "Use read-only external discovery only. Do not send outreach, mutate CRM, deploy, publish, or write externally."
)

QUALIFIED_LEAD_CANARY_OBJECTIVE = (
    "Run exactly one qualified-lead canary using genuine LIVE_EXTERNAL evidence. "
    "Research exactly one qualified business prospect and produce exactly one valid LeadArtifact. "
    "Require deterministic business entity qualification. Use read-only external discovery only. "
    "Do not send outreach, mutate CRM, deploy, publish, or write externally."
)


class StubLiveDiscoveryProvider:
    name = "stub_live"
    endpoint = "https://discovery.example.test/search"

    def __init__(self, results=None, error=None):
        self.results = results
        self.error = error
        self.calls = []

    async def search(self, query, max_results=10):
        self.calls.append((query, max_results))
        if self.error:
            raise self.error
        return self.results


class MissionIntelligenceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.original_mission_file = mission_registry.mission_file
        with mission_registry.locked(reload=False):
            mission_registry.mission_file = str(Path(self.temp_dir.name) / "missions.json")
            mission_registry.missions = {}
        self.engine = MissionEngine()
        self.coordinator = MissionWorkCoordinator()

    def tearDown(self):
        with mission_registry.locked(reload=False):
            mission_registry.mission_file = self.original_mission_file
            mission_registry.missions = {}
            mission_registry._load_state_unlocked()
        self.temp_dir.cleanup()

    def create_mission(self, request="Create a mission to acquire 10 new restaurant clients"):
        return mission_registry.create_mission(request)

    async def bootstrap_first_work(self):
        mission = self.create_mission()
        await self.engine._bootstrap_mission(mission, "internal_napstertec")
        return mission_registry.get_mission(mission.mission_id)

    def dispatch_and_claim(self, mission_id, worker_id="worker_test"):
        request = self.coordinator.claim_ready_request(mission_id)
        self.assertIsNotNone(request)
        delegation = self.coordinator.create_delegation(mission_id, request["execution_request_id"])
        self.assertIsNotNone(delegation)
        claimed = self.coordinator.claim_pending_delegation(mission_id, worker_id)
        self.assertIsNotNone(claimed)
        claimed_delegation, claim = claimed
        self.assertTrue(self.coordinator.mark_delegation_running(mission_id, delegation["delegation_id"]))
        return request, claimed_delegation, claim

    @staticmethod
    def verified_artifact(delegation, suffix="1", evidence_source=EvidenceSource.LIVE_EXTERNAL.value):
        artifact = {
            "artifact_id": f"artifact_{suffix}",
            "artifact_type": delegation["expected_artifact"],
            "verified": True,
            "verification_method": "deterministic_test",
            "evidence_source": evidence_source,
        }
        if delegation["expected_artifact"] == "LeadArtifact":
            artifact.update({
                "simulation_evidence": evidence_source != EvidenceSource.LIVE_EXTERNAL.value,
                "source_provider": "deterministic_test_provider",
                "source_metadata": {
                    "provider": "deterministic_test_provider",
                    "retrieval_type": "deterministic_test_fixture",
                    "request_succeeded": evidence_source == EvidenceSource.LIVE_EXTERNAL.value,
                    "result_count": 1,
                },
                "source_url": "https://buka-restaurant.example/",
                "source_reference": "https://buka-restaurant.example/",
                "source_type": "OFFICIAL_BUSINESS_SITE",
                "business_name": "Buka Restaurant",
                "business_category": "restaurant",
                "business_location": "Accra Ghana",
                "business_domain": "buka-restaurant.example",
                "entity_qualification": {
                    "status": "VERIFIED_BUSINESS",
                    "source_type": "OFFICIAL_BUSINESS_SITE",
                    "qualified": True,
                    "business_name": "Buka Restaurant",
                    "business_category": "restaurant",
                    "business_location": "Accra Ghana",
                    "business_domain": "buka-restaurant.example",
                    "source_url": "https://buka-restaurant.example/",
                    "qualification_reasons": [
                        "SPECIFIC_BUSINESS_NAME", "DOMAIN_MATCHES_BUSINESS_NAME",
                    ],
                },
                "qualification_reasons": [
                    "SPECIFIC_BUSINESS_NAME", "DOMAIN_MATCHES_BUSINESS_NAME",
                ],
            })
        return artifact

    @staticmethod
    def verified_live_artifact(delegation, suffix="live"):
        artifact = MissionIntelligenceTests.verified_artifact(
            delegation,
            suffix=suffix,
            evidence_source=EvidenceSource.LIVE_EXTERNAL.value,
        )
        artifact.update({
            "simulation_evidence": False,
            "source_provider": "stub_live",
            "source_metadata": {
                "provider": "stub_live",
                "endpoint": "https://discovery.example.test/search",
                "retrieval_type": "read_only_web_search",
                "request_succeeded": True,
                "result_count": 1,
            },
            "source_reference": "https://real-business.example/",
        })
        return artifact

    @staticmethod
    def unqualified_live_artifact(delegation, suffix="unqualified"):
        artifact = MissionIntelligenceTests.verified_live_artifact(delegation, suffix=suffix)
        artifact.update({
            "source_reference": "https://research.example/report/market-123",
            "source_url": "https://research.example/report/market-123",
            "source_type": "REPORT",
            "business_name": "Restaurant Industry Market Report 2026",
            "business_domain": "research.example",
            "entity_qualification": {
                "status": "NON_BUSINESS_SOURCE",
                "source_type": "REPORT",
                "qualified": False,
                "business_name": "Restaurant Industry Market Report 2026",
                "business_domain": "research.example",
                "source_url": "https://research.example/report/market-123",
                "qualification_reasons": ["REPORT_OR_RESEARCH_PAGE"],
            },
            "qualification_reasons": ["REPORT_OR_RESEARCH_PAGE"],
        })
        return artifact

    @staticmethod
    def live_delegation_fixture():
        return {
            "delegation_id": "del_live",
            "execution_request_id": "mer_live",
            "materialization_id": "mat_live",
            "decision_id": "dec_live",
            "target_agent": "lead_intelligence",
            "objective": "Research exactly one live business prospect",
            "milestone_id": "m1",
            "plan_version": "v1",
            "expected_artifact": "LeadArtifact",
            "target_count": 1,
            "worker_claim_id": "wcl_live",
            "verification_mode": "LIVE_EVIDENCE_CANARY",
            "simulation_mode": False,
        }

    @staticmethod
    def complete_active_plan(mission_id):
        with mission_registry.locked():
            mission = mission_registry.missions[mission_id]
            for milestone in mission.milestones:
                if milestone.get("plan_version", "v1") == mission.plan_version:
                    milestone["status"] = "Completed"
                    milestone["progress"] = 100
            mission_registry.save_mission(mission)

    async def test_canary_objective_preservation_and_typed_success_criterion(self):
        flattened_runtime_command = (
            "Director, create and execute one controlled canary mission.  "
            f"MISSION OBJECTIVE: {LEAD_CANARY_OBJECTIVE}  "
            "AUTHORIZED INTERNAL ACTIONS: Create only the required internal work.  "
            "NOT AUTHORIZED: Outreach or deployment."
        )
        mission = self.create_mission(flattened_runtime_command)
        await self.engine._bootstrap_mission(mission, "internal_napstertec")
        current = mission_registry.get_mission(mission.mission_id)

        self.assertEqual(LEAD_CANARY_OBJECTIVE, current.objective)
        self.assertEqual("Lead Research Canary", current.title)
        self.assertEqual("ARTIFACT_PRODUCTION", current.mission_type)
        self.assertEqual("verified_artifacts", current.success_criteria["criterion"])
        self.assertEqual("LeadArtifact", current.success_criteria["artifact_type"])
        self.assertEqual(1, current.success_criteria["required"])
        self.assertEqual(1, len(current.milestones))
        self.assertIn("Research 1 test business prospect", current.next_eligible_action)
        self.assertNotIn("restaurant", current.next_eligible_action.lower())
        self.assertNotIn("10", current.success_criteria_progress)

    async def test_real_acquisition_objective_preserves_ten_client_behavior(self):
        mission = self.create_mission("Acquire 10 new restaurant clients.")
        await self.engine._bootstrap_mission(mission, "internal_napstertec")
        current = mission_registry.get_mission(mission.mission_id)

        self.assertEqual("CLIENT_ACQUISITION", current.mission_type)
        self.assertEqual("Strategic Business Acquisition", current.title)
        self.assertEqual({
            "criterion": "verified_won_clients",
            "required": 10,
            "verification_mode": "PRODUCTION_BUSINESS_SUCCESS",
        }, current.success_criteria)
        self.assertEqual("0 / 10 verified won clients", current.success_criteria_progress)
        self.assertEqual("Discover qualified restaurant prospects", current.next_eligible_action)
        self.assertEqual(4, len(current.milestones))

    async def test_one_artifact_mission_completes_and_stops(self):
        mission = self.create_mission(LEAD_CANARY_OBJECTIVE)
        await self.engine._bootstrap_mission(mission, "internal_napstertec")
        request, delegation, _ = self.dispatch_and_claim(mission.mission_id)

        completed = await self.engine.process_delegation_completion(
            mission.mission_id,
            delegation["delegation_id"],
            self.verified_artifact(
                delegation,
                evidence_source=EvidenceSource.INTERNAL_FIXTURE.value,
            ),
        )

        self.assertTrue(completed)
        current = mission_registry.get_mission(mission.mission_id)
        self.assertEqual("COMPLETED", current.status)
        self.assertTrue(current.mission_objective_achieved)
        self.assertEqual("1 / 1 verified LeadArtifact", current.success_criteria_progress)
        self.assertEqual(100, current.progress)
        self.assertEqual("STOPPED", current.auto_continue_status)
        self.assertEqual("None", current.next_eligible_action)
        self.assertEqual("SUCCESS_CRITERIA_VERIFIED", current.terminal_reason)
        self.assertEqual(1, len(current.artifact_lineage))
        self.assertEqual(1, len(current.execution_requests))
        self.assertEqual("COMPLETED", next(r for r in current.execution_requests if r["execution_request_id"] == request["execution_request_id"])["status"])
        self.assertFalse(any(d.get("target_agent") == "communication_intelligence" for d in current.delegation_history))

    async def test_mock_discovery_and_resulting_artifact_are_explicitly_tagged(self):
        from app.agent.definitions.lead_agent import LeadIntelligenceAgent
        from app.tools.plugins.business_discovery import BusinessDiscoveryTool

        with patch.dict(os.environ, {"APOLLO_API_TOKEN": "", "APIFY_API_TOKEN": ""}):
            discovery = await BusinessDiscoveryTool().execute(
                query="test business", location="Test Region", max_results=1
            )
        self.assertEqual(EvidenceSource.MOCK_FALLBACK.value, discovery["evidence_source"])
        self.assertTrue(discovery["simulation_evidence"])
        self.assertEqual(1, len(discovery["results"]))

        invoked = []

        async def fake_invoke_tool(tool_name, parameters, context):
            invoked.append((tool_name, parameters))
            if tool_name == "business_discovery":
                return {"output": discovery}
            return {"output": {
                "success": True, "transaction_committed": True, "created": 1,
                "updated": 0, "duplicates": 0, "failed": 0, "qualified": 1,
                "needs_review": 0, "unqualified": 0,
            }}

        lead_agent = LeadIntelligenceAgent()
        lead_agent.invoke_tool = fake_invoke_tool
        lead_result = await lead_agent.execute(AgentContext(
            task="Research one test business prospect",
            session_id="mock_lead_provenance",
            planner_output={
                "query": "test business", "target_count": 1,
                "mission_id": "mis_test", "execution_request_id": "mer_test",
                "delegation_id": "del_test", "worker_claim_id": "wcl_test",
            },
        ))
        lead_artifact = lead_result.artifacts[0]
        self.assertEqual(1, invoked[0][1]["max_results"])
        self.assertEqual(EvidenceSource.MOCK_FALLBACK.value, lead_artifact["evidence_source"])
        self.assertTrue(lead_artifact["simulation_evidence"])
        self.assertEqual("mis_test", lead_artifact["provenance"]["mission_id"])
        self.assertEqual("mer_test", lead_artifact["provenance"]["execution_request_id"])
        self.assertEqual("del_test", lead_artifact["provenance"]["delegation_id"])
        self.assertEqual("wcl_test", lead_artifact["provenance"]["worker_claim_id"])
        self.assertEqual("lead_intelligence", lead_artifact["provenance"]["specialist"])

        mission = self.create_mission(LEAD_CANARY_OBJECTIVE)
        await self.engine._bootstrap_mission(mission, "internal_napstertec")
        _, delegation, _ = self.dispatch_and_claim(mission.mission_id)
        evidence = self.verified_artifact(
            delegation,
            evidence_source=discovery["evidence_source"],
        )
        evidence["simulation_evidence"] = discovery["simulation_evidence"]
        await self.engine.process_delegation_completion(
            mission.mission_id, delegation["delegation_id"], evidence
        )

        lineage = mission_registry.get_mission(mission.mission_id).artifact_lineage[0]
        self.assertEqual(EvidenceSource.MOCK_FALLBACK.value, lineage["evidence_source"])
        self.assertTrue(lineage["simulation_evidence"])
        self.assertEqual(mission.mission_id, lineage["mission_id"])
        self.assertEqual("lead_intelligence", lineage["specialist"])

    async def test_mock_evidence_cannot_satisfy_production_artifact_success(self):
        objective = "Research exactly one business prospect and produce exactly one valid LeadArtifact."
        mission = self.create_mission(objective)
        await self.engine._bootstrap_mission(mission, "internal_napstertec")
        _, delegation, _ = self.dispatch_and_claim(mission.mission_id)

        accepted_structurally = await self.engine.process_delegation_completion(
            mission.mission_id,
            delegation["delegation_id"],
            self.verified_artifact(
                delegation,
                evidence_source=EvidenceSource.MOCK_FALLBACK.value,
            ),
        )

        self.assertFalse(accepted_structurally)
        current = mission_registry.get_mission(mission.mission_id)
        self.assertEqual("PRODUCTION_BUSINESS_SUCCESS", current.verification_mode)
        self.assertEqual("0 / 1 verified LeadArtifact", current.success_criteria_progress)
        self.assertFalse(current.mission_objective_achieved)
        self.assertNotEqual("COMPLETED", current.status)
        self.assertEqual([], current.artifact_lineage)
        self.assertFalse(any(d.get("target_agent") == "communication_intelligence" for d in current.delegation_history))

        acquisition = self.create_mission("Acquire 1 new restaurant client.")
        recorded = await self.engine.record_success_evidence(acquisition.mission_id, {
            "evidence_id": "mock_won_client",
            "event_type": "DEAL_WON",
            "client_id": "mock_client",
            "verified": True,
            "evidence_source": EvidenceSource.MOCK_FALLBACK.value,
        })
        self.assertTrue(recorded)
        acquisition = mission_registry.get_mission(acquisition.mission_id)
        self.assertEqual("0 / 1 verified won clients", acquisition.success_criteria_progress)
        self.assertFalse(acquisition.mission_objective_achieved)
        self.assertNotEqual("COMPLETED", acquisition.status)

    async def test_live_discovery_success_is_live_external(self):
        from app.tools.plugins.business_discovery import BusinessDiscoveryTool

        provider = StubLiveDiscoveryProvider(results=[{
            "title": "Real Business Ltd",
            "url": "https://real-business.example/",
            "snippet": "Real business evidence from the configured source.",
            "source_provider": "stub_live",
        }])
        discovery = await BusinessDiscoveryTool(provider, "stub_live").execute(
            query="business services",
            location="Test Region",
            max_results=1,
            require_live_evidence=True,
        )

        self.assertEqual(EvidenceSource.LIVE_EXTERNAL.value, discovery["evidence_source"])
        self.assertFalse(discovery["simulation_evidence"])
        self.assertEqual(1, len(discovery["results"]))
        self.assertTrue(discovery["source_metadata"]["request_succeeded"])
        self.assertEqual(1, len(provider.calls))

    async def test_live_provider_unavailable_produces_no_production_success(self):
        from app.tools.plugins.business_discovery import BusinessDiscoveryTool

        discovery = await BusinessDiscoveryTool(
            StubLiveDiscoveryProvider(error=ConnectionError("provider unavailable")),
            "stub_live",
        ).execute(
            query="business services",
            location="Test Region",
            max_results=1,
            require_live_evidence=True,
        )

        self.assertEqual(EvidenceSource.UNKNOWN.value, discovery["evidence_source"])
        self.assertEqual("LIVE_EVIDENCE_UNAVAILABLE", discovery["error_code"])
        self.assertEqual([], discovery["results"])
        self.assertFalse(discovery["source_metadata"]["request_succeeded"])

    async def test_live_timeout_cannot_fall_back_to_mock_success(self):
        from app.tools.plugins.business_discovery import BusinessDiscoveryTool

        discovery = await BusinessDiscoveryTool(
            StubLiveDiscoveryProvider(error=TimeoutError("timed out")),
            "stub_live",
        ).execute(
            query="business services",
            location="Test Region",
            max_results=1,
            require_live_evidence=True,
        )

        self.assertEqual(EvidenceSource.UNKNOWN.value, discovery["evidence_source"])
        self.assertNotEqual(EvidenceSource.MOCK_FALLBACK.value, discovery["evidence_source"])
        self.assertEqual("LIVE_EVIDENCE_UNAVAILABLE", discovery["error_code"])

    async def test_live_empty_and_malformed_responses_are_not_live_external(self):
        from app.tools.plugins.business_discovery import BusinessDiscoveryTool

        for results in ([], [{"title": "Missing URL"}], [{"title": "Bad URL", "url": "not-a-url"}]):
            with self.subTest(results=results):
                discovery = await BusinessDiscoveryTool(
                    StubLiveDiscoveryProvider(results=results),
                    "stub_live",
                ).execute(
                    query="business services",
                    location="Test Region",
                    max_results=1,
                    require_live_evidence=True,
                )
                self.assertEqual(EvidenceSource.UNKNOWN.value, discovery["evidence_source"])
                self.assertEqual("LIVE_EVIDENCE_UNAVAILABLE", discovery["error_code"])
                self.assertEqual([], discovery["results"])

    async def test_mock_fallback_remains_mock_fallback(self):
        from app.tools.plugins.business_discovery import BusinessDiscoveryTool

        discovery = await BusinessDiscoveryTool().execute(
            query="test business",
            location="Test Region",
            max_results=1,
            require_live_evidence=False,
        )
        self.assertEqual(EvidenceSource.MOCK_FALLBACK.value, discovery["evidence_source"])
        self.assertTrue(discovery["simulation_evidence"])
        self.assertFalse(discovery["source_metadata"]["request_succeeded"])

    async def test_live_external_sets_simulation_false_and_skips_lead_upsert(self):
        from app.agent.definitions.lead_agent import LeadIntelligenceAgent
        from app.tools.plugins.business_discovery import BusinessDiscoveryTool

        discovery = await BusinessDiscoveryTool(
            StubLiveDiscoveryProvider(results=[{
                "title": "Real Business Ltd",
                "url": "https://real-business.example/",
                "snippet": "Live evidence",
                "source_provider": "stub_live",
            }]),
            "stub_live",
        ).execute(
            query="business services",
            location="Test Region",
            max_results=1,
            require_live_evidence=True,
        )
        invoked = []

        async def fake_invoke_tool(tool_name, parameters, context):
            invoked.append(tool_name)
            if tool_name != "business_discovery":
                raise AssertionError(f"Unexpected mutating tool call: {tool_name}")
            return {"output": discovery}

        agent = LeadIntelligenceAgent()
        agent.invoke_tool = fake_invoke_tool
        result = await agent.execute(AgentContext(
            task="Research exactly one live business prospect",
            session_id="live_read_only_agent",
            planner_output={
                "query": "business services",
                "target_count": 1,
                "verification_mode": "LIVE_EVIDENCE_CANARY",
                "require_live_evidence": True,
                "mission_id": "mis_live",
                "plan_version": "v1",
                "milestone_id": "m1",
                "decision_id": "dec_live",
                "materialization_id": "mat_live",
                "execution_request_id": "mer_live",
                "delegation_id": "del_live",
                "worker_claim_id": "wcl_live",
            },
        ))

        self.assertTrue(result.success)
        self.assertEqual(["business_discovery"], invoked)
        self.assertEqual(EvidenceSource.LIVE_EXTERNAL.value, result.artifacts[0]["evidence_source"])
        self.assertFalse(result.artifacts[0]["simulation_evidence"])
        self.assertTrue(result.artifacts[0]["metrics"]["read_only_validation"])
        self.assertFalse(result.artifacts[0]["metrics"]["transaction_committed"])

    async def test_complete_live_provenance_is_preserved_without_secrets(self):
        mission = self.create_mission(LIVE_EVIDENCE_CANARY_OBJECTIVE)
        await self.engine._bootstrap_mission(mission, "internal_napstertec")
        _, delegation, claim = self.dispatch_and_claim(mission.mission_id)

        completed = await self.engine.process_delegation_completion(
            mission.mission_id,
            delegation["delegation_id"],
            self.verified_live_artifact(delegation),
        )
        self.assertTrue(completed)
        lineage = mission_registry.get_mission(mission.mission_id).artifact_lineage[0]
        required = {
            "mission_id", "plan_version", "milestone_id", "decision_id",
            "materialization_id", "execution_request_id", "delegation_id",
            "worker_claim_id", "specialist", "artifact_type", "evidence_source",
            "simulation_evidence", "created_at",
        }
        self.assertTrue(required.issubset(lineage))
        self.assertEqual(claim["worker_claim_id"], lineage["worker_claim_id"])
        self.assertEqual("stub_live", lineage["source_provider"])
        serialized = json.dumps(lineage).lower()
        for forbidden in ("api_key", "authorization", "access_token", "secret"):
            self.assertNotIn(forbidden, serialized)

    async def test_exactly_one_live_artifact_completes_the_canary(self):
        mission = self.create_mission(LIVE_EVIDENCE_CANARY_OBJECTIVE)
        await self.engine._bootstrap_mission(mission, "internal_napstertec")
        request, delegation, _ = self.dispatch_and_claim(mission.mission_id)
        self.assertEqual("LIVE_EVIDENCE_CANARY", mission.verification_mode)
        self.assertFalse(mission.simulation_mode)

        self.assertTrue(await self.engine.process_delegation_completion(
            mission.mission_id,
            delegation["delegation_id"],
            self.verified_live_artifact(delegation),
        ))
        current = mission_registry.get_mission(mission.mission_id)
        self.assertEqual("COMPLETED", current.status)
        self.assertEqual(100, current.progress)
        self.assertEqual("1 / 1 verified LeadArtifact", current.success_criteria_progress)
        self.assertEqual("STOPPED", current.auto_continue_status)
        self.assertEqual("None", current.next_eligible_action)
        self.assertEqual("SUCCESS_CRITERIA_VERIFIED", current.terminal_reason)
        self.assertEqual(1, len(current.execution_requests))
        self.assertEqual(1, len(current.delegation_history))
        self.assertEqual(1, len(current.worker_claims))
        self.assertEqual(1, len(current.artifact_lineage))
        self.assertEqual("COMPLETED", next(
            item for item in current.execution_requests
            if item["execution_request_id"] == request["execution_request_id"]
        )["status"])

    async def test_live_canary_does_not_execute_again_after_success(self):
        from app.engine.autonomous_worker import autonomous_worker

        mission = self.create_mission(LIVE_EVIDENCE_CANARY_OBJECTIVE)
        await self.engine._bootstrap_mission(mission, "internal_napstertec")
        _, delegation, _ = self.dispatch_and_claim(mission.mission_id)
        await self.engine.process_delegation_completion(
            mission.mission_id,
            delegation["delegation_id"],
            self.verified_live_artifact(delegation),
        )
        before = mission_registry.get_mission(mission.mission_id)
        counts = (
            len(before.execution_requests), len(before.delegation_history),
            len(before.worker_claims), len(before.artifact_lineage),
        )

        await self.engine._bootstrap_mission(before, "internal_napstertec")
        self.assertEqual(0, await MissionExecutionDispatcher().process_ready_requests(mission.mission_id))
        self.assertFalse(await autonomous_worker.process_mission_once(mission.mission_id))
        stable = mission_registry.get_mission(mission.mission_id)
        self.assertEqual(counts, (
            len(stable.execution_requests), len(stable.delegation_history),
            len(stable.worker_claims), len(stable.artifact_lineage),
        ))
        self.assertEqual(0, stable.replan_count)

    async def test_non_live_evidence_blocks_live_canary_without_retry(self):
        for source in (
            EvidenceSource.MOCK_FALLBACK.value,
            EvidenceSource.INTERNAL_FIXTURE.value,
            EvidenceSource.UNKNOWN.value,
        ):
            with self.subTest(source=source):
                mission = self.create_mission(LIVE_EVIDENCE_CANARY_OBJECTIVE)
                await self.engine._bootstrap_mission(mission, "internal_napstertec")
                _, delegation, _ = self.dispatch_and_claim(mission.mission_id)
                evidence = self.verified_artifact(delegation, suffix=source, evidence_source=source)
                evidence["simulation_evidence"] = source != EvidenceSource.UNKNOWN.value
                accepted = await self.engine.process_delegation_completion(
                    mission.mission_id, delegation["delegation_id"], evidence
                )
                self.assertFalse(accepted)
                current = mission_registry.get_mission(mission.mission_id)
                self.assertEqual("WAITING_DIRECTOR", current.status)
                self.assertEqual("LIVE_EVIDENCE_UNAVAILABLE", current.terminal_reason)
                self.assertEqual("0 / 1 verified LeadArtifact", current.success_criteria_progress)
                self.assertEqual(1, len(current.execution_requests))
                self.assertNotEqual("COMPLETED", current.status)

    def test_live_authority_prohibits_outreach(self):
        from app.engine.autonomous_worker import autonomous_worker

        context = autonomous_worker._build_specialist_context("mis_live", self.live_delegation_fixture())
        self.assertNotIn(AgentPermission.OUTREACH, context.granted_permissions)
        self.assertNotIn(AgentPermission.EMAIL, context.granted_permissions)
        self.assertFalse(context.runtime_metadata["outreach_allowed"])
        self.assertIn("outreach", context.runtime_metadata["forbidden_tool_permissions"])

    async def test_live_authority_blocks_crm_mutation_tool(self):
        from app.agent.base_agent import ToolExecutionDenied
        from app.agent.definitions.lead_agent import LeadIntelligenceAgent
        from app.api import endpoints
        from app.engine.autonomous_worker import autonomous_worker

        context = autonomous_worker._build_specialist_context("mis_live", self.live_delegation_fixture())
        agent = LeadIntelligenceAgent(tool_manager=endpoints.tool_manager)
        with self.assertRaises(ToolExecutionDenied):
            await agent.invoke_tool(
                "lead_upsert",
                {"raw_leads": [], "provider_mode": "live"},
                context,
            )

    def test_live_authority_prohibits_deployment_and_publishing(self):
        from app.engine.autonomous_worker import autonomous_worker

        context = autonomous_worker._build_specialist_context("mis_live", self.live_delegation_fixture())
        self.assertNotIn(AgentPermission.DEPLOYMENT, context.granted_permissions)
        self.assertNotIn(AgentPermission.PUBLISHING, context.granted_permissions)
        self.assertIn("deployment", context.runtime_metadata["forbidden_tool_permissions"])
        self.assertIn("publishing", context.runtime_metadata["forbidden_tool_permissions"])

    def test_live_authority_has_read_discovery_but_no_external_write(self):
        from app.engine.autonomous_worker import autonomous_worker

        context = autonomous_worker._build_specialist_context("mis_live", self.live_delegation_fixture())
        self.assertIn(AgentPermission.READ_EXTERNAL_DISCOVERY, context.granted_permissions)
        self.assertNotIn(AgentPermission.EXTERNAL_API, context.granted_permissions)
        self.assertNotIn(AgentPermission.WRITE_EXTERNAL, context.granted_permissions)
        self.assertFalse(context.runtime_metadata["external_write_allowed"])

    async def test_live_readiness_work_preserves_legacy_quarantine(self):
        legacy = self.create_mission("Acquire 10 restaurant clients for legacy live readiness")
        with mission_registry.locked():
            current = mission_registry.missions[legacy.mission_id]
            current.status = "WAITING_DIRECTOR"
            current.escalation_reason = "LEGACY_QUARANTINE"
            current.terminal_reason = "LEGACY_QUARANTINE"
            current.auto_continue_status = "STOPPED"
            mission_registry.save_mission(current)
        before = mission_registry.get_mission(legacy.mission_id).model_dump(mode="json")

        live = self.create_mission(LIVE_EVIDENCE_CANARY_OBJECTIVE)
        await self.engine._bootstrap_mission(live, "internal_napstertec")

        self.assertEqual(before, mission_registry.get_mission(legacy.mission_id).model_dump(mode="json"))

    async def test_structural_canary_accepts_mock_artifact_without_business_claims(self):
        from app.engine.autonomous_worker import autonomous_worker

        mission = self.create_mission(LEAD_CANARY_OBJECTIVE)
        await self.engine._bootstrap_mission(mission, "internal_napstertec")
        request = self.coordinator.claim_ready_request(mission.mission_id)
        delegation = self.coordinator.create_delegation(mission.mission_id, request["execution_request_id"])

        async def mock_specialist(_mission_id, assigned):
            return AgentResult(
                success=True,
                agent_name=assigned["target_agent"],
                session_id="mock_structural_canary",
                artifacts=[{
                    "artifact_id": "mock_lead_artifact",
                    "artifact_type": "LeadArtifact",
                    "verified": True,
                    "evidence_source": EvidenceSource.MOCK_FALLBACK.value,
                    "simulation_evidence": True,
                }],
            )

        previous = autonomous_worker.specialist_executor
        autonomous_worker.specialist_executor = mock_specialist
        try:
            completed = await autonomous_worker.process_mission_once(mission.mission_id)
        finally:
            autonomous_worker.specialist_executor = previous

        self.assertTrue(completed)
        report = await self.engine.process_mission_request(
            "MISSION STATUS MODE", mission.mission_id, "structural_canary_report"
        )
        self.assertEqual("COMPLETED", report.status)
        self.assertTrue(report.canary_pipeline_verified)
        self.assertEqual(EvidenceSource.MOCK_FALLBACK.value, report.evidence_source)
        self.assertTrue(report.simulation_mode)
        self.assertFalse(report.real_world_business_evidence_verified)
        self.assertEqual("NONE", report.external_side_effects)
        self.assertEqual(delegation["delegation_id"], report.artifact_lineage[0]["delegation_id"])
        self.assertEqual("lead_intelligence", report.artifact_lineage[0]["specialist"])

    async def test_fresh_artifact_mission_dispatches_exactly_one_delegation(self):
        mission = self.create_mission(LEAD_CANARY_OBJECTIVE)
        await self.engine._bootstrap_mission(mission, "internal_napstertec")

        dispatched = await MissionExecutionDispatcher().process_ready_requests(mission.mission_id)
        current = mission_registry.get_mission(mission.mission_id)

        self.assertEqual(1, dispatched)
        self.assertEqual(1, len(current.execution_requests))
        self.assertEqual("DISPATCHED", current.execution_requests[0]["status"])
        self.assertEqual(1, len(current.active_delegations))
        self.assertEqual("lead_intelligence", current.active_delegations[0]["target_agent"])
        self.assertEqual(
            current.execution_requests[0]["execution_request_id"],
            current.active_delegations[0]["execution_request_id"],
        )
        self.assertEqual(0, await MissionExecutionDispatcher().process_ready_requests(mission.mission_id))
        self.assertEqual(1, len(mission_registry.get_mission(mission.mission_id).active_delegations))

    async def test_lead_research_materialization_resolves_registered_specialist(self):
        from app.api import endpoints

        mission = self.create_mission(LEAD_CANARY_OBJECTIVE)
        await self.engine._bootstrap_mission(mission, "internal_napstertec")
        current = mission_registry.get_mission(mission.mission_id)
        request = current.execution_requests[0]
        specialist = endpoints.agent_registry.get_agent(request["target_intelligence"])

        self.assertEqual("lead_discovery", request["capability"])
        self.assertEqual("lead_intelligence", request["target_intelligence"])
        self.assertIsNotNone(specialist)
        self.assertEqual("lead_intelligence", specialist.metadata.name)

    async def test_structural_canary_runs_registered_specialist_chain_to_completion(self):
        from app.api import endpoints
        from app.engine.autonomous_worker import autonomous_worker
        from app.tools.plugins.business_discovery import BusinessDiscoveryTool

        specialist = endpoints.agent_registry.get_agent("lead_intelligence")
        self.assertIsNotNone(specialist)
        with patch.dict(os.environ, {"APOLLO_API_TOKEN": "", "APIFY_API_TOKEN": ""}):
            discovery = await BusinessDiscoveryTool().execute(
                query="test business", location="Test Region", max_results=1
            )

        async def fake_invoke_tool(tool_name, parameters, context):
            if tool_name == "business_discovery":
                return {"output": discovery}
            return {"output": {
                "success": True,
                "transaction_committed": True,
                "created": 1,
                "updated": 0,
                "duplicates": 0,
                "failed": 0,
                "qualified": 1,
                "needs_review": 0,
                "unqualified": 0,
            }}

        with patch.object(specialist, "invoke_tool", new=fake_invoke_tool):
            report = await self.engine.process_mission_request(
                "MISSION CREATION MODE",
                REAL_RUNTIME_CANARY_COMMAND,
                "registered_specialist_chain",
            )

        self.assertEqual("COMPLETED", report.status)
        self.assertEqual(100, report.overall_progress)
        self.assertEqual(1, report.verified_count)
        self.assertEqual("STOPPED", report.auto_continue_status)
        self.assertEqual(1, len(report.execution_requests))
        self.assertEqual(1, len(report.delegation_history))
        self.assertEqual(1, len(report.worker_claims))
        self.assertEqual(1, len(report.artifact_lineage))
        self.assertEqual([], report.active_delegations)
        request = report.execution_requests[0]
        delegation = report.delegation_history[0]
        claim = report.worker_claims[0]
        lineage = report.artifact_lineage[0]
        self.assertEqual("COMPLETED", request["status"])
        self.assertEqual("Completed", delegation["status"])
        self.assertEqual("COMPLETED", claim["status"])
        self.assertEqual("LeadArtifact", lineage["artifact_type"])
        self.assertEqual(EvidenceSource.MOCK_FALLBACK.value, lineage["evidence_source"])
        self.assertTrue(lineage["simulation_evidence"])
        self.assertEqual(report.mission_id, lineage["mission_id"])
        self.assertEqual(request["execution_request_id"], lineage["execution_request_id"])
        self.assertEqual(delegation["delegation_id"], lineage["delegation_id"])
        self.assertEqual(claim["worker_claim_id"], lineage["worker_claim_id"])
        self.assertEqual("lead_intelligence", lineage["specialist"])
        self.assertEqual("NONE", report.external_side_effects)

        counts = (
            len(report.execution_requests),
            len(report.delegation_history),
            len(report.worker_claims),
            len(report.artifact_lineage),
        )
        completed = mission_registry.get_mission(report.mission_id)
        await self.engine._bootstrap_mission(completed, "internal_napstertec")
        self.assertEqual(0, await MissionExecutionDispatcher().process_ready_requests(report.mission_id))
        self.assertFalse(await autonomous_worker.process_mission_once(report.mission_id))
        stable = mission_registry.get_mission(report.mission_id)
        self.assertEqual(counts, (
            len(stable.execution_requests),
            len(stable.delegation_history),
            len(stable.worker_claims),
            len(stable.artifact_lineage),
        ))

    async def test_auto_continue_stops_when_no_runnable_work_exists(self):
        mission = self.create_mission(LEAD_CANARY_OBJECTIVE)
        with mission_registry.locked():
            current = mission_registry.missions[mission.mission_id]
            current.execution_requests = []
            current.active_delegations = []
            current.progression_state = "RUNNING"
            current.execution_state = "RUNNING"
            current.auto_continue_status = "RUNNING"
            changed = MissionExecutionStateReconciler.ensure_truthful(current)
            mission_registry.save_mission(current)

        self.assertTrue(changed)
        current = mission_registry.get_mission(mission.mission_id)
        self.assertEqual("BLOCKED", current.progression_state)
        self.assertEqual("BLOCKED", current.execution_state)
        self.assertEqual("BLOCKED", current.dispatch_state)
        self.assertEqual("STOPPED", current.auto_continue_status)
        self.assertEqual("AUTO_CONTINUE_NO_RUNNABLE_WORK", current.last_error)

    async def test_dispatch_failure_sets_explicit_blocked_state(self):
        mission = self.create_mission(LEAD_CANARY_OBJECTIVE)
        await self.engine._bootstrap_mission(mission, "internal_napstertec")

        with patch.object(MissionWorkCoordinator, "create_delegation", return_value=None):
            dispatched = await MissionExecutionDispatcher().process_ready_requests(mission.mission_id)

        self.assertEqual(0, dispatched)
        current = mission_registry.get_mission(mission.mission_id)
        self.assertEqual("FAILED", current.execution_requests[0]["status"])
        self.assertIn("EXECUTION_REQUEST_UNDISPATCHED", current.execution_requests[0]["error"])
        self.assertEqual("BLOCKED", current.progression_state)
        self.assertEqual("BLOCKED", current.execution_state)
        self.assertEqual("BLOCKED", current.dispatch_state)
        self.assertEqual("STOPPED", current.auto_continue_status)
        self.assertEqual("EXECUTION_REQUEST_UNDISPATCHED", current.last_error)

    async def test_completed_canary_has_no_extra_progression_or_side_effects(self):
        from app.engine.autonomous_worker import autonomous_worker

        mission = self.create_mission(LEAD_CANARY_OBJECTIVE)
        await self.engine._bootstrap_mission(mission, "internal_napstertec")
        _, delegation, _ = self.dispatch_and_claim(mission.mission_id)
        await self.engine.process_delegation_completion(
            mission.mission_id,
            delegation["delegation_id"],
            self.verified_artifact(
                delegation,
                evidence_source=EvidenceSource.MOCK_FALLBACK.value,
            ),
        )
        before = Path(mission_registry.mission_file).read_bytes()
        completed = mission_registry.get_mission(mission.mission_id)
        counts = (
            len(completed.progression_decisions), len(completed.progression_materializations),
            len(completed.execution_requests), len(completed.delegation_history),
            len(completed.worker_claims), len(completed.artifact_lineage),
        )

        await self.engine._bootstrap_mission(completed, "internal_napstertec")
        await self.engine._bootstrap_mission(completed, "internal_napstertec")
        self.assertFalse(await autonomous_worker.process_mission_once(mission.mission_id))

        stable = mission_registry.get_mission(mission.mission_id)
        self.assertEqual(before, Path(mission_registry.mission_file).read_bytes())
        self.assertEqual(counts, (
            len(stable.progression_decisions), len(stable.progression_materializations),
            len(stable.execution_requests), len(stable.delegation_history),
            len(stable.worker_claims), len(stable.artifact_lineage),
        ))
        self.assertEqual(0, stable.replan_count)
        self.assertEqual([], stable.external_operations)
        self.assertFalse(any(d.get("target_agent") == "communication_intelligence" for d in stable.delegation_history))

    async def test_two_legacy_quarantined_missions_remain_untouched(self):
        legacy_ids = []
        for suffix in ("alpha", "beta"):
            legacy = self.create_mission(f"Acquire 10 restaurant clients for legacy {suffix}")
            with mission_registry.locked():
                quarantined = mission_registry.missions[legacy.mission_id]
                quarantined.status = "WAITING_DIRECTOR"
                quarantined.escalation_reason = "LEGACY_QUARANTINE"
                quarantined.terminal_reason = "LEGACY_QUARANTINE"
                quarantined.auto_continue_status = "STOPPED"
                mission_registry.save_mission(quarantined)
            legacy_ids.append(legacy.mission_id)
        before = {
            mission_id: mission_registry.get_mission(mission_id).model_dump(mode="json")
            for mission_id in legacy_ids
        }

        fresh = self.create_mission(LEAD_CANARY_OBJECTIVE)
        await self.engine._bootstrap_mission(fresh, "internal_napstertec")

        for mission_id in legacy_ids:
            self.assertEqual(before[mission_id], mission_registry.get_mission(mission_id).model_dump(mode="json"))
            self.assertEqual("WAITING_DIRECTOR", mission_registry.get_mission(mission_id).status)

    async def test_normal_progression_closes_claim_and_builds_lineage(self):
        mission = await self.bootstrap_first_work()
        request, delegation, claim = self.dispatch_and_claim(mission.mission_id)

        completed = await self.engine.process_delegation_completion(
            mission.mission_id, delegation["delegation_id"], self.verified_artifact(delegation)
        )

        self.assertTrue(completed)
        current = mission_registry.get_mission(mission.mission_id)
        milestone = next(m for m in current.milestones if m["milestone_id"] == "m1")
        self.assertEqual("Completed", milestone["status"])
        self.assertEqual("COMPLETED", next(c for c in current.worker_claims if c["worker_claim_id"] == claim["worker_claim_id"])["status"])
        self.assertEqual("Completed", current.delegation_history[0]["status"])
        self.assertEqual([], current.active_delegations)
        self.assertEqual(request["execution_request_id"], current.artifact_lineage[0]["execution_request_id"])
        self.assertEqual(claim["worker_claim_id"], current.artifact_lineage[0]["worker_claim_id"])
        self.assertTrue(any(r["milestone_id"] == "m2" and r["status"] == "READY" for r in current.execution_requests))
        self.assertLessEqual(current.progress, 100)

    async def test_completed_plan_with_unmet_mission_replans_instead_of_completing(self):
        mission = self.create_mission()
        self.complete_active_plan(mission.mission_id)

        await self.engine._bootstrap_mission(mission_registry.get_mission(mission.mission_id), "internal_napstertec")

        current = mission_registry.get_mission(mission.mission_id)
        self.assertEqual("ACTIVE", current.status)
        self.assertFalse(current.mission_objective_achieved)
        self.assertEqual("v2", current.plan_version)
        self.assertEqual(1, current.replan_count)
        self.assertEqual("COMPLETED", current.historical_plans[0]["status"])
        self.assertEqual("APPROVAL_REQUIRED", current.progression_materializations[-1]["status"])
        self.assertEqual("WAITING_APPROVAL", current.auto_continue_status)

    async def test_success_criteria_require_verified_unique_outcomes(self):
        mission = self.create_mission()
        with mission_registry.locked():
            current = mission_registry.missions[mission.mission_id]
            current.success_evidence = [
                {"evidence_id": f"e{i}", "event_type": "DEAL_WON", "client_id": f"client_{i}", "verified": True, "evidence_source": EvidenceSource.LIVE_EXTERNAL.value}
                for i in range(9)
            ] + [{"evidence_id": "unverified", "event_type": "DEAL_WON", "client_id": "client_10", "verified": False, "evidence_source": EvidenceSource.LIVE_EXTERNAL.value}]
            for milestone in current.milestones:
                milestone["status"] = "Completed"
                milestone["progress"] = 100
            MissionCompletionGuard().refresh_progress(current)
            mission_registry.save_mission(current)

        await self.engine._bootstrap_mission(mission_registry.get_mission(mission.mission_id), "internal_napstertec")
        current = mission_registry.get_mission(mission.mission_id)
        self.assertFalse(current.mission_objective_achieved)
        self.assertEqual("9 / 10 verified won clients", current.success_criteria_progress)

        self.complete_active_plan(mission.mission_id)
        recorded = await self.engine.record_success_evidence(mission.mission_id, {
            "evidence_id": "verified_10", "event_type": "DEAL_WON", "client_id": "client_10", "verified": True,
            "evidence_source": EvidenceSource.LIVE_EXTERNAL.value,
        })
        self.assertTrue(recorded)
        completed = mission_registry.get_mission(mission.mission_id)
        self.assertEqual("COMPLETED", completed.status)
        self.assertTrue(completed.mission_objective_achieved)
        self.assertEqual(100, completed.progress)

    async def test_replanning_records_plan_version_and_zero_progress(self):
        mission = self.create_mission()
        self.complete_active_plan(mission.mission_id)
        await self.engine._bootstrap_mission(mission_registry.get_mission(mission.mission_id), "internal_napstertec")

        current = mission_registry.get_mission(mission.mission_id)
        self.assertEqual("v2", current.plan_version)
        self.assertEqual(1, current.zero_progress_count)
        self.assertEqual("v1", current.historical_plans[0]["version"])
        self.assertTrue(all(m.get("plan_version") == "v2" for m in current.milestones if m["milestone_id"] == "m5"))

    async def test_repeated_strategy_limit_escalates(self):
        mission = self.create_mission()
        for _ in range(3):
            with mission_registry.locked():
                current = mission_registry.missions[mission.mission_id]
                current.zero_progress_count = 0
                mission_registry.save_mission(current)
            self.complete_active_plan(mission.mission_id)
            await self.engine._bootstrap_mission(mission_registry.get_mission(mission.mission_id), "internal_napstertec")
            current = mission_registry.get_mission(mission.mission_id)
            if current.status == "WAITING_DIRECTOR":
                break

        current = mission_registry.get_mission(mission.mission_id)
        self.assertEqual("WAITING_DIRECTOR", current.status)
        self.assertEqual("REPEATED_STRATEGY_LIMIT_REACHED", current.escalation_reason)
        self.assertEqual("STOPPED", current.auto_continue_status)

    async def test_zero_progress_limit_escalates(self):
        mission = self.create_mission()
        with mission_registry.locked():
            current = mission_registry.missions[mission.mission_id]
            current.zero_progress_count = MAX_ZERO_PROGRESS_PLANS - 1
            for milestone in current.milestones:
                milestone["status"] = "Completed"
                milestone["progress"] = 100
            mission_registry.save_mission(current)

        await self.engine._bootstrap_mission(mission_registry.get_mission(mission.mission_id), "internal_napstertec")
        current = mission_registry.get_mission(mission.mission_id)
        self.assertEqual("WAITING_DIRECTOR", current.status)
        self.assertEqual("ZERO_PROGRESS_LIMIT_REACHED", current.escalation_reason)

    def test_stall_limit_escalates_without_unbounded_recovery(self):
        mission = self.create_mission()
        with mission_registry.locked():
            current = mission_registry.missions[mission.mission_id]
            current.last_progress_at = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
            current.stall_recovery_count = MAX_STALL_RECOVERIES - 1
            changed = MissionStallDetector().evaluate_and_recover(current, datetime.now(timezone.utc))
            mission_registry.save_mission(current)
        self.assertTrue(changed)
        current = mission_registry.get_mission(mission.mission_id)
        self.assertEqual("WAITING_DIRECTOR", current.status)
        self.assertEqual("STALL_RECOVERY_LIMIT_REACHED", current.escalation_reason)

    async def test_duplicate_materialization_dispatch_and_claim_are_prevented(self):
        mission = await self.bootstrap_first_work()
        with mission_registry.locked():
            current = mission_registry.missions[mission.mission_id]
            decision = current.progression_decisions[-1]
            MissionProgressionMaterializer().materialize(current, decision)
            MissionProgressionMaterializer().materialize(current, decision)
            mission_registry.save_mission(current)
        current = mission_registry.get_mission(mission.mission_id)
        self.assertEqual(1, len(current.progression_materializations))
        self.assertEqual(1, len(current.execution_requests))

        request = self.coordinator.claim_ready_request(mission.mission_id)
        self.assertIsNone(self.coordinator.claim_ready_request(mission.mission_id))
        first = self.coordinator.create_delegation(mission.mission_id, request["execution_request_id"])
        second = self.coordinator.create_delegation(mission.mission_id, request["execution_request_id"])
        self.assertEqual(first["delegation_id"], second["delegation_id"])
        self.assertIsNotNone(self.coordinator.claim_pending_delegation(mission.mission_id, "worker_a"))
        self.assertIsNone(self.coordinator.claim_pending_delegation(mission.mission_id, "worker_b"))
        current = mission_registry.get_mission(mission.mission_id)
        self.assertEqual(1, len([c for c in current.worker_claims if c["status"] == "ACTIVE"]))

    async def test_worker_delegation_consistency_is_validated(self):
        mission = await self.bootstrap_first_work()
        self.dispatch_and_claim(mission.mission_id)
        current = mission_registry.get_mission(mission.mission_id)
        codes = {finding["code"] for finding in MissionInvariantValidator().validate(current)}
        self.assertNotIn("ORPHAN_WORKER_CLAIM", codes)

        current.worker_claims.append({"worker_claim_id": "orphan", "delegation_id": "missing", "status": "ACTIVE"})
        codes = {finding["code"] for finding in MissionInvariantValidator().validate(current)}
        self.assertIn("ORPHAN_WORKER_CLAIM", codes)

    async def test_invalid_artifact_is_non_retryable_and_does_not_advance(self):
        mission = await self.bootstrap_first_work()
        _, delegation, _ = self.dispatch_and_claim(mission.mission_id)
        completed = await self.engine.process_delegation_completion(mission.mission_id, delegation["delegation_id"], {
            "artifact_id": "wrong", "artifact_type": "WrongArtifact", "verified": True
        })
        self.assertFalse(completed)
        current = mission_registry.get_mission(mission.mission_id)
        self.assertNotEqual("Completed", next(m for m in current.milestones if m["milestone_id"] == "m1")["status"])
        self.assertEqual(0, len(current.artifact_lineage))
        self.assertEqual(1, len(current.execution_requests))
        self.assertEqual(0, current.retry_count)
        self.assertEqual("NON_RETRYABLE_EXECUTION_FAILURE", current.terminal_reason)
        self.assertEqual("FAILED", current.delegation_history[0]["status"])

    async def test_worker_invokes_specialist_and_accepts_only_returned_artifact(self):
        from app.engine.autonomous_worker import autonomous_worker

        mission = await self.bootstrap_first_work()
        request = self.coordinator.claim_ready_request(mission.mission_id)
        delegation = self.coordinator.create_delegation(mission.mission_id, request["execution_request_id"])
        calls = []

        async def specialist_executor(mission_id, assigned):
            calls.append((mission_id, assigned["delegation_id"], assigned["target_agent"]))
            return AgentResult(
                success=True,
                agent_name=assigned["target_agent"],
                session_id="specialist_test",
                artifacts=[{
                    **self.verified_artifact(
                        assigned, suffix="worker", evidence_source=EvidenceSource.LIVE_EXTERNAL.value
                    ),
                    "artifact_id": "worker_artifact",
                }],
            )

        previous = autonomous_worker.specialist_executor
        autonomous_worker.specialist_executor = specialist_executor
        try:
            completed = await autonomous_worker.process_mission_once(mission.mission_id)
        finally:
            autonomous_worker.specialist_executor = previous

        self.assertTrue(completed)
        self.assertEqual([(mission.mission_id, delegation["delegation_id"], "lead_intelligence")], calls)
        current = mission_registry.get_mission(mission.mission_id)
        self.assertEqual("worker_artifact", current.artifact_lineage[0]["artifact_id"])

    async def test_agent_lifecycle_does_not_turn_grounded_failure_into_success(self):
        class GroundedFailureAgent(BaseAgent):
            def __init__(self):
                super().__init__(AgentMetadata(name="failure_agent", display_name="Failure Agent", description="test"))

            async def execute(self, context):
                return AgentResult(
                    success=False,
                    agent_name=self.metadata.name,
                    session_id=context.session_id,
                    final_output="Persistence Failed",
                )

        result = await GroundedFailureAgent().run(AgentContext(task="test", session_id="failure_session"))
        self.assertFalse(result.success)

    async def test_read_only_audit_route_and_saver_are_isolated(self):
        from app.agent.definitions.director_agent import DirectorIntelligenceAgent
        from app.agent.agent_models import AgentPermission
        from app.tools.plugins.director_tools import (
            DirectorContextBuilderTool,
            DirectorEvaluatorTool,
            DirectorSaverTool,
            classify_director_command,
            resolve_director_runtime_authority,
        )
        from app.tools.tool_executor import ToolExecutor
        from app.tools.tool_manager import ToolManager
        from app.tools.tool_registry import ToolRegistry

        mission = await self.bootstrap_first_work()
        before = Path(mission_registry.mission_file).read_bytes()
        mixed_audit_query = "Director, audit whether we should create and execute a fresh canary mission."
        audit_route = classify_director_command(mixed_audit_query)
        audit_authority = resolve_director_runtime_authority(mixed_audit_query)
        self.assertEqual("AUDIT", audit_route["command_class"])
        self.assertFalse(audit_authority["mutation_allowed"])
        self.assertFalse(audit_authority["external_side_effect_allowed"])

        built = await DirectorContextBuilderTool().execute(
            query="Director, perform a strict read-only engineering audit. Do not execute or modify anything."
        )
        context = built["isolated_context"]
        self.assertEqual("AUDIT", context["command_class"])
        self.assertEqual("READ_ONLY", context["authority_mode"])
        self.assertFalse(context["mutation_allowed"])

        evaluated = await DirectorEvaluatorTool().execute(context=context)
        artifact = evaluated["artifact"]
        self.assertTrue(artifact["read_only"])
        saved = await DirectorSaverTool().execute(artifact=artifact)
        self.assertTrue(saved["success"])
        self.assertFalse(saved["registered"])
        self.assertEqual(before, Path(mission_registry.mission_file).read_bytes())

        registry = ToolRegistry()
        registry.register(DirectorContextBuilderTool())
        registry.register(DirectorEvaluatorTool())
        registry.register(DirectorSaverTool())
        agent = DirectorIntelligenceAgent(tool_manager=ToolManager(registry, ToolExecutor()))
        result = await agent.run(AgentContext(
            task="Director, perform a strict read-only engineering audit. Do not execute or modify anything.",
            session_id="audit_route_test",
            granted_permissions={AgentPermission.READ},
        ))
        self.assertTrue(result.success)
        self.assertIn("AUDIT DEPTH", result.final_output)
        self.assertIn("State Mutation: None", result.final_output)
        self.assertEqual(before, Path(mission_registry.mission_file).read_bytes())

    async def test_structured_director_command_intent_matrix(self):
        from app.services.director_command_resolver import (
            DIRECTOR_COMMAND_UNRESOLVED,
            resolve_director_command,
        )
        from app.tools.plugins.director_tools import DirectorContextBuilderTool

        cases = (
            (
                "Director, inspect mission mis_123. Do not modify anything.",
                "AUDIT",
                "READ_ONLY",
                "NONE",
            ),
            (
                "Director, create a fresh mission and execute it. Do not deploy anything.",
                "MISSION_CREATE_EXECUTE",
                "MISSION_MUTATION",
                "INTERNAL_MISSION_STATE",
            ),
            (
                "Director, create a mission, then audit the result.",
                "MISSION_CREATE_EXECUTE",
                "MISSION_MUTATION",
                "INTERNAL_MISSION_STATE",
            ),
            (
                "Director, audit the current mission state.",
                "AUDIT",
                "READ_ONLY",
                "NONE",
            ),
            (
                REAL_RUNTIME_CANARY_COMMAND,
                "MISSION_CREATE_EXECUTE",
                "MISSION_MUTATION",
                "INTERNAL_MISSION_STATE",
            ),
        )

        for query, command_class, authority_mode, authority_scope in cases:
            with self.subTest(query=query):
                resolved = resolve_director_command(query)
                self.assertEqual(command_class, resolved["command_class"])
                self.assertEqual(authority_mode, resolved["authority_mode"])
                self.assertEqual(authority_scope, resolved["authority_scope"])
                self.assertEqual(
                    authority_scope == "INTERNAL_MISSION_STATE",
                    resolved["write_allowed"],
                )
                self.assertFalse(resolved["external_api_allowed"])
                self.assertFalse(resolved["external_side_effect_allowed"])

        mixed = resolve_director_command(
            "Director, create a mission and audit its result afterward."
        )
        self.assertEqual("MISSION_CREATE_EXECUTE", mixed["command_class"])
        self.assertEqual("MISSION_EXECUTION", mixed["intent_category"])

        constraint_only = "Director, do not deploy or send anything."
        unresolved = resolve_director_command(constraint_only)
        self.assertEqual("UNKNOWN", unresolved["command_class"])
        self.assertTrue(unresolved["constraint_only"])
        self.assertEqual(DIRECTOR_COMMAND_UNRESOLVED, unresolved["error_code"])
        self.assertFalse(unresolved["mutation_allowed"])
        self.assertFalse(unresolved["external_api_allowed"])
        self.assertNotEqual("EXECUTIVE_ACTION", unresolved["command_class"])

        for negated_action in (
            "Director, do not send anything.",
            "Director, do not publish anything.",
            "Director, do not execute outreach.",
            "Director, no external communication.",
        ):
            with self.subTest(negated_action=negated_action):
                negated = resolve_director_command(negated_action)
                self.assertEqual("UNKNOWN", negated["command_class"])
                self.assertTrue(negated["constraint_only"])
                self.assertFalse(negated["mutation_allowed"])
                self.assertFalse(negated["external_api_allowed"])

        built = await DirectorContextBuilderTool().execute(query=constraint_only)
        self.assertFalse(built["found"])
        self.assertEqual(DIRECTOR_COMMAND_UNRESOLVED, built["error"])

    async def test_unresolved_director_command_fails_closed_at_agent_boundary(self):
        from app.agent.definitions.director_agent import DirectorIntelligenceAgent
        from app.services.director_command_resolver import resolve_director_command
        from app.tools.plugins.director_tools import (
            DirectorContextBuilderTool,
            DirectorEvaluatorTool,
            DirectorSaverTool,
        )
        from app.tools.tool_executor import ToolExecutor
        from app.tools.tool_manager import ToolManager
        from app.tools.tool_registry import ToolRegistry

        query = "Director, do not deploy or send anything."
        resolution = resolve_director_command(query)
        registry = ToolRegistry()
        registry.register(DirectorContextBuilderTool())
        registry.register(DirectorEvaluatorTool())
        registry.register(DirectorSaverTool())

        result = await DirectorIntelligenceAgent(
            tool_manager=ToolManager(registry, ToolExecutor())
        ).run(AgentContext(
            task=query,
            session_id="director_unknown_fail_closed",
            granted_permissions={AgentPermission.READ},
            runtime_metadata={"command_context": resolution},
        ))

        self.assertFalse(result.success)
        self.assertIn("DIRECTOR_COMMAND_UNRESOLVED", result.final_output)
        self.assertNotIn("Generic Audit Fallback", result.final_output)
        self.assertNotIn("MISSION_ENGINE_VERIFIED", result.final_output)
        self.assertNotIn("successful audit", result.final_output.lower())

    async def test_realistic_canary_command_enters_mission_engine(self):
        from app.engine.autonomous_worker import autonomous_worker
        from app.tools.plugins.director_tools import (
            DirectorContextBuilderTool,
            DirectorEvaluatorTool,
            classify_director_command,
            resolve_director_runtime_authority,
        )

        route = classify_director_command(REALISTIC_CANARY_COMMAND)
        authority = resolve_director_runtime_authority(REALISTIC_CANARY_COMMAND)
        self.assertNotEqual("AUDIT", route["command_class"])
        self.assertEqual("MISSION_CREATE_EXECUTE", route["command_class"])
        self.assertEqual("INTERNAL_MISSION_STATE", authority["authority_scope"])
        self.assertTrue(authority["mission_creation_allowed"])
        self.assertTrue(authority["mission_execution_allowed"])
        self.assertTrue(authority["mutation_allowed"])
        self.assertFalse(authority["external_side_effect_allowed"])

        authority["granted_permissions"] = ["read", "write"]
        built = await DirectorContextBuilderTool().execute(
            query=REALISTIC_CANARY_COMMAND,
            authority_context=authority,
        )
        context = built["isolated_context"]
        self.assertEqual("INTERNAL_MISSION_STATE", context["authority_scope"])
        self.assertTrue(context["mission_creation_allowed"])
        self.assertTrue(context["mission_execution_allowed"])
        self.assertTrue(context["mutation_allowed"])

        calls = []
        original_process = MissionEngine.process_mission_request

        async def capture_process(engine, mode, query, session_id):
            calls.append((mode, query, session_id))
            return await original_process(engine, mode, query, session_id)

        async def mock_specialist(_mission_id, assigned):
            return AgentResult(
                success=True,
                agent_name=assigned["target_agent"],
                session_id="classifier_canary_specialist",
                artifacts=[{
                    "artifact_id": "classifier_canary_lead",
                    "artifact_type": assigned["expected_artifact"],
                    "verified": True,
                    "evidence_source": EvidenceSource.MOCK_FALLBACK.value,
                    "simulation_evidence": True,
                }],
            )

        with (
            patch.object(MissionEngine, "process_mission_request", new=capture_process),
            patch.object(autonomous_worker, "specialist_executor", new=mock_specialist),
        ):
            evaluated = await DirectorEvaluatorTool().execute(context=context)

        self.assertEqual(1, len(calls))
        self.assertEqual("MISSION CREATION MODE", calls[0][0])
        self.assertEqual("MissionArtifact", evaluated["artifact"]["artifact_type"])
        self.assertNotIn("Generic Audit Fallback", str(evaluated))

    async def test_mission_formatter_never_returns_generic_audit_fallback(self):
        from app.agent.definitions.director_agent import DirectorIntelligenceAgent
        from app.tools.plugins.director_tools import (
            DirectorContextBuilderTool,
            DirectorEvaluatorTool,
            DirectorSaverTool,
            resolve_director_runtime_authority,
        )
        from app.tools.tool_executor import ToolExecutor
        from app.tools.tool_manager import ToolManager
        from app.tools.tool_registry import ToolRegistry

        class MisroutingEvaluator(DirectorEvaluatorTool):
            async def execute(self, context, **kwargs):
                return {"artifact": {
                    "artifact_type": "DirectorArtifact",
                    "operating_mode": "EXECUTIVE COMMAND MODE",
                    "read_only": True,
                    "executive_summary": "Synthetic misrouted mission command.",
                    "execution_metadata": {},
                }}

        registry = ToolRegistry()
        registry.register(DirectorContextBuilderTool())
        registry.register(MisroutingEvaluator())
        registry.register(DirectorSaverTool())
        authority = resolve_director_runtime_authority(REALISTIC_CANARY_COMMAND)
        before = mission_registry.persisted_digest()
        result = await DirectorIntelligenceAgent(
            tool_manager=ToolManager(registry, ToolExecutor())
        ).run(AgentContext(
            task=REALISTIC_CANARY_COMMAND,
            session_id="fallback_protection_test",
            granted_permissions={AgentPermission.READ, AgentPermission.WRITE},
            runtime_metadata={"command_context": authority},
        ))

        self.assertFalse(result.success)
        self.assertIn("MISSION_DISPATCH_REJECTED", result.final_output)
        self.assertIn("MISSION_ACTION_UNRESOLVED", result.final_output)
        self.assertNotIn("Generic Audit Fallback", result.final_output)
        self.assertEqual(before, mission_registry.persisted_digest())

    async def test_real_audit_still_routes_mission_engineering_audit_service(self):
        from app.agent.definitions.director_agent import DirectorIntelligenceAgent
        from app.tools.plugins.director_tools import (
            DirectorContextBuilderTool,
            DirectorEvaluatorTool,
            DirectorSaverTool,
            resolve_director_runtime_authority,
        )
        from app.tools.tool_executor import ToolExecutor
        from app.tools.tool_manager import ToolManager
        from app.tools.tool_registry import ToolRegistry

        query = "Director, perform a strict read-only Mission Intelligence engineering audit. Do not modify anything."
        authority = resolve_director_runtime_authority(query)
        self.assertEqual("READ_ONLY", authority["authority_mode"])
        self.assertEqual("NONE", authority["authority_scope"])

        registry = ToolRegistry()
        registry.register(DirectorContextBuilderTool())
        registry.register(DirectorEvaluatorTool())
        registry.register(DirectorSaverTool())
        result = await DirectorIntelligenceAgent(
            tool_manager=ToolManager(registry, ToolExecutor())
        ).run(AgentContext(
            task=query,
            session_id="audit_dispatch_regression",
            granted_permissions={AgentPermission.READ},
            runtime_metadata={"command_context": authority},
        ))

        self.assertTrue(result.success)
        self.assertIn("Route: MissionEngineeringAuditService", result.final_output)
        self.assertIn("Authority: READ_ONLY", result.final_output)
        self.assertNotIn("Generic Audit Fallback", result.final_output)

    async def test_realistic_canary_without_internal_authority_fails_explicitly(self):
        from app.tools.plugins.director_tools import (
            DirectorContextBuilderTool,
            DirectorEvaluatorTool,
            resolve_director_runtime_authority,
        )

        authority = resolve_director_runtime_authority(REALISTIC_CANARY_COMMAND)
        authority["granted_permissions"] = ["read"]
        built = await DirectorContextBuilderTool().execute(
            query=REALISTIC_CANARY_COMMAND,
            authority_context=authority,
        )
        context = built["isolated_context"]
        self.assertEqual("UNAUTHORIZED", context["authority_mode"])
        self.assertEqual("NONE", context["authority_scope"])
        self.assertFalse(context["mutation_allowed"])
        before = mission_registry.persisted_digest()
        with self.assertRaisesRegex(PermissionError, "MISSION_AUTHORITY_MISSING"):
            await DirectorEvaluatorTool().execute(context=context)
        self.assertEqual(before, mission_registry.persisted_digest())

    async def test_agent_stream_director_fast_path_selects_mission_engine(self):
        from app.api import endpoints
        from app.engine.autonomous_worker import autonomous_worker
        from app.tools.plugins.director_tools import DirectorSaverTool

        calls = []
        captured = {}
        original_process = MissionEngine.process_mission_request
        director = endpoints.agent_registry.get_agent("director_intelligence")
        original_run = director.run

        async def capture_process(engine, mode, query, session_id):
            calls.append((mode, query, session_id))
            return await original_process(engine, mode, query, session_id)

        async def capture_run(context):
            captured["agent_name"] = director.metadata.name
            captured["permissions"] = set(context.granted_permissions)
            captured["command_context"] = dict(context.runtime_metadata["command_context"])
            return await original_run(context)

        async def save_without_database(self, artifact, **kwargs):
            return {
                "success": True,
                "artifact_id": artifact.get("artifact_id", "test_artifact"),
                "version": 1,
                "validation": "Passed (Test Isolation)",
                "registered": True,
            }

        async def mock_specialist(_mission_id, assigned):
            return AgentResult(
                success=True,
                agent_name=assigned["target_agent"],
                session_id="endpoint_canary_specialist",
                artifacts=[{
                    "artifact_id": "endpoint_canary_lead",
                    "artifact_type": assigned["expected_artifact"],
                    "verified": True,
                    "evidence_source": EvidenceSource.MOCK_FALLBACK.value,
                    "simulation_evidence": True,
                }],
            )

        with (
            patch.object(MissionEngine, "process_mission_request", new=capture_process),
            patch.object(
                MissionAuditService,
                "run_engineering_audit",
                side_effect=AssertionError("MissionEngineeringAuditService must not be selected"),
            ) as audit_mock,
            patch.object(DirectorSaverTool, "execute", new=save_without_database),
            patch.object(director, "run", new=capture_run),
            patch.object(autonomous_worker, "specialist_executor", new=mock_specialist),
        ):
            response = await endpoints.stream_agent_execution(endpoints.AgentExecutionRequest(
                goal=REAL_RUNTIME_CANARY_COMMAND,
                session_id="endpoint_canary_dispatch",
            ))
            events = []
            async for chunk in response.body_iterator:
                chunk_text = chunk.decode() if isinstance(chunk, bytes) else chunk
                for line in chunk_text.splitlines():
                    if line.startswith("data: "):
                        events.append(json.loads(line[6:]))

        completion = next(event for event in events if event.get("type") == "completion")
        final_result = completion["state"]["final_result"]
        self.assertEqual("director_intelligence", captured["agent_name"])
        self.assertEqual(1, len(calls))
        self.assertEqual("MISSION CREATION MODE", calls[0][0])
        self.assertEqual(0, audit_mock.call_count)
        self.assertIn(AgentPermission.WRITE, captured["permissions"])
        self.assertNotIn(AgentPermission.EXTERNAL_API, captured["permissions"])
        self.assertEqual("MISSION_CREATE_EXECUTE", captured["command_context"]["command_class"])
        self.assertEqual("MISSION_EXECUTION", captured["command_context"]["intent_category"])
        self.assertTrue(captured["command_context"]["write_allowed"])
        self.assertFalse(captured["command_context"]["external_api_allowed"])
        self.assertEqual("INTERNAL_MISSION_STATE", captured["command_context"]["authority_scope"])
        self.assertTrue(captured["command_context"]["mission_creation_allowed"])
        self.assertTrue(captured["command_context"]["mission_execution_allowed"])
        self.assertIn("Mission Engine Execution Report", final_result)
        self.assertNotIn("Generic Audit Fallback", final_result)
        current = next(iter(mission_registry.missions.values()))
        self.assertEqual(1, len(current.execution_requests))
        self.assertEqual([], current.external_operations)

    async def test_controlled_mission_creation_execution_classification(self):
        from app.tools.plugins.director_tools import (
            DirectorContextBuilderTool,
            classify_director_command,
            resolve_director_runtime_authority,
        )

        query = "Director, create and execute a fresh canary mission to audit internal mission-state progression for 1 new client."
        route = classify_director_command(query)
        self.assertEqual("MISSION_CREATE_EXECUTE", route["command_class"])
        self.assertEqual("MISSION CREATION MODE", route["operating_mode"])
        self.assertTrue(route["mission_creation_requested"])
        self.assertTrue(route["mission_execution_requested"])

        authority = resolve_director_runtime_authority(query)
        self.assertEqual("INTERNAL_MISSION_STATE", authority["authority_scope"])
        self.assertTrue(authority["internal_mission_mutation_allowed"])
        self.assertTrue(authority["mission_creation_allowed"])
        self.assertTrue(authority["mission_execution_allowed"])
        self.assertFalse(authority["external_side_effect_allowed"])

        authority["granted_permissions"] = ["read", "write"]
        built = await DirectorContextBuilderTool().execute(query=query, authority_context=authority)
        context = built["isolated_context"]
        self.assertEqual("MISSION_CREATE_EXECUTE", context["command_class"])
        self.assertEqual("MISSION_MUTATION", context["authority_mode"])
        self.assertEqual("INTERNAL_MISSION_STATE", context["authority_scope"])
        self.assertTrue(context["mutation_allowed"])

    async def test_scoped_internal_mutation_authority_creates_and_bootstraps_mission(self):
        from app.tools.plugins.director_tools import (
            DirectorContextBuilderTool,
            DirectorEvaluatorTool,
            resolve_director_runtime_authority,
        )

        query = "Director, this is not an audit. Create and execute one fresh canary mission to acquire 1 new client."
        authority = resolve_director_runtime_authority(query)
        authority["granted_permissions"] = ["read", "write"]
        built = await DirectorContextBuilderTool().execute(query=query, authority_context=authority)
        evaluated = await DirectorEvaluatorTool().execute(context=built["isolated_context"])

        artifact = evaluated["artifact"]
        self.assertEqual("MissionArtifact", artifact["artifact_type"])
        current = mission_registry.get_mission(artifact["mission_id"])
        self.assertIsNotNone(current)
        self.assertEqual("ACTIVE", current.status)
        self.assertEqual(1, len(current.execution_requests))
        self.assertEqual("READY", current.execution_requests[0]["status"])
        self.assertEqual([], current.external_operations)

    async def test_external_side_effect_authority_is_blocked(self):
        from app.tools.plugins.director_tools import (
            DirectorContextBuilderTool,
            DirectorEvaluatorTool,
            resolve_director_runtime_authority,
        )

        query = (
            "Director, this is not an audit. Create and execute a fresh canary mission.\n"
            f"MISSION OBJECTIVE: {LEAD_CANARY_OBJECTIVE}\n"
            "AUTHORIZED INTERNAL ACTIONS: Produce only the requested artifact."
        )
        authority = resolve_director_runtime_authority(query)
        self.assertFalse(authority["external_side_effect_allowed"])

        forged = dict(authority)
        forged["external_side_effect_allowed"] = True
        forged["granted_permissions"] = ["read", "write", "external_api"]
        built = await DirectorContextBuilderTool().execute(query=query, authority_context=forged)
        before = mission_registry.persisted_digest()
        with self.assertRaisesRegex(PermissionError, "separate external approval"):
            await DirectorEvaluatorTool().execute(context=built["isolated_context"])
        self.assertEqual(before, mission_registry.persisted_digest())

    async def test_unauthorized_mission_mutation_fails_closed(self):
        from app.tools.plugins.director_tools import (
            DirectorContextBuilderTool,
            DirectorEvaluatorTool,
            resolve_director_runtime_authority,
        )

        query = "Director, create and execute a fresh canary mission."
        authority = resolve_director_runtime_authority(query)
        authority["granted_permissions"] = ["read"]
        built = await DirectorContextBuilderTool().execute(query=query, authority_context=authority)
        context = built["isolated_context"]
        self.assertEqual("UNAUTHORIZED", context["authority_mode"])
        self.assertFalse(context["mutation_allowed"])
        before = mission_registry.persisted_digest()
        with self.assertRaisesRegex(PermissionError, "MISSION_AUTHORITY_MISSING"):
            await DirectorEvaluatorTool().execute(context=context)
        self.assertEqual(before, mission_registry.persisted_digest())

    async def test_legacy_quarantine_is_preserved_by_new_mission_control_plane(self):
        from app.engine.autonomous_worker import autonomous_worker
        from app.tools.plugins.director_tools import (
            DirectorContextBuilderTool,
            DirectorEvaluatorTool,
            resolve_director_runtime_authority,
        )

        legacy = self.create_mission("Acquire 10 restaurant clients for the legacy mission")
        with mission_registry.locked():
            quarantined = mission_registry.missions[legacy.mission_id]
            quarantined.status = "WAITING_DIRECTOR"
            quarantined.escalation_reason = "LEGACY_QUARANTINE"
            quarantined.auto_continue_status = "STOPPED"
            mission_registry.save_mission(quarantined)
        legacy_before = mission_registry.get_mission(legacy.mission_id).model_dump(mode="json")

        query = (
            "Director, this is not an audit. Create and execute a fresh canary mission.\n"
            f"MISSION OBJECTIVE: {LEAD_CANARY_OBJECTIVE}\n"
            "AUTHORIZED INTERNAL ACTIONS: Produce only the requested artifact."
        )
        authority = resolve_director_runtime_authority(query)
        authority["granted_permissions"] = ["read", "write"]
        built = await DirectorContextBuilderTool().execute(query=query, authority_context=authority)

        async def mock_specialist(_mission_id, assigned):
            return AgentResult(
                success=True,
                agent_name=assigned["target_agent"],
                session_id="legacy_quarantine_canary",
                artifacts=[{
                    "artifact_id": "legacy_quarantine_lead",
                    "artifact_type": assigned["expected_artifact"],
                    "verified": True,
                    "evidence_source": EvidenceSource.MOCK_FALLBACK.value,
                    "simulation_evidence": True,
                }],
            )

        with patch.object(autonomous_worker, "specialist_executor", new=mock_specialist):
            evaluated = await DirectorEvaluatorTool().execute(context=built["isolated_context"])

        self.assertNotEqual(legacy.mission_id, evaluated["artifact"]["mission_id"])
        self.assertEqual(legacy_before, mission_registry.get_mission(legacy.mission_id).model_dump(mode="json"))
        self.assertEqual("WAITING_DIRECTOR", mission_registry.get_mission(legacy.mission_id).status)

    async def test_read_only_engineering_audit_does_not_mutate_state(self):
        mission = await self.bootstrap_first_work()
        mission_file = Path(mission_registry.mission_file)
        before_bytes = mission_file.read_bytes()
        before_revision = mission_registry.get_mission(mission.mission_id).state_revision

        report = MissionAuditService().run_engineering_audit()

        self.assertEqual(before_bytes, mission_file.read_bytes())
        self.assertEqual(before_revision, mission_registry.get_mission(mission.mission_id).state_revision)
        self.assertEqual(1, report["missions_inspected"])
        self.assertEqual("PASSED", report["mutation_ledger"]["read_only_isolation_integrity"])
        self.assertIn("invariants", report)
        self.assertIn("thresholds", report)

    async def test_terminal_mission_cannot_restart_or_retain_active_work(self):
        mission = self.create_mission()
        with mission_registry.locked():
            current = mission_registry.missions[mission.mission_id]
            current.success_evidence = [
                {"evidence_id": f"deal_{i}", "event_type": "DEAL_WON", "client_id": f"client_{i}", "verified": True, "evidence_source": EvidenceSource.LIVE_EXTERNAL.value}
                for i in range(10)
            ]
            for milestone in current.milestones:
                milestone["status"] = "Completed"
                milestone["progress"] = 100
            mission_registry.save_mission(current)

        await self.engine._bootstrap_mission(mission_registry.get_mission(mission.mission_id), "internal_napstertec")
        completed = mission_registry.get_mission(mission.mission_id)
        before = Path(mission_registry.mission_file).read_bytes()
        await self.engine._bootstrap_mission(completed, "internal_napstertec")
        self.assertEqual(before, Path(mission_registry.mission_file).read_bytes())
        completed = mission_registry.get_mission(mission.mission_id)
        self.assertEqual("COMPLETED", completed.status)
        self.assertEqual([], completed.active_delegations)
        self.assertFalse(any(r["status"] in MissionInvariantValidator.ACTIVE_REQUEST_STATES for r in completed.execution_requests))

    async def test_corrupt_persisted_state_is_quarantined_before_execution(self):
        mission = await self.bootstrap_first_work()
        with mission_registry.locked():
            current = mission_registry.missions[mission.mission_id]
            current.plan_version = "v99"
            current.progress = 900
            current.worker_claims.append({
                "worker_claim_id": "legacy_claim",
                "delegation_id": "missing",
                "status": "ACTIVE",
                "health": "HEALTHY",
            })
            mission_registry.save_mission(current)

        quarantined = MissionSafetyReconciler().quarantine_unsafe_missions(mission.mission_id)
        self.assertEqual([mission.mission_id], quarantined)
        current = mission_registry.get_mission(mission.mission_id)
        self.assertEqual("WAITING_DIRECTOR", current.status)
        self.assertEqual("PERSISTED_STATE_INVARIANT_VIOLATION", current.escalation_reason)
        self.assertEqual("BLOCKED", current.execution_requests[0]["status"])
        self.assertEqual("INVALIDATED", current.worker_claims[0]["status"])
        self.assertIsNone(self.coordinator.claim_ready_request(mission.mission_id))

    async def test_qualified_lead_canary_has_distinct_bounded_contract(self):
        mission = self.create_mission(QUALIFIED_LEAD_CANARY_OBJECTIVE)
        await self.engine._bootstrap_mission(mission, "internal_napstertec")
        current = mission_registry.get_mission(mission.mission_id)

        self.assertEqual("QUALIFIED_LEAD_CANARY", current.verification_mode)
        self.assertFalse(current.simulation_mode)
        self.assertEqual(1, current.success_criteria["required"])
        self.assertEqual(EvidenceSource.LIVE_EXTERNAL.value, current.success_criteria["required_evidence_source"])
        self.assertTrue(current.success_criteria["require_entity_qualification"])
        self.assertEqual(1, len(current.execution_requests))
        self.assertTrue(current.execution_requests[0]["read_only_external_discovery"])

    async def test_one_qualified_live_artifact_completes_and_stops_exactly_once(self):
        mission = self.create_mission(QUALIFIED_LEAD_CANARY_OBJECTIVE)
        await self.engine._bootstrap_mission(mission, "internal_napstertec")
        _, delegation, _ = self.dispatch_and_claim(mission.mission_id)

        completed = await self.engine.process_delegation_completion(
            mission.mission_id,
            delegation["delegation_id"],
            self.verified_live_artifact(delegation, suffix="qualified"),
        )
        self.assertTrue(completed)

        current = mission_registry.get_mission(mission.mission_id)
        self.assertEqual("COMPLETED", current.status)
        self.assertEqual("STOPPED", current.auto_continue_status)
        self.assertEqual("SUCCESS_CRITERIA_VERIFIED", current.terminal_reason)
        self.assertEqual("1 / 1 verified LeadArtifact", current.success_criteria_progress)
        self.assertEqual(100, current.progress)
        self.assertEqual(1, len(current.artifact_lineage))
        self.assertEqual(1, len(current.execution_requests))
        lineage = current.artifact_lineage[0]
        for field in (
            "mission_id", "plan_version", "milestone_id", "decision_id", "materialization_id",
            "execution_request_id", "delegation_id", "worker_claim_id", "specialist",
            "artifact_type", "created_at", "entity_qualification", "source_type",
        ):
            self.assertTrue(lineage.get(field), field)
        self.assertTrue(lineage["entity_qualification"]["qualified"])

        await self.engine._bootstrap_mission(current, "internal_napstertec")
        stable = mission_registry.get_mission(mission.mission_id)
        self.assertEqual(1, len(stable.artifact_lineage))
        self.assertEqual(1, len(stable.execution_requests))

    async def test_qualified_canary_unverified_entity_stops_without_retry(self):
        mission = self.create_mission(QUALIFIED_LEAD_CANARY_OBJECTIVE)
        await self.engine._bootstrap_mission(mission, "internal_napstertec")
        _, delegation, _ = self.dispatch_and_claim(mission.mission_id)

        accepted = await self.engine.process_delegation_completion(
            mission.mission_id,
            delegation["delegation_id"],
            self.unqualified_live_artifact(delegation),
        )

        self.assertFalse(accepted)
        current = mission_registry.get_mission(mission.mission_id)
        self.assertEqual("WAITING_DIRECTOR", current.status)
        self.assertEqual("BUSINESS_ENTITY_UNVERIFIED", current.terminal_reason)
        self.assertEqual("BUSINESS_ENTITY_UNVERIFIED", current.last_error)
        self.assertEqual("STOPPED", current.auto_continue_status)
        self.assertEqual("0 / 1 verified LeadArtifact", current.success_criteria_progress)
        self.assertEqual(1, len(current.execution_requests))
        self.assertEqual([], current.artifact_lineage)

    async def test_live_evidence_canary_still_accepts_unqualified_external_page(self):
        mission = self.create_mission(LIVE_EVIDENCE_CANARY_OBJECTIVE)
        await self.engine._bootstrap_mission(mission, "internal_napstertec")
        _, delegation, _ = self.dispatch_and_claim(mission.mission_id)

        accepted = await self.engine.process_delegation_completion(
            mission.mission_id,
            delegation["delegation_id"],
            self.unqualified_live_artifact(delegation, suffix="technical_live"),
        )

        self.assertTrue(accepted)
        current = mission_registry.get_mission(mission.mission_id)
        self.assertEqual("COMPLETED", current.status)
        self.assertEqual("1 / 1 verified LeadArtifact", current.success_criteria_progress)
        self.assertFalse(current.artifact_lineage[0]["entity_qualification"]["qualified"])

    async def test_unqualified_live_evidence_cannot_complete_production_lead_mission(self):
        mission = self.create_mission(
            "Research exactly one business prospect and produce exactly one valid LeadArtifact."
        )
        await self.engine._bootstrap_mission(mission, "internal_napstertec")
        _, delegation, _ = self.dispatch_and_claim(mission.mission_id)

        accepted = await self.engine.process_delegation_completion(
            mission.mission_id,
            delegation["delegation_id"],
            self.unqualified_live_artifact(delegation, suffix="production_unqualified"),
        )

        self.assertFalse(accepted)
        current = mission_registry.get_mission(mission.mission_id)
        self.assertEqual("0 / 1 verified LeadArtifact", current.success_criteria_progress)
        self.assertFalse(current.mission_objective_achieved)
        self.assertNotEqual("COMPLETED", current.status)
        self.assertEqual([], current.artifact_lineage)

    def test_qualified_canary_inherits_live_read_only_authority(self):
        from app.engine.autonomous_worker import autonomous_worker

        delegation = dict(self.live_delegation_fixture())
        delegation["verification_mode"] = "QUALIFIED_LEAD_CANARY"
        context = autonomous_worker._build_specialist_context("mis_qualified", delegation)

        self.assertEqual("READ_EXTERNAL_DISCOVERY", context.runtime_metadata["authority_scope"])
        self.assertIn("lead_upsert", context.runtime_metadata["blocked_tools"])
        self.assertFalse(context.runtime_metadata["external_write_allowed"])
        self.assertFalse(context.runtime_metadata["outreach_allowed"])
        self.assertTrue(context.planner_output["require_live_evidence"])
        self.assertTrue(context.planner_output["require_entity_qualification"])
        self.assertNotIn(AgentPermission.OUTREACH, context.granted_permissions)


if __name__ == "__main__":
    unittest.main()
