import asyncio
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from app.tools.base_tool import BaseTool
from app.tools.plugins.search.search_providers import (
    BraveSearchProvider, 
    DuckDuckGoProvider
)
from app.tools.plugins.search.ranking_engine import RankingEngine
from app.tools.plugins.search.search_cache import SearchCache
from app.tools.plugins.url_reader import URLReaderTool

class WebSearchInput(BaseModel):
    query: str = Field(..., description="The search query.")
    providers: List[str] = Field(default=["brave", "duckduckgo"], description="List of providers to query concurrently.")
    max_results: int = Field(default=10, description="Total results to fetch before ranking.")
    prioritize_news: bool = Field(default=False, description="Whether to bump recent articles in ranking.")
    deep_read_top_n: int = Field(default=0, description="If > 0, downloads and extracts full markdown content of the top N ranked URLs.")

class WebSearchOutput(BaseModel):
    query: str
    ranked_results: List[Dict[str, Any]] = Field(..., description="Deduplicated and ranked search results.")
    citations: Dict[str, str] = Field(..., description="Mapping of citation IDs [1] to URLs.")
    deep_read_content: Optional[Dict[str, str]] = Field(default=None, description="Full markdown content mapped by URL if deep_read_top_n > 0.")

class WebIntelligenceTool(BaseTool):
    """
    NapsterTec Web Intelligence Engine.
    Executes concurrent multi-provider searches, ranks results, and optionally deep-reads top pages.
    """
    
    def __init__(self):
        self.cache = SearchCache()
        self.ranking_engine = RankingEngine()
        self.url_reader = URLReaderTool()
        
        # Initialize active providers
        self._providers = {
            "brave": BraveSearchProvider(),
            "duckduckgo": DuckDuckGoProvider(),
            # "google": GoogleCustomSearchProvider(),
            # "tavily": TavilyProvider()
        }

    @property
    def name(self) -> str:
        return "web_intelligence"

    @property
    def description(self) -> str:
        return "Searches the web across multiple engines, ranks results by confidence, and retrieves full page content for deep research."

    @property
    def capabilities(self) -> List[str]:
        return ["search", "ranking", "data-extraction"]

    @property
    def input_schema(self) -> type[BaseModel]:
        return WebSearchInput

    @property
    def output_schema(self) -> type[BaseModel]:
        return WebSearchOutput

    async def execute(self, **kwargs) -> Dict[str, Any]:
        query = kwargs["query"]
        requested_providers = kwargs.get("providers", ["duckduckgo"])
        deep_read = kwargs.get("deep_read_top_n", 0)
        
        # 1. Check Cache
        cached = await self.cache.get("multi", query, deep_read=deep_read)
        if cached:
            return cached

        # 2. Concurrent Multi-Provider Search
        tasks = []
        for p_name in requested_providers:
            if p_name in self._providers:
                tasks.append(self._providers[p_name].search(query, kwargs.get("max_results", 10)))
        
        raw_results_arrays = await asyncio.gather(*tasks, return_exceptions=True)
        
        # Flatten results, ignoring provider failures
        all_results = []
        for res in raw_results_arrays:
            if isinstance(res, list):
                all_results.extend(res)

        # 3. Rank & Deduplicate
        ranked_results = self.ranking_engine.rank_and_deduplicate(
            all_results, 
            query, 
            prioritize_freshness=kwargs.get("prioritize_news", False)
        )

        # 4. Generate Citations map (e.g., {"[1]": "https://github.com/..."})
        citations = {}
        for idx, item in enumerate(ranked_results, 1):
            citation_key = f"[{idx}]"
            item["citation"] = citation_key
            citations[citation_key] = item["url"]

        # 5. Optional Deep Reading (Sprint 3 Integration)
        deep_read_content = {}
        if deep_read > 0 and ranked_results:
            top_urls = [res["url"] for res in ranked_results[:deep_read]]
            read_tasks = [self.url_reader.execute(url=u) for u in top_urls]
            read_results = await asyncio.gather(*read_tasks, return_exceptions=True)
            
            for url, content_res in zip(top_urls, read_results):
                if not isinstance(content_res, Exception):
                    deep_read_content[url] = content_res.get("content", "Extraction failed.")

        final_output = {
            "query": query,
            "ranked_results": ranked_results,
            "citations": citations,
            "deep_read_content": deep_read_content if deep_read_content else None
        }

        # Cache and return
        await self.cache.set("multi", query, final_output, deep_read=deep_read)
        return final_output