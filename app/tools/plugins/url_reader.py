import os
import math
import logging
import re
import asyncio
import io
import random
from typing import Any, Dict, List, Optional, AsyncGenerator
from urllib.parse import urlparse
from pydantic import BaseModel, Field

import httpx
from bs4 import BeautifulSoup
from markdownify import markdownify as md
from PyPDF2 import PdfReader

from app.tools.base_tool import BaseTool
from app.tools.tool_models import RetryPolicy

logger = logging.getLogger(__name__)

# --- URL Reader Schemas ---

class URLReaderInput(BaseModel):
    url: str = Field(..., description="The fully qualified URL to read (e.g., https://github.com/...)")
    chunk_size: int = Field(default=4000, description="Character limit per extracted chunk for LLM context windows.")
    include_raw_html: bool = Field(default=False, description="Whether to include the raw, uncleaned HTML in the output.")

class URLReaderOutput(BaseModel):
    title: str = Field(default="Unknown Title")
    author: Optional[str] = None
    content: str = Field(..., description="The cleaned, markdown-formatted content of the URL.")
    sections: List[str] = Field(default_factory=list, description="Chunked sections of the content.")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Extracted metadata tags.")
    word_count: int = 0
    estimated_reading_time_min: int = 0
    source_type: str = Field(..., description="Detected type: github_repo, github_readme, pdf, html_doc, etc.")

# --- Cache Management ---

class AsyncTTLCache:
    """Simple in-memory TTL cache for high-frequency URL reads."""
    def __init__(self, ttl_seconds: int = 3600):
        self.ttl = ttl_seconds
        self._cache: Dict[str, Dict[str, Any]] = {}

    def get(self, key: str) -> Optional[Any]:
        if key in self._cache:
            entry = self._cache[key]
            if asyncio.get_event_loop().time() < entry['expires_at']:
                return entry['data']
            else:
                del self._cache[key]
        return None

    def set(self, key: str, data: Any):
        self._cache[key] = {
            'data': data,
            'expires_at': asyncio.get_event_loop().time() + self.ttl
        }

# --- Tool Implementation ---

