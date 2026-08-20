import re
from urllib.parse import urlparse
from typing import List, Dict
from app.tools.plugins.search.search_providers import SearchResult

class RankingEngine:
    def __init__(self):
        # Domain reputation mapping
        self.high_authority_tlds = {".edu": 1.5, ".gov": 1.5, ".org": 1.2}
        self.trusted_domains = ["github.com", "stackoverflow.com", "bloomberg.com", "reuters.com", "arxiv.org"]
        self.low_quality_domains = ["quora.com", "pinterest.com", "yahoo.answers.com"]

    def rank_and_deduplicate(self, results: List[SearchResult], query: str, prioritize_freshness: bool = False) -> List[Dict]:
        unique_results = {}
        
        # 1. Deduplicate by URL
        for res in results:
            clean_url = res.url.split("#")[0].split("?utm")[0]
            if clean_url not in unique_results:
                unique_results[clean_url] = res

        ranked_list = []
        for url, res in unique_results.items():
            score = self._calculate_score(res, query, prioritize_freshness)
            ranked_list.append({
                "title": res.title,
                "url": url,
                "snippet": res.snippet,
                "provider": res.source_provider,
                "confidence_score": round(score, 2),
                "published_date": res.published_date
            })

        # Sort descending by confidence score
        ranked_list.sort(key=lambda x: x["confidence_score"], reverse=True)
        return ranked_list

    def _calculate_score(self, result: SearchResult, query: str, freshness: bool) -> float:
        score = 10.0
        parsed_url = urlparse(result.url)
        domain = parsed_url.netloc.lower()

        # Domain Authority Scoring
        for tld, multiplier in self.high_authority_tlds.items():
            if domain.endswith(tld):
                score *= multiplier

        if any(td in domain for td in self.trusted_domains):
            score += 3.0
        if any(lq in domain for lq in self.low_quality_domains):
            score -= 5.0

        # Keyword Relevance in Title
        query_terms = set(re.findall(r'\w+', query.lower()))
        title_terms = set(re.findall(r'\w+', result.title.lower()))
        overlap = len(query_terms.intersection(title_terms))
        score += (overlap * 0.5)

        # Freshness Boost (If date is provided and freshness is required)
        if freshness and result.published_date:
            # Simplified: In production, parse ISO dates and calculate timedelta
            score += 2.0 

        return max(0.1, score) # Ensure positive score