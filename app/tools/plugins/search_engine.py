import json
import logging
import re
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field

from app.tools.base_tool import BaseTool

logger = logging.getLogger(__name__)


class WebSearchInput(BaseModel):
    query: str = Field(..., description="Search query to run")
    max_results: int = Field(default=5, ge=1, le=10)


class WebSearchOutput(BaseModel):
    results: List[Dict[str, Any]] = Field(default_factory=list)
    query: str = ""


class WebIntelligenceTool(BaseTool):
    name: str = "web_search"
    description: str = "Performs web search using DuckDuckGo and normalizes the results for tool use."
    input_schema = WebSearchInput
    output_schema = WebSearchOutput
    capabilities = ["web_search"]
    permissions = ["network_outbound"]

    def __init__(self):
        self._providers = []

    async def execute(self, query: str, max_results: int = 5, **kwargs) -> Dict[str, Any]:
        # Additional alias safety for callers that still refer to web_intelligence by name.
        if not query or not str(query).strip():
            return {"results": [], "query": ""}

        from app.tools.plugins.search.search_providers import search_web

        results = await search_web(str(query).strip(), max_results=max_results)
        return {
            "query": str(query).strip(),
            "results": results,
        }

    async def execute_stream(self, **kwargs):
        raise NotImplementedError("WebIntelligenceTool does not stream results.")
