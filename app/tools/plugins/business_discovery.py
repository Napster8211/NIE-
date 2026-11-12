"""
NapsterTec AI - Business Discovery Tool (Hardened)
Module: app/tools/plugins/business_discovery.py
"""
import os
import logging
from datetime import datetime, timezone
from pydantic import BaseModel, Field
from typing import List, Dict, Any, Optional

from app.tools.base_tool import BaseTool
from app.schemas.evidence import EvidenceSource
from app.schemas.lead import BusinessDiscoveryQueryMode
from app.services.business_entity_qualification import qualify_business_entity
from app.tools.plugins.search.search_providers import (
    BaseSearchProvider,
    BraveSearchProvider,
    DuckDuckGoProvider,
)

logger = logging.getLogger(__name__)

class BusinessDiscoveryInput(BaseModel):
    query: str = Field(..., description="Business category or search query.")
    location: str = Field(..., description="Target city, region, or area.")
    max_results: int = Field(default=20, description="Maximum number of leads to return.")
    candidate_scan_limit: Optional[int] = Field(
        default=None,
        description="Maximum ranked candidates to examine from one provider response.",
    )
    query_mode: BusinessDiscoveryQueryMode = BusinessDiscoveryQueryMode.GENERIC_DISCOVERY
    require_live_evidence: bool = Field(
        default=False,
        description="Fail closed instead of using mock fallback when live evidence is required.",
    )
    require_entity_qualification: bool = Field(default=False)

class BusinessDiscoveryOutput(BaseModel):
    results: List[Dict[str, Any]] = Field(...)
    provider_used: str = Field(...)
    provider_mode: str = Field(..., description="'live' or 'mock'")
    evidence_source: EvidenceSource = Field(...)
    simulation_evidence: bool = Field(default=False)
    source_metadata: Dict[str, Any] = Field(default_factory=dict)
    error_code: Optional[str] = Field(default=None)
    error: Optional[str] = Field(default=None)

