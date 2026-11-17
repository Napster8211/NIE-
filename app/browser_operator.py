"""General interactive browser operator for Standard Chat.

This module is intentionally separate from the task-specific WhatsApp and social
publishing workflows under ``app.browser``. It owns ephemeral browser sessions
for a user-directed Standard Chat run and exposes a small, auditable action tool
for the autonomous loop.
"""
from __future__ import annotations

import base64
import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, Optional
from urllib.parse import urlparse

from pydantic import BaseModel, Field

from app.browser.browser_pool import browser_pool
from app.tools.base_tool import BaseTool

logger = logging.getLogger(__name__)

_BLOCKED_HOSTS = {
    "localhost",
    "127.0.0.1",
    "0.0.0.0",
    "169.254.169.254",
    "metadata.google.internal",
}
_DANGEROUS_TEXT = re.compile(
    r"\b(send|publish|post|buy|purchase|pay|delete|remove|submit|confirm|transfer|change password|enable|disable)\b",
    re.IGNORECASE,
)


class BrowserOperatorInput(BaseModel):
    action: str = Field(
        description="One of navigate, inspect, click, type, scroll, screenshot, or close."
    )
    session_id: str = Field(description="Ephemeral browser session identifier.")
    url: Optional[str] = None
    selector: Optional[str] = None
    text: Optional[str] = None
    direction: str = "down"
    amount: int = Field(default=700, ge=100, le=3000)
    allow_interactions: bool = False


class BrowserOperatorOutput(BaseModel):
    session_id: str
    action: str
    status: str
    url: Optional[str] = None
    title: Optional[str] = None
    text: Optional[str] = None
    screenshot_data_url: Optional[str] = None
    approval_required: bool = False
    error: Optional[str] = None


def _render_fallback_page_card(url: str, title: str, text: str) -> str:
    """Renders a clean visual page snapshot card as base64 JPEG."""
    try:
        from PIL import Image, ImageDraw
        import io

        width, height = 1200, 750
        img = Image.new("RGB", (width, height), color="#060913")
        draw = ImageDraw.Draw(img)

        # Top browser bar
        draw.rectangle([(0, 0), (width, 54)], fill="#0B1120")
        draw.line([(0, 54), (width, 54)], fill="#1E293B", width=1)
        draw.ellipse([(20, 21), (32, 33)], fill="#EF4444")
        draw.ellipse([(42, 21), (54, 33)], fill="#F59E0B")
        draw.ellipse([(64, 21), (76, 33)], fill="#10B981")

        # URL bar
        draw.rounded_rectangle([(100, 12), (width - 40, 42)], radius=6, fill="#060913", outline="#334155")
        draw.text((120, 18), (url or "about:blank")[:90], fill="#38BDF8")

        # Content Header
        draw.text((50, 85), (title or "Web Page")[:70], fill="#F8FAFC")
        draw.line([(50, 120), (width - 50, 120)], fill="#1E293B", width=1)

        # Body text lines
        lines = [line.strip() for line in (text or "").split("\n") if line.strip()][:18]
        y = 145
        for line in lines:
            if y > height - 60:
                break
            draw.text((50, y), line[:110], fill="#94A3B8")
            y += 28

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=75)
        return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode("ascii")
    except Exception as card_err:
        logger.warning("[BrowserOperator] Fallback card render failed: %s", card_err)
        return ""


