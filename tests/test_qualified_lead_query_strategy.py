import unittest

from app.agent.agent_models import AgentContext
from app.agent.definitions.lead_agent import LeadIntelligenceAgent
from app.schemas.evidence import EvidenceSource
from app.schemas.lead import (
    BusinessDiscoveryQueryMode,
    BusinessEntityQualificationStatus,
    BusinessSourceType,
)
from app.services.business_entity_qualification import qualify_business_entity
from app.tools.plugins.business_discovery import BusinessDiscoveryTool


AGGREGATOR = {
    "title": "THE 10 BEST Restaurants in Accra",
    "url": "https://www.tripadvisor.com/Restaurants-g293797-Accra_Greater_Accra.html",
    "snippet": "A ranked list of restaurants in Accra.",
    "source_provider": "fixture",
}
BUKA = {
    "title": "Buka Restaurant | Accra",
    "url": "https://bukarestaurant.com.gh/menu",
    "snippet": "A restaurant serving Ghanaian food in Accra.",
    "source_provider": "fixture",
}


class _QueryFixtureProvider:
    name = "fixture"
    endpoint = "https://search.example.test/"

    def __init__(self, results):
        self.results = list(results)
        self.calls = []

    async def search(self, query, max_results=10):
        self.calls.append((query, max_results))
        return self.results[:max_results]


