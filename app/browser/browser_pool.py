"""
NapsterTec AI - Autonomous Browser Pool & Stealth Control Plane
Module: app/browser/browser_pool.py

Manages high-concurrency, sandboxed Playwright Chromium contexts with
anti-detection stealth, human-like typing, SSRF guard, and persistent Session Vault binding.
"""
import os
import asyncio
import logging
from typing import Dict, Optional, Tuple, Any
from pathlib import Path
from playwright.async_api import async_playwright, Browser, BrowserContext, Page, Route

from app.browser.session_vault import session_vault

logger = logging.getLogger(__name__)

BLOCKED_INTERNAL_HOSTS = {
    "localhost", "127.0.0.1", "0.0.0.0", "169.254.169.254", "metadata.google.internal"
}

STEALTH_INJECTION_SCRIPT = """
(() => {
    // 1. Mask navigator.webdriver
    Object.defineProperty(navigator, 'webdriver', {
        get: () => undefined,
        configurable: true
    });

    // 2. Emulate Chrome runtime
    window.chrome = {
        runtime: {},
        loadTimes: function() {},
        csi: function() {},
        app: {}
    };

    // 3. Mock navigator.plugins to look like authentic desktop Chrome
    Object.defineProperty(navigator, 'plugins', {
        get: () => [1, 2, 3, 4, 5],
        configurable: true
    });

    // 4. Set standard languages
    Object.defineProperty(navigator, 'languages', {
        get: () => ['en-US', 'en'],
        configurable: true
    });
})();
"""

class BrowserPool:
    def __init__(self):
        self.playwright = None
        self.browser: Optional[Browser] = None
        self.active_contexts: Dict[str, BrowserContext] = {}
        self.active_pages: Dict[str, Page] = {}
        self._lock = asyncio.Lock()

    async def initialize(self, headless: bool = True):
        async with self._lock:
            if not self.playwright:
                self.playwright = await async_playwright().start()
            if not self.browser:
                logger.info("[BrowserPool] Launching sandboxed Chromium with anti-detection flags...")
                self.browser = await self.playwright.chromium.launch(
                    headless=headless,
                    args=[
                        "--disable-blink-features=AutomationControlled",
                        "--no-sandbox",
                        "--disable-dev-shm-usage",
                        "--disable-infobars",
                        "--window-size=1280,800"
                    ]
                )
                logger.info("[BrowserPool] Chromium engine online and ready.")

    async def _ssrf_guard(self, route: Route):
        """Prevents agents from requesting local network metadata services."""
        url = route.request.url.lower()
        for blocked in BLOCKED_INTERNAL_HOSTS:
            if blocked in url:
                logger.warning(f"[BrowserPool] Blocked suspicious internal navigation: {url}")
                await route.abort("accessdenied")
                return
        await route.continue_()

    async def get_or_create_page(
        self,
        department: str,
        agent_id: str,
        profile_name: Optional[str] = None,
        headless: bool = True
    ) -> Page:
        """
        Retrieves or spawns an isolated browser page for a specific agent.
        If profile_name is specified (e.g. 'whatsapp', 'tiktok_viewstorm'),
        loads authenticated cookies and tokens from the SessionVault.
        """
        if not self.browser:
            await self.initialize(headless=headless)

        session_key = f"{department}_{agent_id}_{profile_name or 'ephemeral'}"
        if session_key in self.active_pages:
            page = self.active_pages[session_key]
            if not page.is_closed():
                return page

        # Determine if saved session exists in Vault
        storage_path = None
        if profile_name and session_vault.has_session(profile_name):
            storage_path = str(session_vault.get_storage_path(profile_name))
            logger.info(f"[BrowserPool] Injecting saved session state for '{profile_name}' from {storage_path}")

        context = await self.browser.new_context(
            storage_state=storage_path,
            viewport={"width": 1280, "height": 800},
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            locale="en-US",
            timezone_id="GMT"
        )

        # Apply security guard
        await context.route("**/*", self._ssrf_guard)

        page = await context.new_page()
        # Inject anti-detection overrides into every frame
        await page.add_init_script(STEALTH_INJECTION_SCRIPT)

        self.active_contexts[session_key] = context
        self.active_pages[session_key] = page
        return page

    async def save_profile(self, session_key: str, profile_name: str) -> Path:
        """Exports the active cookies and localStorage of a session to the SessionVault."""
        if session_key in self.active_contexts:
            context = self.active_contexts[session_key]
            state = await context.storage_state()
            saved_path = session_vault.save_session(profile_name, state)
            return saved_path
        raise ValueError(f"No active browser context found for session key '{session_key}'")

    async def human_type(self, page: Page, selector: str, text: str, delay_ms: int = 35, press_enter: bool = False):
        """Types with human-like cadence to prevent bot detection."""
        locator = page.locator(selector).first
        await locator.click()
        await locator.press_sequentially(text, delay=delay_ms)
        if press_enter:
            await page.keyboard.press("Enter")

    async def close_session(self, session_key: str):
        if session_key in self.active_contexts:
            try:
                await self.active_contexts[session_key].close()
            except Exception as e:
                logger.warning(f"[BrowserPool] Error closing context '{session_key}': {e}")
            self.active_contexts.pop(session_key, None)
            self.active_pages.pop(session_key, None)

    async def shutdown(self):
        for key in list(self.active_contexts.keys()):
            await self.close_session(key)
        if self.browser:
            await self.browser.close()
            self.browser = None
        if self.playwright:
            await self.playwright.stop()
            self.playwright = None
        logger.info("[BrowserPool] Browser engine shut down.")

# Singleton instance
browser_pool = BrowserPool()