class URLReaderTool(BaseTool):
    """
    Enterprise URL Reader. Bypasses standard bot-protections to extract 
    and convert web content into LLM-digestible chunks for deep research.
    """
    
    def __init__(self):
        self._cache = AsyncTTLCache(ttl_seconds=1800)
        self._rate_limit_semaphore = asyncio.Semaphore(10) # Increased concurrency
        
        # Rotating Enterprise Headers to bypass basic 403 Forbidden / Bot Blocks
        self.user_agents = [
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2.1 Safari/605.1.15",
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
        ]
        
    @property
    def name(self) -> str:
        return "url_reader"

    @property
    def description(self) -> str:
        return "Extracts and reads content from URLs, including GitHub repositories, READMEs, PDFs, and deep market/supplier documentation."

    @property
    def capabilities(self) -> List[str]:
        return ["network", "read-only", "web-scraping", "data-extraction"]

    @property
    def permissions(self) -> List[str]:
        return ["internet_access"]

    @property
    def input_schema(self) -> type[BaseModel]:
        return URLReaderInput

    @property
    def output_schema(self) -> type[BaseModel]:
        return URLReaderOutput

    @property
    def timeout(self) -> float:
        return 45.0 # Extended for heavy global portals and PDFs

    @property
    def retry_policy(self) -> RetryPolicy:
        return RetryPolicy(
            max_retries=3,
            backoff_factor=2.5,
            retryable_exceptions=["TimeoutException", "ConnectError", "ReadError", "HTTPStatusError"]
        )

    def _get_headers(self) -> dict:
        """Generates evasive headers for scraping."""
        return {
            "User-Agent": random.choice(self.user_agents),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
            "Upgrade-Insecure-Requests": "1",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1"
        }

    async def execute(self, **kwargs) -> Dict[str, Any]:
        """Main execution flow for single-shot retrieval."""
        url = kwargs["url"]
        chunk_size = kwargs.get("chunk_size", 4000)
        
        cached_result = self._cache.get(url)
        if cached_result:
            logger.info(f"[URLReader] Cache hit for {url}")
            return cached_result

        async with self._rate_limit_semaphore:
            logger.info(f"[URLReader] Fetching target: {url}")
            
            # verify=False helps bypass strict corporate SSL configurations often found in supply chain portals
            async with httpx.AsyncClient(follow_redirects=True, timeout=self.timeout, verify=False) as client:
                response = await client.get(url, headers=self._get_headers())
                
                # If we hit a 403, we still want to try to parse it (sometimes blocks return partial HTML)
                if response.status_code not in [200, 403]:
                    response.raise_for_status()

        content_type = response.headers.get("Content-Type", "").lower()
        parsed_url = urlparse(url)
        
        if "pdf" in content_type or url.endswith(".pdf"):
            parsed_data = self._parse_pdf(response.content)
            parsed_data["source_type"] = "pdf"
        elif "github.com" in parsed_url.netloc:
            parsed_data = await self._parse_github(url, client)
        else:
            parsed_data = self._parse_html(response.text, url)
            parsed_data["source_type"] = "html_doc"

        text_content = parsed_data["content"]
        words = len(text_content.split())
        parsed_data["word_count"] = words
        parsed_data["estimated_reading_time_min"] = math.ceil(words / 200)
        parsed_data["sections"] = self._chunk_text(text_content, chunk_size)

        self._cache.set(url, parsed_data)
        return parsed_data

    async def execute_stream(self, **kwargs) -> AsyncGenerator[Dict[str, Any], None]:
        result = await self.execute(**kwargs)
        
        yield {
            "type": "metadata",
            "title": result["title"],
            "source_type": result["source_type"],
            "word_count": result["word_count"]
        }
        
        for i, chunk in enumerate(result["sections"]):
            yield {
                "type": "chunk",
                "index": i,
                "total_chunks": len(result["sections"]),
                "content": chunk
            }
            await asyncio.sleep(0.01)

    def _parse_html(self, html_text: str, url: str) -> Dict[str, Any]:
        """Aggressively strips noise and isolates main content."""
        soup = BeautifulSoup(html_text, "html.parser")
        
        title_tag = soup.find("title")
        title = title_tag.text.strip() if title_tag else url
        
        author = None
        metadata = {}
        for meta in soup.find_all("meta"):
            name = meta.get("name", "").lower()
            prop = meta.get("property", "").lower()
            content = meta.get("content", "")
            
            if name in ["author", "creator"]:
                author = content
            elif name in ["description", "keywords"] or prop.startswith("og:"):
                metadata[name or prop] = content

        # Remove elements that pollute LLM context
        for tag in soup(["script", "style", "nav", "footer", "header", "aside", "noscript", "iframe", "svg"]):
            tag.decompose()

        main_content = soup.find("main") or soup.find("article") or soup.find("div", class_=re.compile(r'content|main|post|body', re.I))
        if not main_content:
            main_content = soup.find("body") or soup

        # Convert to Markdown for high-density, low-token data extraction
        markdown_content = md(str(main_content), heading_style="ATX", strip=['a', 'img']).strip()
        markdown_content = re.sub(r'\n{3,}', '\n\n', markdown_content)

        return {
            "title": title,
            "author": author,
            "content": markdown_content,
            "metadata": metadata
        }

    async def _parse_github(self, url: str, client: httpx.AsyncClient) -> Dict[str, Any]:
        parts = url.strip("/").split("/")
        
        if len(parts) >= 5 and parts[2] == "github.com":
            user, repo = parts[3], parts[4]
            
            if "blob" in parts:
                raw_url = url.replace("github.com", "raw.githubusercontent.com").replace("/blob/", "/")
                source_type = "github_file"
            else:
                raw_url = f"https://raw.githubusercontent.com/{user}/{repo}/main/README.md"
                source_type = "github_readme"
                
            try:
                response = await client.get(raw_url, headers=self._get_headers())
                if response.status_code == 404 and source_type == "github_readme":
                    raw_url = f"https://raw.githubusercontent.com/{user}/{repo}/master/README.md"
                    response = await client.get(raw_url, headers=self._get_headers())
                
                response.raise_for_status()
                
                return {
                    "title": f"{user}/{repo}",
                    "author": user,
                    "content": response.text,
                    "metadata": {"repo": repo, "owner": user, "original_url": url},
                    "source_type": source_type
                }
                
            except httpx.HTTPStatusError:
                pass
                
        html_data = self._parse_html((await client.get(url, headers=self._get_headers())).text, url)
        html_data["source_type"] = "github_fallback"
        return html_data

    def _parse_pdf(self, pdf_bytes: bytes) -> Dict[str, Any]:
        pdf_file = io.BytesIO(pdf_bytes)
        reader = PdfReader(pdf_file)
        
        text_content = []
        for page in reader.pages:
            text = page.extract_text()
            if text:
                text_content.append(text)
                
        metadata = reader.metadata
        title = metadata.title if metadata and metadata.title else "PDF Document"
        author = metadata.author if metadata and metadata.author else "Unknown"
        
        return {
            "title": title,
            "author": author,
            "content": "\n\n".join(text_content),
            "metadata": dict(metadata) if metadata else {}
        }

    def _chunk_text(self, text: str, chunk_size: int) -> List[str]:
        paragraphs = text.split("\n\n")
        chunks = []
        current_chunk = ""
        
        for p in paragraphs:
            if len(current_chunk) + len(p) < chunk_size:
                current_chunk += p + "\n\n"
            else:
                if current_chunk:
                    chunks.append(current_chunk.strip())
                current_chunk = p + "\n\n"
                
        if current_chunk:
            chunks.append(current_chunk.strip())
            
        return chunks if chunks else [text]