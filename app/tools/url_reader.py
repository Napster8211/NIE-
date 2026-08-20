import subprocess
import json
import logging
from typing import List
from pydantic import BaseModel, Field
from app.tools.base_tool import BaseTool

logger = logging.getLogger(__name__)

class UrlReaderInput(BaseModel):
    url: str = Field(..., description="The URL to scrape.")

class UrlReaderOutput(BaseModel):
    extracted_text: str = Field(..., description="The cleaned text content.")

class UrlReaderTool(BaseTool):
    name: str = "url_reader"
    description: str = "Extracts text from web links using a headless browser."
    input_schema = UrlReaderInput
    output_schema = UrlReaderOutput
    capabilities = ["web_fetching"]
    permissions = ["network_outbound"]

    async def execute(self, url: str, **kwargs) -> dict:
        logger.info(f"[UrlReaderTool] Firing isolated Playwright process for: {url}")
        
        # Injected 30000ms navigation timeout + 3000ms DOM hydration pause
        script = f"""
from playwright.sync_api import sync_playwright
import json
import sys

try:
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto('{url}', wait_until="domcontentloaded", timeout=30000)
        
        # Pause to let React/Next.js storefront components fully render
        page.wait_for_timeout(3000) 
        
        text = page.evaluate('document.body.innerText')
        print(json.dumps({{"extracted_text": text[:8000]}}))
        browser.close()
except Exception as e:
    print(json.dumps({{"extracted_text": f"Playwright script error: {{str(e)}}"}}))
    sys.exit(0)
"""
        try:
            result = subprocess.run(
                ["python", "-c", script], 
                capture_output=True, text=True, check=True
            )
            return json.loads(result.stdout)
        except subprocess.CalledProcessError as e:
            logger.error(f"[UrlReaderTool] Subprocess crashed: {e.stderr}")
            return {"extracted_text": f"Scrape failed. stderr: {e.stderr}"}
        except Exception as e:
            return {"extracted_text": f"Scrape execution failed: {str(e)}"}