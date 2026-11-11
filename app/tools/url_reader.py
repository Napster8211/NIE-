import subprocess
import json
import logging
import importlib.util
import re
from typing import List
from pydantic import BaseModel, Field
import httpx
from bs4 import BeautifulSoup
from app.tools.base_tool import BaseTool

logger = logging.getLogger(__name__)

class UrlReaderInput(BaseModel):
    url: str = Field(..., description="The URL to scrape.")

class UrlReaderOutput(BaseModel):
    extracted_text: str = Field(..., description="The cleaned text content.")

class UrlReaderTool(BaseTool):
    name: str = "url_reader"
    description: str = "Extracts text from web links using Playwright with an httpx and BeautifulSoup fallback."
    input_schema = UrlReaderInput
    output_schema = UrlReaderOutput
    capabilities = ["web_fetching"]
    permissions = ["network_outbound"]

    async def _fetch_with_httpx_bs4(self, url: str) -> dict:
        """Fallback web scraper using httpx and BeautifulSoup when Playwright is unavailable."""
        logger.info(f"[UrlReaderTool] Scraping with httpx/BeautifulSoup fallback for: {url}")
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "en-US,en;q=0.9",
        }
        try:
            async with httpx.AsyncClient(timeout=20.0, follow_redirects=True, verify=False) as client:
                response = await client.get(url, headers=headers)
                if response.status_code >= 400:
                    return {"extracted_text": f"HTTP request failed with status {response.status_code}"}

                soup = BeautifulSoup(response.text, "html.parser")

                # Strip script, style, svg, and non-content tags
                for element in soup(["script", "style", "svg", "noscript", "iframe", "nav", "footer"]):
                    element.decompose()

                title = soup.title.string.strip() if (soup.title and soup.title.string) else ""
                main = (
                    soup.find("main")
                    or soup.find("article")
                    or soup.find("div", id="root")
                    or soup.find("div", class_=re.compile(r"content|main|post|body", re.I))
                    or soup.find("body")
                    or soup
                )
                text = main.get_text(separator="\n", strip=True) if main else soup.get_text(separator="\n", strip=True)
                text = re.sub(r"\n{3,}", "\n\n", text)

                output = f"Title: {title}\n\n{text}" if title and not text.startswith(title) else text
                if not output.strip():
                    output = "No readable text content found on the page."

                return {"extracted_text": output[:10000]}
        except Exception as e:
            logger.error(f"[UrlReaderTool] httpx/bs4 fallback failed for {url}: {e}")
            return {"extracted_text": f"Scrape failed: {str(e)}"}

    async def execute(self, url: str, **kwargs) -> dict:
        # Check if playwright is available
        has_playwright = importlib.util.find_spec("playwright") is not None

        if not has_playwright:
            logger.info(f"[UrlReaderTool] Playwright not found in environment. Using httpx/bs4 fallback for: {url}")
            return await self._fetch_with_httpx_bs4(url)

        logger.info(f"[UrlReaderTool] Firing isolated Playwright process for: {url}")

        script = f"""
from playwright.sync_api import sync_playwright
import json
import sys

try:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto('{url}', wait_until="domcontentloaded", timeout=20000)
        page.wait_for_timeout(2000)
        text = page.evaluate('document.body.innerText')
        print(json.dumps({{"extracted_text": text[:8000]}}))
        browser.close()
except Exception as e:
    print(json.dumps({{"extracted_text": f"Playwright script error: {{str(e)}}", "error": True}}))
    sys.exit(1)
"""
        try:
            result = subprocess.run(
                ["python", "-c", script],
                capture_output=True, text=True, timeout=30
            )
            if result.returncode == 0:
                data = json.loads(result.stdout)
                if not data.get("error"):
                    return data
            logger.warning(
                f"[UrlReaderTool] Playwright execution failed (code {result.returncode}), "
                f"falling back to httpx/BeautifulSoup. Stderr: {result.stderr}"
            )
            return await self._fetch_with_httpx_bs4(url)
        except Exception as e:
            logger.warning(
                f"[UrlReaderTool] Playwright execution crashed ({e}), "
                "falling back to httpx/BeautifulSoup"
            )
            return await self._fetch_with_httpx_bs4(url)
