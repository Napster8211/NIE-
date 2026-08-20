import unittest

from app.agent.agent_models import AgentContext
from app.agent.definitions.lead_agent import LeadIntelligenceAgent
from app.engine.autonomous_worker import autonomous_worker
from app.schemas.evidence import EvidenceSource
from app.tools.plugins.business_discovery import BusinessDiscoveryTool


AGGREGATOR = {
    "title": "THE 10 BEST Restaurants in Accra",
    "url": "https://www.tripadvisor.com/Restaurants-g293797-Accra_Greater_Accra.html",
    "snippet": "A ranked list of restaurants in Accra.",
    "source_provider": "fixture",
}
REPORT = {
    "title": "Restaurant Industry Market Report 2026",
    "url": "https://research.example.com/report/restaurant-industry-2026",
    "snippet": "Industry research and market forecast.",
    "source_provider": "fixture",
}
BUKA = {
    "title": "Buka Restaurant",
    "url": "https://bukarestaurant.com.gh/menu",
    "snippet": "A restaurant serving Ghanaian food in Accra.",
    "source_provider": "fixture",
}
AZMERA = {
    "title": "Azmera Restaurant",
    "url": "https://azmerarestaurant.com/menu",
    "snippet": "An Ethiopian restaurant serving diners in Accra.",
    "source_provider": "fixture",
}
BUSINESS_PROFILE = {
    "title": "Buka Restaurant",
    "url": "https://profiles.example.com/business/buka-restaurant",
    "snippet": "Business profile for a restaurant in Accra.",
    "source_provider": "fixture",
}


class _BoundedProvider:
    name = "fixture"
    endpoint = "https://search.example.test/"

    def __init__(self, results=None, error=None):
        self.results = list(results or [])
        self.error = error
        self.calls = []

    async def search(self, query, max_results=10):
        self.calls.append((query, max_results))
        if self.error:
            raise self.error
        return self.results[:max_results]


