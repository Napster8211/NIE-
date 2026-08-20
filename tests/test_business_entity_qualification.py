import unittest

from app.agent.agent_models import AgentContext
from app.agent.definitions.lead_agent import LeadIntelligenceAgent
from app.schemas.evidence import EvidenceSource
from app.schemas.lead import (
    BusinessDiscoveryQueryMode,
    BusinessEntityQualificationStatus,
    BusinessSourceType,
)
from app.services.business_entity_qualification import (
    is_entity_qualified,
    qualify_business_entity,
)
from app.tools.plugins.business_discovery import BusinessDiscoveryTool


class _FixtureProvider:
    name = "fixture"
    endpoint = "https://search.example.test/"

    def __init__(self, result):
        self.result = result
        self.calls = []

    async def search(self, query, max_results=10):
        self.calls.append((query, max_results))
        return [self.result]


class BusinessEntityQualificationTests(unittest.IsolatedAsyncioTestCase):
    def qualify(self, **overrides):
        candidate = {
            "name": "Buka Restaurant",
            "category": "restaurant",
            "description": "A restaurant serving Ghanaian food in Accra.",
            "city": "Accra Ghana",
            "source_url": "https://bukarestaurant.com.gh/menu",
        }
        candidate.update(overrides)
        return qualify_business_entity(candidate)

    def test_official_business_website_qualifies(self):
        qualification = self.qualify()
        self.assertEqual(BusinessEntityQualificationStatus.VERIFIED_BUSINESS, qualification.status)
        self.assertEqual(BusinessSourceType.OFFICIAL_BUSINESS_SITE, qualification.source_type)
        self.assertTrue(qualification.qualified)
        self.assertEqual("bukarestaurant.com.gh", qualification.business_domain)

    def test_business_specific_third_party_profile_may_qualify(self):
        qualification = self.qualify(
            source_url="https://profiles.example.com/business/buka-restaurant",
        )
        self.assertEqual(BusinessEntityQualificationStatus.LIKELY_BUSINESS, qualification.status)
        self.assertEqual(BusinessSourceType.BUSINESS_PROFILE, qualification.source_type)
        self.assertTrue(qualification.qualified)

    def test_aggregator_list_page_does_not_qualify(self):
        qualification = self.qualify(
            name="THE 10 BEST Restaurants in Accra",
            source_url="https://travel.example.com/restaurants/accra",
        )
        self.assertEqual(BusinessEntityQualificationStatus.UNVERIFIED, qualification.status)
        self.assertEqual(BusinessSourceType.AGGREGATOR, qualification.source_type)
        self.assertFalse(qualification.qualified)

    def test_research_report_does_not_qualify(self):
        qualification = self.qualify(
            name="Live Production Services Market Report",
            category="live production services",
            source_url="https://www.trendvaultresearch.com/report/live-production-services-64817",
        )
        self.assertEqual(BusinessEntityQualificationStatus.NON_BUSINESS_SOURCE, qualification.status)
        self.assertEqual(BusinessSourceType.REPORT, qualification.source_type)
        self.assertFalse(qualification.qualified)

    def test_generic_category_page_does_not_qualify(self):
        qualification = self.qualify(
            name="Best Hotels in Ghana",
            category="hotel",
            source_url="https://travel.example.com/hotels/ghana",
        )
        self.assertEqual(BusinessSourceType.AGGREGATOR, qualification.source_type)
        self.assertFalse(qualification.qualified)

    def test_missing_business_name_fails(self):
        qualification = self.qualify(name="")
        self.assertEqual(BusinessEntityQualificationStatus.INSUFFICIENT_EVIDENCE, qualification.status)
        self.assertIn("BUSINESS_NAME_MISSING", qualification.qualification_reasons)
        self.assertFalse(qualification.qualified)

    def test_invalid_or_unsafe_source_url_fails(self):
        for source_url in (
            "javascript:alert(1)", "data:text/plain,unsafe", "file:///tmp/business", "https://",
            "//example.com/business", "https://exa mple.com/business",
        ):
            with self.subTest(source_url=source_url):
                qualification = self.qualify(source_url=source_url)
                self.assertFalse(qualification.qualified)
                self.assertIn("SOURCE_URL_INVALID", qualification.qualification_reasons)

    async def test_live_external_unqualified_result_preserves_evidence_validity(self):
        provider = _FixtureProvider({
            "title": "Live Production Services Market Report",
            "url": "https://www.trendvaultresearch.com/report/live-production-services-64817",
            "snippet": "A market research report and industry forecast.",
            "source_provider": "fixture",
        })
        discovery = await BusinessDiscoveryTool(provider, "fixture").execute(
            query="live production services",
            location="Global",
            max_results=1,
            require_live_evidence=True,
        )
        candidate = discovery["results"][0]

        self.assertEqual(EvidenceSource.LIVE_EXTERNAL.value, discovery["evidence_source"])
        self.assertTrue(discovery["source_metadata"]["request_succeeded"])
        self.assertFalse(discovery["simulation_evidence"])
        self.assertFalse(candidate["entity_qualified"])
        self.assertEqual(BusinessSourceType.REPORT.value, candidate["source_type"])
        self.assertFalse(is_entity_qualified(candidate["entity_qualification"]))
        self.assertEqual(1, len(provider.calls))

    async def _run_qualified_lead_agent(self, provider_result):
        discovery = await BusinessDiscoveryTool(
            _FixtureProvider(provider_result), "fixture"
        ).execute(
            query="restaurant", location="Accra Ghana", max_results=1,
            candidate_scan_limit=5,
            require_live_evidence=True,
            require_entity_qualification=True,
        )

        async def fake_invoke_tool(tool_name, parameters, context):
            self.assertEqual("business_discovery", tool_name)
            self.assertTrue(parameters["require_live_evidence"])
            self.assertTrue(parameters["require_entity_qualification"])
            self.assertEqual(5, parameters["candidate_scan_limit"])
            self.assertEqual(
                BusinessDiscoveryQueryMode.QUALIFIED_ENTITY_SEARCH,
                parameters["query_mode"],
            )
            return {"output": discovery}

        agent = LeadIntelligenceAgent()
        agent.invoke_tool = fake_invoke_tool
        result = await agent.execute(AgentContext(
            task="Research one qualified business prospect",
            session_id="qualified_entity_fixture",
            planner_output={
                "query": "restaurant",
                "location": "Accra Ghana",
                "discovery_scope": {
                    "category": "restaurant",
                    "location": "Accra Ghana",
                    "max_results": 1,
                    "candidate_scan_limit": 5,
                    "query_mode": "QUALIFIED_ENTITY_SEARCH",
                },
                "target_count": 1,
                "verification_mode": "QUALIFIED_LEAD_CANARY",
                "require_live_evidence": True,
                "require_entity_qualification": True,
                "mission_id": "mis_entity",
                "plan_version": "v1",
                "milestone_id": "m1",
                "decision_id": "dec_entity",
                "materialization_id": "mat_entity",
                "execution_request_id": "mer_entity",
                "delegation_id": "del_entity",
                "worker_claim_id": "wcl_entity",
            },
        ))
        return result

    async def test_valid_live_external_qualified_entity_verifies_lead_artifact(self):
        result = await self._run_qualified_lead_agent({
            "title": "Buka Restaurant",
            "url": "https://bukarestaurant.com/menu",
            "snippet": "A restaurant serving Ghanaian food in Accra.",
            "source_provider": "fixture",
        })

        self.assertTrue(result.success)
        artifact = result.artifacts[0]
        self.assertTrue(artifact["verified"])
        self.assertEqual(EvidenceSource.LIVE_EXTERNAL.value, artifact["evidence_source"])
        self.assertFalse(artifact["simulation_evidence"])
        self.assertTrue(artifact["entity_qualification"]["qualified"])
        self.assertEqual("OFFICIAL_BUSINESS_SITE", artifact["source_type"])
        self.assertEqual("wcl_entity", artifact["provenance"]["worker_claim_id"])

    async def test_unqualified_live_external_result_does_not_verify_lead_artifact(self):
        result = await self._run_qualified_lead_agent({
            "title": "Restaurant Industry Market Report 2026",
            "url": "https://research.example.com/report/restaurant-industry-2026",
            "snippet": "Research report and industry forecast.",
            "source_provider": "fixture",
        })

        self.assertFalse(result.success)
        self.assertIn("BUSINESS_ENTITY_UNVERIFIED", result.errors)
        self.assertEqual([], result.artifacts)
        discovery = result.tool_calls[0]["output"]
        self.assertEqual(EvidenceSource.LIVE_EXTERNAL.value, discovery["evidence_source"])
        self.assertFalse(discovery["results"][0]["entity_qualification"]["qualified"])
        self.assertEqual("REPORT", discovery["results"][0]["source_type"])

    async def test_qualified_canary_missing_scope_fails_closed_before_tool_call(self):
        calls = []

        async def fake_invoke_tool(tool_name, parameters, context):
            calls.append((tool_name, parameters))
            raise AssertionError("provider tool must not run without explicit scope")

        agent = LeadIntelligenceAgent()
        agent.invoke_tool = fake_invoke_tool
        result = await agent.execute(AgentContext(
            task="Research one qualified business prospect",
            session_id="missing_scope_fixture",
            planner_output={
                "query": "Research 1 qualified live business prospect for LeadArtifact production",
                "location": "Global",
                "target_count": 1,
                "verification_mode": "QUALIFIED_LEAD_CANARY",
                "require_live_evidence": True,
                "require_entity_qualification": True,
            },
        ))

        self.assertFalse(result.success)
        self.assertEqual(["DISCOVERY_SCOPE_INCOMPLETE"], result.errors)
        self.assertEqual([], result.tool_calls)
        self.assertEqual([], calls)


if __name__ == "__main__":
    unittest.main()
