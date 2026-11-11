import asyncio
import logging
from typing import Any, Dict, List

from pydantic import BaseModel, Field

from app.tools.base_tool import BaseTool
from app.tools.plugins.search.search_providers import (
    BraveSearchProvider,
    DuckDuckGoProvider,
)

logger = logging.getLogger(__name__)


class WebSearchInput(BaseModel):
    query: str = Field(..., description="Search query to run")
    max_results: int = Field(default=5, ge=1, le=10)


class WebSearchOutput(BaseModel):
    results: List[Dict[str, Any]] = Field(default_factory=list)
    query: str = ""


class WebIntelligenceTool(BaseTool):
    name = "web_search"
    description = "Searches the web with DuckDuckGo and Brave when configured, returning source URLs and snippets."
    input_schema = WebSearchInput
    output_schema = WebSearchOutput
    capabilities = ["web_search"]
    permissions = ["network_outbound"]
    timeout = 30.0

    def __init__(self):
        self._providers = [DuckDuckGoProvider(), BraveSearchProvider()]

    async def execute(self, query: str, max_results: int = 5, **kwargs) -> Dict[str, Any]:
        query = str(query or "").strip()
        if not query:
            return {"results": [], "query": query}

        provider_results = await asyncio.gather(
            *(provider.search(query, max_results=max_results) for provider in self._providers),
            return_exceptions=True,
        )

        results: List[Dict[str, Any]] = []
        seen_urls = set()
        for provider_result in provider_results:
            if isinstance(provider_result, Exception):
                logger.warning("[WebIntelligenceTool] Search provider failed: %s", provider_result)
                continue
            for result in provider_result:
                item = result.model_dump() if hasattr(result, "model_dump") else dict(result)
                url = item.get("url")
                if url and url not in seen_urls:
                    seen_urls.add(url)
                    results.append(item)
                if len(results) >= max_results:
                    break
            if len(results) >= max_results:
                break

        return {"results": results[:max_results], "query": query}