class QualifiedLeadQueryStrategyTests(unittest.IsolatedAsyncioTestCase):
    async def test_qualified_query_is_entity_oriented_and_bounded(self):
        provider = _QueryFixtureProvider([AGGREGATOR, BUKA])
        discovery = await BusinessDiscoveryTool(provider, "fixture").execute(
            query="restaurant",
            location="Accra Ghana",
            max_results=1,
            candidate_scan_limit=5,
            query_mode=BusinessDiscoveryQueryMode.QUALIFIED_ENTITY_SEARCH,
            require_live_evidence=True,
            require_entity_qualification=True,
        )

        expected = "restaurant Accra Ghana official website"
        self.assertEqual([(expected, 5)], provider.calls)
        self.assertIn("restaurant", expected)
        self.assertIn("Accra Ghana", expected)
        self.assertIn("official website", expected)
        self.assertNotIn("LeadArtifact", expected)
        self.assertNotIn("Global business", expected)
        self.assertNotIn("production", expected)
        metadata = discovery["source_metadata"]
        self.assertEqual(BusinessDiscoveryQueryMode.QUALIFIED_ENTITY_SEARCH.value, metadata["query_mode"])
        self.assertEqual(expected, metadata["query"])
        self.assertEqual(1, metadata["request_count"])
        self.assertEqual(5, metadata["candidate_scan_limit"])
        self.assertEqual(1, metadata["qualified_artifact_target"])
        self.assertEqual(2, metadata["qualified_candidate_index"])
        self.assertEqual(EvidenceSource.LIVE_EXTERNAL.value, discovery["evidence_source"])
        self.assertEqual(1, len(discovery["results"]))

    async def test_generic_discovery_query_semantics_are_unchanged(self):
        provider = _QueryFixtureProvider([BUKA])
        discovery = await BusinessDiscoveryTool(provider, "fixture").execute(
            query="restaurant",
            location="Accra Ghana",
            max_results=1,
            query_mode=BusinessDiscoveryQueryMode.GENERIC_DISCOVERY,
            require_live_evidence=True,
        )

        self.assertEqual([("restaurant Accra Ghana business", 1)], provider.calls)
        self.assertEqual("restaurant Accra Ghana business", discovery["source_metadata"]["query"])
        self.assertEqual(BusinessDiscoveryQueryMode.GENERIC_DISCOVERY.value, discovery["source_metadata"]["query_mode"])

    async def test_qualified_query_rejects_incomplete_or_orchestration_scope_before_request(self):
        for query, location in (
            ("restaurant", "Global"),
            ("Research LeadArtifact production", "Accra Ghana"),
            ("mission restaurant planner", "Accra Ghana"),
        ):
            provider = _QueryFixtureProvider([BUKA])
            with self.subTest(query=query, location=location):
                with self.assertRaisesRegex(ValueError, "DISCOVERY_SCOPE_INCOMPLETE"):
                    await BusinessDiscoveryTool(provider, "fixture").execute(
                        query=query,
                        location=location,
                        max_results=1,
                        candidate_scan_limit=5,
                        query_mode=BusinessDiscoveryQueryMode.QUALIFIED_ENTITY_SEARCH,
                        require_live_evidence=True,
                        require_entity_qualification=True,
                    )
                self.assertEqual([], provider.calls)

    async def test_qualified_production_lead_research_forces_entity_mode(self):
        provider = _QueryFixtureProvider([BUKA])
        tool = BusinessDiscoveryTool(provider, "fixture")

        async def invoke_tool(tool_name, parameters, context):
            self.assertEqual("business_discovery", tool_name)
            self.assertEqual(
                BusinessDiscoveryQueryMode.QUALIFIED_ENTITY_SEARCH,
                parameters["query_mode"],
            )
            return {"output": await tool.execute(**parameters)}

        agent = LeadIntelligenceAgent()
        agent.invoke_tool = invoke_tool
        result = await agent.execute(AgentContext(
            task="Research one qualified restaurant",
            session_id="qualified_production_query_fixture",
            planner_output={
                "discovery_scope": {
                    "category": "restaurant",
                    "location": "Accra Ghana",
                    "max_results": 1,
                    "candidate_scan_limit": 5,
                },
                "target_count": 1,
                "verification_mode": "PRODUCTION_BUSINESS_SUCCESS",
                "require_live_evidence": True,
                "require_entity_qualification": True,
                "mission_id": "mis_query",
                "plan_version": "v1",
                "milestone_id": "m1",
                "decision_id": "dec_query",
                "materialization_id": "mat_query",
                "execution_request_id": "mer_query",
                "delegation_id": "del_query",
                "worker_claim_id": "wcl_query",
            },
        ))

        self.assertTrue(result.success)
        self.assertEqual([("restaurant Accra Ghana official website", 5)], provider.calls)
        self.assertEqual(1, len(result.artifacts))

    async def test_qualified_production_without_scope_fails_before_request(self):
        calls = []

        async def invoke_tool(tool_name, parameters, context):
            calls.append((tool_name, parameters))
            raise AssertionError("qualified production search must require structured scope")

        agent = LeadIntelligenceAgent()
        agent.invoke_tool = invoke_tool
        result = await agent.execute(AgentContext(
            task="Research one qualified restaurant",
            session_id="missing_production_scope_fixture",
            planner_output={
                "query": "Research LeadArtifact production",
                "location": "Global",
                "target_count": 1,
                "verification_mode": "PRODUCTION_BUSINESS_SUCCESS",
                "require_live_evidence": True,
                "require_entity_qualification": True,
            },
        ))

        self.assertFalse(result.success)
        self.assertEqual(["DISCOVERY_SCOPE_INCOMPLETE"], result.errors)
        self.assertEqual([], calls)

    def test_numeric_generic_list_titles_are_rejected(self):
        for title in (
            "25 must try restaurants and places to eat in Accra",
            "20 places to eat in Accra",
        ):
            with self.subTest(title=title):
                qualification = qualify_business_entity({
                    "name": title,
                    "category": "restaurant",
                    "description": "A dining guide for Accra.",
                    "city": "Accra Ghana",
                    "source_url": "https://viewghana.com/accra-dining",
                })
                self.assertEqual(BusinessEntityQualificationStatus.UNVERIFIED, qualification.status)
                self.assertEqual(BusinessSourceType.AGGREGATOR, qualification.source_type)
                self.assertEqual(
                    ["GENERIC_LIST_OR_INFORMATIONAL_TITLE"],
                    qualification.qualification_reasons,
                )
                self.assertFalse(qualification.qualified)

    def test_specific_restaurant_title_is_not_a_generic_list(self):
        qualification = qualify_business_entity({
            "name": "Buka Restaurant | Accra",
            "category": "restaurant",
            "description": "A restaurant serving Ghanaian food in Accra.",
            "city": "Accra Ghana",
            "source_url": "https://bukarestaurant.com.gh/menu",
        })

        self.assertEqual(BusinessEntityQualificationStatus.VERIFIED_BUSINESS, qualification.status)
        self.assertEqual(BusinessSourceType.OFFICIAL_BUSINESS_SITE, qualification.source_type)
        self.assertTrue(qualification.qualified)

    async def test_candidate_diagnostics_store_only_compact_canonical_fields(self):
        provider = _QueryFixtureProvider([AGGREGATOR, BUKA])
        discovery = await BusinessDiscoveryTool(provider, "fixture").execute(
            query="restaurant",
            location="Accra Ghana",
            max_results=1,
            candidate_scan_limit=5,
            query_mode=BusinessDiscoveryQueryMode.QUALIFIED_ENTITY_SEARCH,
            require_live_evidence=True,
            require_entity_qualification=True,
        )

        diagnostics = discovery["source_metadata"]["candidate_diagnostics"]
        self.assertEqual(2, len(diagnostics))
        expected_keys = {
            "rank", "title", "canonical_url", "hostname", "source_type",
            "qualification_status", "rejection_reason",
        }
        self.assertEqual(expected_keys, set(diagnostics[0]))
        self.assertEqual(expected_keys, set(diagnostics[1]))
        self.assertEqual(AGGREGATOR["url"], diagnostics[0]["canonical_url"])
        self.assertEqual(BUKA["url"], diagnostics[1]["canonical_url"])
        self.assertEqual("www.tripadvisor.com", diagnostics[0]["hostname"])
        self.assertEqual("bukarestaurant.com.gh", diagnostics[1]["hostname"])
        self.assertEqual(1, diagnostics[0]["rank"])
        self.assertEqual(2, diagnostics[1]["rank"])
        self.assertIsNone(diagnostics[1]["rejection_reason"])
        self.assertEqual(1, len(provider.calls))


if __name__ == "__main__":
    unittest.main()