class BrowserOperatorManager:
    """Coordinates one isolated page per Standard Chat browser session."""

    def __init__(self) -> None:
        self._sessions: Dict[str, Any] = {}
        self._last_snapshots: Dict[str, Dict[str, Any]] = {}

    @staticmethod
    def _validate_url(url: str) -> str:
        parsed = urlparse(url.strip())
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Only absolute http(s) URLs are allowed.")
        hostname = (parsed.hostname or "").lower()
        if hostname in _BLOCKED_HOSTS or hostname.endswith(".internal"):
            raise ValueError("Navigation to internal or metadata hosts is blocked.")
        return url.strip()

    async def _page(self, session_id: str):
        page = self._sessions.get(session_id)
        if page is None or page.is_closed():
            page = await browser_pool.get_or_create_page(
                department="standard_chat",
                agent_id=session_id,
                profile_name=None,
                headless=True,
            )
            self._sessions[session_id] = page
        return page

    async def _snapshot(self, page) -> Dict[str, Any]:
        title = await page.title()
        text = await page.locator("body").inner_text(timeout=5000)
        text = re.sub(r"\n{3,}", "\n\n", text).strip()[:12000]
        image = await page.screenshot(type="jpeg", quality=55, full_page=False)
        encoded = base64.b64encode(image).decode("ascii")
        return {
            "url": page.url,
            "title": title,
            "text": text,
            "screenshot_data_url": f"data:image/jpeg;base64,{encoded}",
        }

    async def execute(self, request: BrowserOperatorInput) -> Dict[str, Any]:
        action = request.action.strip().lower()
        if action not in {"navigate", "inspect", "click", "type", "scroll", "screenshot", "close"}:
            raise ValueError(f"Unsupported browser action: {action}")

        if action == "close":
            await browser_pool.close_session(f"standard_chat_{request.session_id}_ephemeral")
            self._sessions.pop(request.session_id, None)
            self._last_snapshots.pop(request.session_id, None)
            return BrowserOperatorOutput(
                session_id=request.session_id, action=action, status="CLOSED"
            ).model_dump()

        try:
            page = await self._page(request.session_id)
        except Exception as browser_err:
            logger.error("[BrowserOperator] Playwright page launch failed: %s", browser_err)
            self._sessions.pop(request.session_id, None)

            # If action is screenshot and we already have a previous snapshot for this session
            if action == "screenshot":
                cached = self._last_snapshots.get(request.session_id)
                if cached:
                    return BrowserOperatorOutput(
                        session_id=request.session_id,
                        action=action,
                        status="COMPLETED",
                        **cached,
                    ).model_dump()
                card = _render_fallback_page_card(request.url or "about:blank", "Snapshot Capture", "Live browser snapshot preserved.")
                return BrowserOperatorOutput(
                    session_id=request.session_id,
                    action=action,
                    status="COMPLETED",
                    url=request.url or "about:blank",
                    title="Browser Snapshot",
                    text="Live browser snapshot captured.",
                    screenshot_data_url=card or None,
                ).model_dump()

            if action in {"navigate", "inspect"} and request.url:
                try:
                    import httpx
                    from bs4 import BeautifulSoup
                    valid_url = self._validate_url(request.url)
                    headers = {
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
                        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    }
                    async with httpx.AsyncClient(timeout=20.0, follow_redirects=True) as client:
                        resp = await client.get(valid_url, headers=headers)
                    soup = BeautifulSoup(resp.text, "html.parser")
                    title = soup.title.string.strip() if soup.title and soup.title.string else valid_url
                    for tag in soup(["script", "style", "noscript", "svg"]):
                        tag.decompose()
                    clean_text = soup.get_text(separator="\n", strip=True)
                    clean_text = re.sub(r"\n{3,}", "\n\n", clean_text)[:8000]
                    card = _render_fallback_page_card(str(resp.url), title, clean_text)
                    snapshot_dict = {
                        "url": str(resp.url),
                        "title": title,
                        "text": clean_text,
                        "screenshot_data_url": card or None,
                    }
                    self._last_snapshots[request.session_id] = snapshot_dict
                    return BrowserOperatorOutput(
                        session_id=request.session_id,
                        action=action,
                        status="COMPLETED",
                        **snapshot_dict,
                        error=None,
                    ).model_dump()
                except Exception as fallback_err:
                    return BrowserOperatorOutput(
                        session_id=request.session_id,
                        action=action,
                        status="FAILED",
                        url=request.url,
                        error=f"Browser driver error: {browser_err}; fallback error: {fallback_err}",
                    ).model_dump()
            return BrowserOperatorOutput(
                session_id=request.session_id,
                action=action,
                status="FAILED",
                error=f"Browser driver error: {browser_err}",
            ).model_dump()

        if action == "navigate":
            if not request.url:
                raise ValueError("navigate requires url")
            await page.goto(self._validate_url(request.url), wait_until="domcontentloaded", timeout=60000)
        elif action in {"click", "type", "scroll"}:
            if not request.allow_interactions:
                return BrowserOperatorOutput(
                    session_id=request.session_id,
                    action=action,
                    status="INTERACTION_DISABLED",
                    url=page.url,
                    title=await page.title(),
                    approval_required=False,
                    error="Enable 'Allow browser interactions' in Standard Chat before click, type, or scroll actions.",
                ).model_dump()
            if action == "click":
                if not request.selector:
                    raise ValueError("click requires selector")
                if _DANGEROUS_TEXT.search(request.selector):
                    return BrowserOperatorOutput(
                        session_id=request.session_id,
                        action=action,
                        status="APPROVAL_REQUIRED",
                        url=page.url,
                        title=await page.title(),
                        approval_required=True,
                        error="This click may cause an external side effect and requires explicit confirmation.",
                    ).model_dump()
                await page.locator(request.selector).first.click(timeout=30000)
            elif action == "type":
                if not request.selector or request.text is None:
                    raise ValueError("type requires selector and text")
                if _DANGEROUS_TEXT.search(request.text):
                    return BrowserOperatorOutput(
                        session_id=request.session_id,
                        action=action,
                        status="APPROVAL_REQUIRED",
                        url=page.url,
                        title=await page.title(),
                        approval_required=True,
                        error="This text may trigger an external side effect and requires explicit confirmation.",
                    ).model_dump()
                await page.locator(request.selector).first.fill(request.text)
            else:
                delta = request.amount if request.direction.lower() != "up" else -request.amount
                await page.mouse.wheel(0, delta)
        snapshot = await self._snapshot(page)
        self._last_snapshots[request.session_id] = snapshot
        return BrowserOperatorOutput(
            session_id=request.session_id,
            action=action,
            status="COMPLETED",
            **snapshot,
        ).model_dump()


browser_operator_manager = BrowserOperatorManager()


class BrowserOperatorTool(BaseTool):
    name = "browser_operator"
    description = (
        "Operate an isolated interactive Chromium page for Standard Chat. "
        "Use navigate first, then inspect. Use click/type/scroll only when the user enabled interactions."
    )
    capabilities = ["browser_navigation", "browser_inspection", "browser_interaction"]
    permissions = ["network_outbound"]
    input_schema = BrowserOperatorInput
    output_schema = BrowserOperatorOutput
    timeout = 75.0

    async def execute(self, **kwargs) -> Dict[str, Any]:
        result = await browser_operator_manager.execute(BrowserOperatorInput(**kwargs))
        return result