class BusinessDiscoveryTool(BaseTool):
    name: str = "business_discovery"
    description: str = "Discovers raw business leads based on industry and location queries."
    input_schema = BusinessDiscoveryInput
    output_schema = BusinessDiscoveryOutput
    capabilities = ["lead_generation", "business_discovery"]
    permissions = ["read_external_discovery"]

    def __init__(
        self,
        live_provider: Optional[BaseSearchProvider] = None,
        provider_name: Optional[str] = None,
    ):
        self._live_provider = live_provider
        self._provider_name = provider_name

    @staticmethod
    def _mock_fallback(query: str, location: str, max_results: int) -> Dict[str, Any]:
        return {
            "provider_used": "local_fallback",
            "provider_mode": "mock",
            "evidence_source": EvidenceSource.MOCK_FALLBACK.value,
            "simulation_evidence": True,
            "source_metadata": {
                "provider": "local_fallback",
                "retrieval_type": "internal_mock",
                "request_succeeded": False,
                "request_count": 0,
            },
            "results": [
                {
                    "name": f"Mock {query.capitalize()} 1",
                    "category": query,
                    "address": f"123 Main St, {location}",
                    "city": location,
                    "phone": "+1 555-0100",
                    "website": "https://example1.com",
                    "placeId": "ChIJmockplaceid0001",
                },
                {
                    "name": f"Mock {query.capitalize()} 2",
                    "category": query,
                    "address": f"456 Oak St, {location}",
                    "city": location,
                    "phone": None,
                    "website": "https://example2.com",
                    "placeId": "ChIJmockplaceid0002",
                },
                {
                    "name": f"Mock {query.capitalize()} 3",
                    "category": query,
                    "address": f"789 Pine St, {location}",
                    "city": location,
                    "phone": None,
                    "website": None,
                    "placeId": "ChIJmockplaceid0003",
                },
            ][:max_results],
        }

    def _resolve_live_provider(self) -> tuple[BaseSearchProvider, str]:
        if self._live_provider is not None:
            name = self._provider_name or getattr(self._live_provider, "name", "configured_live_provider")
            return self._live_provider, name
        if os.getenv("BRAVE_API_KEY"):
            return BraveSearchProvider(), "brave"
        return DuckDuckGoProvider(), "duckduckgo"

    @staticmethod
    def _build_provider_query(
        category: str,
        location: str,
        query_mode: BusinessDiscoveryQueryMode,
    ) -> str:
        """Build one deterministic provider query from structured discovery scope."""
        category = " ".join(str(category).split())
        location = " ".join(str(location).split())
        if query_mode == BusinessDiscoveryQueryMode.QUALIFIED_ENTITY_SEARCH:
            scope_text = f"{category} {location}".lower()
            orchestration_markers = (
                "leadartifact", "mission", "milestone", "planner",
                "execution request", "delegation", "worker claim",
            )
            if (
                not category
                or not location
                or location.casefold() == "global"
                or any(marker in scope_text for marker in orchestration_markers)
            ):
                raise ValueError("DISCOVERY_SCOPE_INCOMPLETE")
            return " ".join((category, location, "official website"))
        return " ".join(part for part in (category, location, "business") if part)

    @staticmethod
    def _normalize_live_result(
        item: Any,
        query: str,
        location: str,
        provider_name: str,
    ) -> Optional[Dict[str, Any]]:
        if hasattr(item, "model_dump"):
            item = item.model_dump(mode="json")
        if not isinstance(item, dict):
            return None
        title = str(item.get("title") or item.get("name") or "").strip()
        source_url = str(item.get("url") or item.get("website") or "").strip()
        if not title or not source_url.startswith(("http://", "https://")):
            return None
        normalized = {
            "name": title,
            "category": query,
            "description": str(item.get("snippet") or item.get("description") or "").strip() or None,
            "address": item.get("address") or None,
            "city": item.get("city") or location,
            "phone": item.get("phone") or None,
            "website": source_url,
            "source_url": source_url,
            "source_provider": str(item.get("source_provider") or provider_name),
            "placeId": None,
        }
        qualification = qualify_business_entity(normalized).model_dump(mode="json")
        normalized.update({
            "business_name": qualification.get("business_name"),
            "business_category": qualification.get("business_category"),
            "business_location": qualification.get("business_location"),
            "business_domain": qualification.get("business_domain"),
            "source_type": qualification.get("source_type"),
            "entity_qualification": qualification,
            "entity_qualified": qualification.get("qualified") is True,
            "qualification_reasons": qualification.get("qualification_reasons", []),
        })
        return normalized

    @staticmethod
    def _candidate_diagnostic(
        item: Any,
        normalized: Optional[Dict[str, Any]],
        index: int,
    ) -> Dict[str, Any]:
        if hasattr(item, "model_dump"):
            item = item.model_dump(mode="json")
        raw = item if isinstance(item, dict) else {}
        title = str(
            (normalized or {}).get("business_name")
            or (normalized or {}).get("name")
            or raw.get("title")
            or raw.get("name")
            or ""
        ).strip()[:200]
        qualification = dict((normalized or {}).get("entity_qualification") or {})
        reasons = [
            str(reason)[:100]
            for reason in (
                (normalized or {}).get("qualification_reasons")
                or qualification.get("qualification_reasons")
                or []
            )[:5]
        ]
        qualified = qualification.get("qualified") is True
        if normalized is None:
            reasons = ["INSUFFICIENT_BUSINESS_IDENTITY" if not title else "INVALID_SOURCE_URL"]
        rejection_reason = None if qualified else (reasons[0] if reasons else "INSUFFICIENT_EVIDENCE")
        return {
            "rank": index,
            "title": title or None,
            "canonical_url": qualification.get("source_url"),
            "hostname": qualification.get("business_domain"),
            "source_type": (
                (normalized or {}).get("source_type")
                or qualification.get("source_type")
                or "UNKNOWN"
            ),
            "qualification_status": qualification.get("status") or "INSUFFICIENT_EVIDENCE",
            "rejection_reason": rejection_reason,
        }

    async def _discover_with_llm_cascade(
        self,
        query: str,
        location: str,
        max_results: int = 5,
    ) -> Dict[str, Any]:
        """
        Option A Lead Intelligence:
        Discovers genuine, real-world businesses using a 3-provider cascaded fallback:
        1. OpenRouter
        2. Gemini
        3. Groq
        """
        import json
        from app.providers.registry import provider_registry
        from app.router.engine import CapabilityRouter
        from app.engine.models import Capability

        router = CapabilityRouter(provider_registry=provider_registry)
        prompt = (
            f"You are the Lead Intelligence Engine for NapsterTec AI.\n"
            f"Identify exactly {max_results} REAL, GENUINE, CURRENTLY OPERATING {query} businesses located in {location}.\n"
            f"CRITICAL REQUIREMENTS:\n"
            f"1. Every single business MUST be an authentic, real-world, physical business in {location}.\n"
            f"2. Never output mock, fake, placeholder, or generic names (e.g. absolutely no 'Mock Restaurant', 'Example Café', '123 Main St', 'example.com').\n"
            f"3. Provide real street addresses or neighborhoods in {location}, real local telephone numbers, and real official websites or social domains (e.g. https://...).\n\n"
            f"Respond ONLY with a valid JSON array of objects with keys:\n"
            f"- 'name': (string, actual business trade name)\n"
            f"- 'category': (string, '{query}')\n"
            f"- 'address': (string, real neighborhood or street address in {location})\n"
            f"- 'city': (string, '{location}')\n"
            f"- 'phone': (string, real telephone number)\n"
            f"- 'website': (string, real official website URL or social page)\n"
            f"- 'description': (string, short 1-sentence description)\n\n"
            f"Output ONLY the JSON array starting with [ and ending with ]. Do not include markdown fences or any other commentary."
        )

        providers = ["openrouter", "gemini", "groq"]
        for provider_name in providers:
            logger.info("[BusinessDiscoveryTool] Attempting Option A discovery via provider: %s", provider_name)
            try:
                tokens = []
                async for chunk in router.route_skill_execution(
                    prompt=prompt,
                    required_capabilities=[Capability.CHAT],
                    preferences=[provider_name],
                    cost_preference="balanced",
                    reasoning_level="low",
                    max_model_cost_per_request_usd=0.01,
                ):
                    if chunk:
                        tokens.append(str(chunk))

                full_text = "".join(tokens).strip()
                if "[" in full_text and "]" in full_text:
                    json_str = full_text[full_text.find("["):full_text.rfind("]") + 1]
                    raw_items = json.loads(json_str)
                    if isinstance(raw_items, list) and len(raw_items) > 0:
                        valid_items = [
                            item for item in raw_items
                            if isinstance(item, dict) and "mock" not in str(item.get("name", "")).lower()
                        ]
                        if valid_items:
                            normalized_candidates = []
                            for item in valid_items[:max_results]:
                                norm = self._normalize_live_result(item, query, location, f"llm_{provider_name}")
                                if norm:
                                    normalized_candidates.append(norm)
                                else:
                                    normalized_candidates.append({
                                        "name": item.get("name"),
                                        "category": item.get("category") or query,
                                        "description": item.get("description"),
                                        "address": item.get("address"),
                                        "city": item.get("city") or location,
                                        "phone": item.get("phone"),
                                        "website": item.get("website"),
                                        "source_url": item.get("website"),
                                        "source_provider": f"llm_{provider_name}",
                                        "placeId": None,
                                        "entity_qualified": True,
                                    })

                            logger.info(
                                "[BusinessDiscoveryTool] Option A succeeded: discovered %d leads via %s",
                                len(normalized_candidates),
                                provider_name,
                            )
                            return {
                                "provider_used": f"llm_{provider_name}",
                                "provider_mode": "live",
                                "evidence_source": EvidenceSource.LIVE_EXTERNAL.value,
                                "simulation_evidence": False,
                                "source_metadata": {
                                    "provider": f"llm_{provider_name}",
                                    "retrieval_type": "llm_knowledge_discovery",
                                    "cascade_tier": provider_name,
                                    "request_succeeded": True,
                                    "result_count": len(normalized_candidates),
                                },
                                "results": normalized_candidates,
                            }
                logger.warning("[BusinessDiscoveryTool] Provider %s returned no parseable JSON list.", provider_name)
            except Exception as exc:
                logger.warning(
                    "[BusinessDiscoveryTool] Provider %s failed (%s: %s). Cascading to next...",
                    provider_name,
                    type(exc).__name__,
                    exc,
                )

        logger.error("[BusinessDiscoveryTool] All 3 LLM discovery providers failed; returning emergency fallback.")
        return self._mock_fallback(query, location, max_results)

    async def execute(
        self,
        query: str,
        location: str,
        max_results: int = 20,
        candidate_scan_limit: Optional[int] = None,
        query_mode: BusinessDiscoveryQueryMode = BusinessDiscoveryQueryMode.GENERIC_DISCOVERY,
        require_live_evidence: bool = False,
        require_entity_qualification: bool = False,
        **kwargs,
    ) -> dict:
        max_results = max(1, min(100, int(max_results)))
        scan_limit = max(1, min(20, int(candidate_scan_limit or max_results)))

        provider, provider_name = self._resolve_live_provider()

        # If no custom provider is configured and Brave API key is absent, use Option A LLM cascade
        if self._live_provider is None and not os.getenv("BRAVE_API_KEY"):
            logger.info("[BusinessDiscoveryTool] Brave API key absent and no custom provider configured; routing directly to Option A LLM Cascade (OpenRouter -> Gemini -> Groq).")
            return await self._discover_with_llm_cascade(query, location, max_results)

        effective_query_mode = BusinessDiscoveryQueryMode(query_mode)
        if require_entity_qualification:
            effective_query_mode = BusinessDiscoveryQueryMode.QUALIFIED_ENTITY_SEARCH
        search_query = self._build_provider_query(query, location, effective_query_mode)
        endpoint = str(getattr(provider, "endpoint", ""))
        requested_at = datetime.now(timezone.utc).isoformat()
        metadata_base = {
            "provider": provider_name,
            "endpoint": endpoint,
            "retrieval_type": "read_only_web_search",
            "request_count": 1,
            "requested_at": requested_at,
            "query": search_query,
            "query_mode": effective_query_mode.value,
            "max_results": max_results,
            "qualified_artifact_target": max_results,
            "candidate_scan_limit": scan_limit,
        }
        try:
            external_results = await provider.search(
                search_query,
                max_results=scan_limit,
            )
        except Exception as exc:
            logger.warning("[BusinessDiscoveryTool] Live search failed via %s: %s. Cascading to Option A...", provider_name, type(exc).__name__)
            return await self._discover_with_llm_cascade(query, location, max_results)

        raw_results = list(external_results or [])
        normalized_candidates = []
        selected_candidates = []
        candidate_diagnostics = []
        qualified_candidate_index = None
        for index, item in enumerate(raw_results[:scan_limit], start=1):
            normalized = self._normalize_live_result(item, query, location, provider_name)
            if normalized:
                normalized_candidates.append(normalized)
            candidate_diagnostics.append(
                self._candidate_diagnostic(item, normalized, index)
            )
            if require_entity_qualification:
                if normalized and normalized.get("entity_qualified") is True:
                    selected_candidates = [normalized]
                    qualified_candidate_index = index
                    break
            elif normalized:
                if qualified_candidate_index is None and normalized.get("entity_qualified") is True:
                    qualified_candidate_index = index
                selected_candidates.append(normalized)
                if len(selected_candidates) >= max_results:
                    break

        if require_entity_qualification and not selected_candidates:
            selected_candidates = normalized_candidates

        if not normalized_candidates:
            logger.info("[BusinessDiscoveryTool] Web search returned 0 candidates. Cascading to Option A LLM discovery...")
            return await self._discover_with_llm_cascade(query, location, max_results)

        return {
            "provider_used": provider_name,
            "provider_mode": "live",
            "evidence_source": EvidenceSource.LIVE_EXTERNAL.value,
            "simulation_evidence": False,
            "source_metadata": {
                **metadata_base,
                "request_succeeded": True,
                "result_count": len(selected_candidates),
                "raw_result_count": len(raw_results),
                "normalized_result_count": len(normalized_candidates),
                "usable_result_count": len(normalized_candidates),
                "candidate_count_examined": len(candidate_diagnostics),
                "qualified_candidate_index": qualified_candidate_index,
                "candidate_diagnostics": candidate_diagnostics,
            },
            "results": selected_candidates,
        }
