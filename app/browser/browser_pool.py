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

try:
    from playwright.async_api import async_playwright, Browser, BrowserContext, Page, Route
    HAS_PLAYWRIGHT = True
except ImportError:
    async_playwright = None
    Browser = Any
    BrowserContext = Any
    Page = Any
    Route = Any
    HAS_PLAYWRIGHT = False

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
        self._lock: Optional[asyncio.Lock] = None
        self._loop = None

    def _get_lock(self) -> asyncio.Lock:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if self._lock is None or self._loop != loop:
            self._lock = asyncio.Lock()
            self._loop = loop
        return self._lock

    async def _initialize_locked(self, headless: bool = True, force: bool = False):
        if not HAS_PLAYWRIGHT:
            raise RuntimeError("Playwright package is not installed in this environment.")
        current_loop = None
        try:
            current_loop = asyncio.get_running_loop()
        except RuntimeError:
            pass

        needs_init = force or not self.browser or (self._loop != current_loop)
        if not needs_init:
            try:
                if hasattr(self.browser, "is_connected") and not self.browser.is_connected():
                    needs_init = True
            except Exception:
                needs_init = True

        if needs_init:
            if self.browser:
                try:
                    await self.browser.close()
                except Exception:
                    pass
                self.browser = None
            if self.playwright:
                try:
                    await self.playwright.stop()
                except Exception:
                    pass
                self.playwright = None

            self.active_contexts.clear()
            self.active_pages.clear()
            self._loop = current_loop

            try:
                self.playwright = await async_playwright().start()
            except Exception as pw_err:
                logger.error(f"[BrowserPool] Failed to start Playwright driver: {pw_err}")
                self.playwright = None
                raise RuntimeError(f"Failed to start Playwright driver: {pw_err}") from pw_err

            logger.info("[BrowserPool] Launching sandboxed Chromium with anti-detection flags...")
            launch_args = [
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-infobars",
                "--disable-gpu",
                "--no-first-run",
                "--window-size=1280,800"
            ]
            try:
                self.browser = await self.playwright.chromium.launch(
                    headless=headless,
                    args=launch_args
                )
            except Exception as launch_err:
                logger.error(f"[BrowserPool] Chromium launch failed: {launch_err}")
                if self.playwright:
                    try:
                        await self.playwright.stop()
                    except Exception:
                        pass
                    self.playwright = None
                self.browser = None
                raise RuntimeError(f"Chromium launch failed: {launch_err}") from launch_err
            logger.info("[BrowserPool] Chromium engine online and ready.")

    async def initialize(self, headless: bool = True, force: bool = False):
        async with self._get_lock():
            await self._initialize_locked(headless=headless, force=force)

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
        session_key = f"{department}_{agent_id}_{profile_name or 'ephemeral'}"
        async with self._get_lock():
            is_alive = False
            try:
                if self.browser and hasattr(self.browser, "is_connected") and self.browser.is_connected():
                    is_alive = True
            except Exception:
                is_alive = False

            if not is_alive:
                await self._initialize_locked(headless=headless)

            if session_key in self.active_pages:
                page = self.active_pages[session_key]
                try:
                    if not page.is_closed():
                        return page
                except Exception:
                    pass

            # Determine if saved session exists in Vault
            storage_path = None
            if profile_name and session_vault.has_session(profile_name):
                storage_path = str(session_vault.get_storage_path(profile_name))
                logger.info(f"[BrowserPool] Injecting saved session state for '{profile_name}' from {storage_path}")

            try:
                context = await self.browser.new_context(
                    storage_state=storage_path,
                    viewport={"width": 1280, "height": 800},
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
                    locale="en-US",
                    timezone_id="GMT"
                )
            except Exception as ctx_err:
                logger.warning(f"[BrowserPool] Context creation failed ({ctx_err}). Re-initializing browser engine with force=True...")
                await self._initialize_locked(headless=headless, force=True)
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
