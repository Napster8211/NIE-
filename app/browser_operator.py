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


class BrowserOperatorManager:
    """Coordinates one isolated page per Standard Chat browser session."""

    def __init__(self) -> None:
        self._sessions: Dict[str, Any] = {}

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
            return BrowserOperatorOutput(
                session_id=request.session_id, action=action, status="CLOSED"
            ).model_dump()

        page = await self._page(request.session_id)
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
