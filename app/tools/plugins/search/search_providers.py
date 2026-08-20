import os
import ipaddress
import re
import httpx
from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from urllib.parse import parse_qs, urljoin, urlsplit
from pydantic import BaseModel, Field


_DUCKDUCKGO_BASE_URL = "https://duckduckgo.com"
_HOST_LABEL_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?$")
_INVALID_PERCENT_ESCAPE = re.compile(r"%(?![0-9A-Fa-f]{2})")


def _has_valid_hostname(hostname: Optional[str]) -> bool:
    if not hostname:
        return False
    candidate = hostname.rstrip(".")
    if not candidate:
        return False
    try:
        ipaddress.ip_address(candidate)
        return True
    except ValueError:
        pass
    try:
        ascii_hostname = candidate.encode("idna").decode("ascii")
    except UnicodeError:
        return False
    if len(ascii_hostname) > 253:
        return False
    return all(
        _HOST_LABEL_PATTERN.fullmatch(label) is not None
        for label in ascii_hostname.split(".")
    )


def _is_safe_absolute_http_url(value: str) -> bool:
    if not value or any(character.isspace() or ord(character) < 32 for character in value):
        return False
    if "\\" in value or _INVALID_PERCENT_ESCAPE.search(value):
        return False
    try:
        parsed = urlsplit(value)
        parsed.port
    except (TypeError, ValueError):
        return False
    if parsed.scheme.lower() not in {"http", "https"}:
        return False
    if not parsed.netloc or not _has_valid_hostname(parsed.hostname):
        return False
    if parsed.username is not None or parsed.password is not None:
        return False
    return True


def _is_duckduckgo_hostname(hostname: Optional[str]) -> bool:
    normalized = (hostname or "").rstrip(".").lower()
    return normalized == "duckduckgo.com" or normalized.endswith(".duckduckgo.com")


def _normalize_result_url(raw_url: Any) -> Optional[str]:
    """Return a safe, absolute result URL, unwrapping DuckDuckGo redirects."""
    if not isinstance(raw_url, str) or not raw_url:
        return None
    if raw_url != raw_url.strip():
        return None

    was_relative = raw_url.startswith("/") and not raw_url.startswith("//")
    candidate = urljoin(_DUCKDUCKGO_BASE_URL, raw_url) if raw_url.startswith("/") else raw_url
    try:
        parsed = urlsplit(candidate)
    except (TypeError, ValueError):
        return None

    if _is_duckduckgo_hostname(parsed.hostname):
        redirect_values = parse_qs(parsed.query, keep_blank_values=True).get("uddg", [])
        if redirect_values:
            destination = redirect_values[0]
            return destination if _is_safe_absolute_http_url(destination) else None
        if was_relative or parsed.path.rstrip("/") == "/l":
            return None

    return candidate if _is_safe_absolute_http_url(candidate) else None

class SearchResult(BaseModel):
    title: str
    url: str
    snippet: str
    published_date: Optional[str] = None
    source_provider: str
    reputation_hint: Optional[str] = None

class BaseSearchProvider(ABC):
    name: str = "unknown"
    endpoint: str = ""
    credential_variables: tuple[str, ...] = ()

    @abstractmethod
    async def search(self, query: str, max_results: int = 10) -> List[SearchResult]:
        pass

class BraveSearchProvider(BaseSearchProvider):
    name = "brave"
    endpoint = "https://api.search.brave.com/res/v1/web/search"
    credential_variables = ("BRAVE_API_KEY",)

    async def search(self, query: str, max_results: int = 10) -> List[SearchResult]:
        api_key = os.getenv("BRAVE_API_KEY")
        if not api_key: return []
        
        headers = {"Accept": "application/json", "X-Subscription-Token": api_key}
        async with httpx.AsyncClient() as client:
            response = await client.get(
                self.endpoint,
                params={"q": query, "count": max_results},
                headers=headers
            )
            response.raise_for_status()
            data = response.json()
            
            results = []
            for item in data.get("web", {}).get("results", []):
                results.append(SearchResult(
                    title=item.get("title", ""),
                    url=item.get("url", ""),
                    snippet=item.get("description", ""),
                    published_date=item.get("page_age"),
                    source_provider="brave"
                ))
            return results

class DuckDuckGoProvider(BaseSearchProvider):
    """Fallback scraping provider when API limits are hit."""
    name = "duckduckgo"
    endpoint = "https://html.duckduckgo.com/html/"

    async def search(self, query: str, max_results: int = 10) -> List[SearchResult]:
        # Using lite DDG HTML version to avoid JS rendering overhead
        headers = {"User-Agent": "NapsterTec-Engine/1.0"}
        async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
            response = await client.get(self.endpoint, params={"q": query}, headers=headers)
            response.raise_for_status()
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(response.text, "html.parser")
            
            results = []
            for result in soup.select(".result"):
                title_link = result.select_one(".result__a")
                snippet = result.select_one(".result__snippet")
                if title_link and title_link.get("href"):
                    title = title_link.get_text(" ", strip=True)
                    canonical_url = _normalize_result_url(title_link["href"])
                    if not title or not canonical_url:
                        continue
                    results.append(SearchResult(
                        title=title,
                        url=canonical_url,
                        snippet=snippet.get_text(" ", strip=True) if snippet else "",
                        source_provider="duckduckgo"
                    ))
                if len(results) >= max_results:
                    break
            return results

# Stubs for other requested enterprise providers
class GoogleCustomSearchProvider(BaseSearchProvider):
    async def search(self, query: str, max_results: int = 10) -> List[SearchResult]:
        # Implements standard Google CSE JSON API
        pass

class TavilyProvider(BaseSearchProvider):
    async def search(self, query: str, max_results: int = 10) -> List[SearchResult]:
        # AI-optimized search provider implementation
        pass

class SerpAPIProvider(BaseSearchProvider):
    async def search(self, query: str, max_results: int = 10) -> List[SearchResult]:
        # Heavy-duty scraping API implementation
        pass

class SearXNGProvider(BaseSearchProvider):
    async def search(self, query: str, max_results: int = 10) -> List[SearchResult]:
        # Open-source metasearch engine implementation
        pass