class QualifiedLeadCandidateScanTests(unittest.IsolatedAsyncioTestCase):
    async def _run_agent(self, candidates, scan_limit=5, provider_error=None):
        provider = _BoundedProvider(candidates, provider_error)
        tool = BusinessDiscoveryTool(provider, "fixture")

        async def invoke_tool(tool_name, parameters, context):
            self.assertEqual("business_discovery", tool_name)
            return {"output": await tool.execute(**parameters)}

        agent = LeadIntelligenceAgent()
        agent.invoke_tool = invoke_tool
        result = await agent.execute(AgentContext(
            task="Research one qualified restaurant",
            session_id="candidate_scan_fixture",
            planner_output={
                "query": "restaurant",
                "location": "Accra Ghana",
                "discovery_scope": {
                    "category": "restaurant",
                    "location": "Accra Ghana",
                    "max_results": 1,
                    "candidate_scan_limit": scan_limit,
                    "query_mode": "QUALIFIED_ENTITY_SEARCH",
                },
                "target_count": 1,
                "verification_mode": "QUALIFIED_LEAD_CANARY",
                "require_live_evidence": True,
                "require_entity_qualification": True,
                "mission_id": "mis_scan",
                "plan_version": "v1",
                "milestone_id": "m1",
                "decision_id": "dec_scan",
                "materialization_id": "mat_scan",
                "execution_request_id": "mer_scan",
                "delegation_id": "del_scan",
                "worker_claim_id": "wcl_scan",
            },
        ))
        return result, provider

    async def test_first_aggregator_second_business_selects_one_artifact(self):
        result, provider = await self._run_agent([AGGREGATOR, BUKA, AZMERA])

        self.assertTrue(result.success)
        self.assertEqual([("restaurant Accra Ghana official website", 5)], provider.calls)
        self.assertEqual(1, len(result.artifacts))
        self.assertEqual("Buka Restaurant", result.artifacts[0]["business_name"])
        metadata = result.artifacts[0]["source_metadata"]
        self.assertEqual(1, metadata["request_count"])
        self.assertEqual(2, metadata["candidate_count_examined"])
        self.assertEqual(2, metadata["qualified_candidate_index"])
        self.assertEqual("GENERIC_LIST_OR_INFORMATIONAL_TITLE", metadata["candidate_diagnostics"][0]["rejection_reason"])
        self.assertEqual(AGGREGATOR["url"], metadata["candidate_diagnostics"][0]["canonical_url"])
        self.assertEqual(BUKA["url"], metadata["candidate_diagnostics"][1]["canonical_url"])

    async def test_scan_stops_at_first_qualified_candidate(self):
        result, _ = await self._run_agent([AGGREGATOR, REPORT, BUKA, AZMERA])

        metadata = result.artifacts[0]["source_metadata"]
        self.assertEqual(3, metadata["candidate_count_examined"])
        self.assertEqual(3, len(metadata["candidate_diagnostics"]))
        self.assertEqual(3, metadata["qualified_candidate_index"])
        self.assertEqual("Buka Restaurant", result.artifacts[0]["business_name"])

    async def test_all_candidates_rejected_without_artifact_or_retry(self):
        result, provider = await self._run_agent([AGGREGATOR, REPORT])

        self.assertFalse(result.success)
        self.assertEqual(["BUSINESS_ENTITY_UNVERIFIED"], result.errors)
        self.assertEqual([], result.artifacts)
        self.assertEqual(1, len(provider.calls))
        failure = autonomous_worker._extract_failure_evidence(result)
        self.assertEqual(EvidenceSource.LIVE_EXTERNAL.value, failure["evidence_source"])
        self.assertEqual(2, failure["source_metadata"]["candidate_count_examined"])
        self.assertIsNone(failure["source_metadata"]["qualified_candidate_index"])
        self.assertEqual(2, len(failure["source_metadata"]["candidate_diagnostics"]))

    async def test_candidate_scan_limit_bounds_examined_results(self):
        result, provider = await self._run_agent(
            [AGGREGATOR, REPORT, AGGREGATOR, BUKA, AZMERA],
            scan_limit=3,
        )

        self.assertFalse(result.success)
        self.assertEqual([("restaurant Accra Ghana official website", 3)], provider.calls)
        metadata = result.tool_calls[0]["output"]["source_metadata"]
        self.assertEqual(3, metadata["raw_result_count"])
        self.assertEqual(3, metadata["candidate_count_examined"])
        self.assertEqual(3, len(metadata["candidate_diagnostics"]))

    async def test_multiple_qualified_candidates_use_first_only(self):
        result, _ = await self._run_agent([BUKA, AZMERA])

        self.assertTrue(result.success)
        self.assertEqual(1, len(result.artifacts))
        self.assertEqual("Buka Restaurant", result.artifacts[0]["business_name"])
        self.assertEqual(1, result.artifacts[0]["source_metadata"]["qualified_candidate_index"])
        self.assertEqual(1, result.artifacts[0]["source_metadata"]["candidate_count_examined"])

    async def test_business_specific_profile_can_be_selected(self):
        result, _ = await self._run_agent([AGGREGATOR, BUSINESS_PROFILE])

        self.assertTrue(result.success)
        self.assertEqual("BUSINESS_PROFILE", result.artifacts[0]["source_type"])
        self.assertEqual(2, result.artifacts[0]["source_metadata"]["qualified_candidate_index"])

    async def test_provider_failure_is_one_request_and_live_only(self):
        result, provider = await self._run_agent([], provider_error=TimeoutError())

        self.assertFalse(result.success)
        self.assertEqual(1, len(provider.calls))
        self.assertEqual([], result.artifacts)
        discovery = result.tool_calls[0]["output"]
        self.assertEqual(EvidenceSource.UNKNOWN.value, discovery["evidence_source"])
        self.assertEqual(1, discovery["source_metadata"]["request_count"])
        self.assertFalse(discovery["source_metadata"]["request_succeeded"])
        self.assertNotEqual("mock", discovery["provider_mode"])


if __name__ == "__main__":
    unittest.main()
