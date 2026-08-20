import unittest
from unittest.mock import patch
from urllib.parse import quote

from app.schemas.evidence import EvidenceSource
from app.tools.plugins.business_discovery import BusinessDiscoveryTool
from app.tools.plugins.search import search_providers
from app.tools.plugins.search.search_providers import (
    DuckDuckGoProvider,
    _normalize_result_url,
)


CANONICAL_DESTINATION = "https://example.com/business"
ENCODED_DESTINATION = quote(CANONICAL_DESTINATION, safe="")
DUCKDUCKGO_HTML_FIXTURE = f"""
<!doctype html>
<html>
  <body>
    <div class="result results_links results_links_deep web-result">
      <div class="result__body">
        <h2 class="result__title">
          <a class="result__a" href="//duckduckgo.com/l/?uddg={ENCODED_DESTINATION}&amp;rut=safe-fixture">
            Example Restaurant Accra
          </a>
        </h2>
        <a class="result__snippet">A deterministic business discovery fixture.</a>
      </div>
    </div>
  </body>
</html>
"""


class _FixtureResponse:
    status_code = 200
    text = DUCKDUCKGO_HTML_FIXTURE

    @staticmethod
    def raise_for_status():
        return None


class _FixtureAsyncClient:
    all_get_calls = []

    def __init__(self, *args, **kwargs):
        self.get_calls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def get(self, url, *args, **kwargs):
        self.get_calls.append((url, args, kwargs))
        type(self).all_get_calls.append((url, args, kwargs))
        return _FixtureResponse()


class _InvalidLiveProvider:
    name = "duckduckgo"
    endpoint = "https://html.duckduckgo.com/html/"

    async def search(self, query, max_results=10):
        return [{
            "title": "Unsafe Business Result",
            "url": "javascript:alert(1)",
            "snippet": "Must remain fail-closed.",
            "source_provider": "duckduckgo",
        }]


class DuckDuckGoUrlNormalizationTests(unittest.IsolatedAsyncioTestCase):
    def test_absolute_http_and_https_urls_are_unchanged(self):
        for url in ("http://example.com/business", "https://example.com/business"):
            with self.subTest(url=url):
                self.assertEqual(url, _normalize_result_url(url))

    def test_protocol_relative_duckduckgo_redirect_extracts_destination(self):
        url = f"//duckduckgo.com/l/?uddg={CANONICAL_DESTINATION}"
        self.assertEqual(CANONICAL_DESTINATION, _normalize_result_url(url))

    def test_encoded_uddg_destination_is_decoded(self):
        destination = "https://example.com/business?city=Accra&kind=restaurant"
        url = f"https://duckduckgo.com/l/?uddg={quote(destination, safe='')}"
        self.assertEqual(destination, _normalize_result_url(url))

    def test_relative_duckduckgo_redirect_extracts_destination(self):
        url = f"/l/?uddg={ENCODED_DESTINATION}"
        self.assertEqual(CANONICAL_DESTINATION, _normalize_result_url(url))

    def test_unsafe_schemes_are_rejected(self):
        for url in (
            "javascript:alert(1)",
            "data:text/html,unsafe",
            "file:///etc/passwd",
            "//duckduckgo.com/l/?uddg=javascript%3Aalert%281%29",
        ):
            with self.subTest(url=url):
                self.assertIsNone(_normalize_result_url(url))

    def test_malformed_destinations_are_rejected(self):
        for url in (
            "",
            "https://",
            "https://exa mple.com/business",
            "https://example.com/%ZZ",
            "//duckduckgo.com/l/?uddg=not-a-url",
            "/l/?uddg=https%3A%2F%2F",
        ):
            with self.subTest(url=url):
                self.assertIsNone(_normalize_result_url(url))

    async def test_realistic_html_fixture_returns_canonical_search_result(self):
        with patch.object(search_providers.httpx, "AsyncClient", _FixtureAsyncClient):
            results = await DuckDuckGoProvider().search("restaurant Accra Ghana business", max_results=1)

        self.assertEqual(1, len(results))
        self.assertEqual("Example Restaurant Accra", results[0].title)
        self.assertEqual(CANONICAL_DESTINATION, results[0].url)
        self.assertEqual("A deterministic business discovery fixture.", results[0].snippet)
        self.assertEqual("duckduckgo", results[0].source_provider)

    async def test_business_discovery_accepts_canonical_duckduckgo_result(self):
        tool = BusinessDiscoveryTool(DuckDuckGoProvider(), "duckduckgo")
        _FixtureAsyncClient.all_get_calls = []
        with patch.object(search_providers.httpx, "AsyncClient", _FixtureAsyncClient):
            discovery = await tool.execute(
                query="restaurant",
                location="Accra Ghana",
                max_results=1,
                require_live_evidence=True,
            )

        self.assertEqual(EvidenceSource.LIVE_EXTERNAL.value, discovery["evidence_source"])
        self.assertEqual("live", discovery["provider_mode"])
        self.assertFalse(discovery["simulation_evidence"])
        metadata = discovery["source_metadata"]
        self.assertTrue(metadata["request_succeeded"])
        self.assertEqual("restaurant Accra Ghana business", metadata["query"])
        self.assertEqual(1, metadata["max_results"])
        self.assertEqual(1, metadata["candidate_scan_limit"])
        self.assertEqual(1, metadata["candidate_count_examined"])
        self.assertEqual(1, metadata["qualified_candidate_index"])
        self.assertEqual(1, metadata["raw_result_count"])
        self.assertEqual(1, metadata["normalized_result_count"])
        self.assertEqual(1, metadata["usable_result_count"])
        self.assertEqual(1, len(_FixtureAsyncClient.all_get_calls))
        _, _, request_kwargs = _FixtureAsyncClient.all_get_calls[0]
        self.assertEqual("restaurant Accra Ghana business", request_kwargs["params"]["q"])
        self.assertNotIn("LeadArtifact production", request_kwargs["params"]["q"])
        self.assertEqual(CANONICAL_DESTINATION, discovery["results"][0]["source_url"])
        self.assertEqual("duckduckgo", discovery["results"][0]["source_provider"])

    async def test_invalid_live_result_remains_fail_closed(self):
        discovery = await BusinessDiscoveryTool(
            _InvalidLiveProvider(),
            "duckduckgo",
        ).execute(
            query="restaurant",
            location="Accra Ghana",
            max_results=1,
            require_live_evidence=True,
        )

        self.assertEqual(EvidenceSource.UNKNOWN.value, discovery["evidence_source"])
        self.assertEqual("LIVE_EVIDENCE_UNAVAILABLE", discovery["error_code"])
        self.assertEqual([], discovery["results"])
        metadata = discovery["source_metadata"]
        self.assertTrue(metadata["request_succeeded"])
        self.assertEqual("restaurant Accra Ghana business", metadata["query"])
        self.assertEqual(1, metadata["raw_result_count"])
        self.assertEqual(0, metadata["normalized_result_count"])
        self.assertEqual(0, metadata["usable_result_count"])
        self.assertEqual(1, metadata["candidate_count_examined"])
        self.assertEqual("INVALID_SOURCE_URL", metadata["candidate_diagnostics"][0]["rejection_reason"])


if __name__ == "__main__":
    unittest.main()
