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
    allow_interactions: bool = True


class BrowserOperatorOutput(BaseModel):
    session_id: str
    action: str
    status: str
    url: Optional[str] = None
    title: Optional[str] = None
    text: Optional[str] = None
    screenshot_data_url: Optional[str] = None
    approval_required: bool = False
    auth_required: bool = False
    error: Optional[str] = None


def _is_auth_required(url: str, title: str, text: str) -> bool:
    url_lower = (url or "").lower()
    title_lower = (title or "").lower()
    text_lower = (text or "")[:3000].lower()

    # URL-based auth detection
    auth_url_patterns = [
        r"/login(\b|\?|/|$)",
        r"/signin(\b|\?|/|$)",
        r"/sign-in(\b|\?|/|$)",
        r"/auth(\b|\?|/|$)",
        r"/oauth(\b|\?|/|$)",
        r"tiktok\.com/login",
        r"accounts\.google\.com",
        r"twitter\.com/i/flow/login",
        r"x\.com/i/flow/login",
        r"facebook\.com/login",
        r"github\.com/login",
    ]
    if any(re.search(pat, url_lower) for pat in auth_url_patterns):
        return True

    # Title-based auth detection
    auth_title_patterns = [
        r"\b(log in|sign in|login|signin)\b",
        r"\b(two-factor|authentication|verify your identity)\b",
    ]
    if any(re.search(pat, title_lower) for pat in auth_title_patterns):
        return True

    # Explicit auth challenge in text (not just a navbar 'Login' link)
    auth_challenge_patterns = [
        r"\b(enter password|enter your password|forgot password|two-factor|security checkpoint)\b",
        r"\b(verify account|enter confirmation code|authenticator code)\b",
    ]
    return any(re.search(pat, text_lower) for pat in auth_challenge_patterns)


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

    async def get_session_info(self, session_id: str) -> Dict[str, Any]:
        snapshot = self._last_snapshots.get(session_id) or {}
        page = self._sessions.get(session_id)
        current_url = page.url if page and not page.is_closed() else snapshot.get("url", "about:blank")
        current_title = await page.title() if page and not page.is_closed() else snapshot.get("title", "Browser Session")
        auth_req = _is_auth_required(current_url, current_title, snapshot.get("text", ""))
        return {
            "session_id": session_id,
            "url": current_url,
            "title": current_title,
            "status": "WAITING_FOR_AUTH" if auth_req else "ACTIVE",
            "auth_required": auth_req,
            "screenshot_data_url": snapshot.get("screenshot_data_url"),
        }

    async def take_control(self, session_id: str) -> Dict[str, Any]:
        info = await self.get_session_info(session_id)
        info["status"] = "USER_IN_CONTROL"
        return info

    async def return_control(self, session_id: str) -> Dict[str, Any]:
        page = self._sessions.get(session_id)
        if page and not page.is_closed():
            snapshot = await self._snapshot(page)
            self._last_snapshots[session_id] = snapshot
        else:
            snapshot = self._last_snapshots.get(session_id) or {}

        current_url = snapshot.get("url", "about:blank")
        current_title = snapshot.get("title", "")
        auth_req = _is_auth_required(current_url, current_title, snapshot.get("text", ""))

        return {
            "session_id": session_id,
            "status": "WAITING_FOR_AUTH" if auth_req else "AUTH_VERIFIED",
            "auth_verified": not auth_req,
            "url": current_url,
            "title": current_title,
            "screenshot_data_url": snapshot.get("screenshot_data_url"),
        }

    async def verify_auth(self, session_id: str) -> Dict[str, Any]:
        return await self.return_control(session_id)

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
            return BrowserOperatorOutput(
                session_id=request.session_id,
                action=action,
                status="FAILED",
                url=request.url,
                error=f"Browser driver error: {browser_err}",
            ).model_dump()

        if action == "navigate":
            if not request.url:
                return BrowserOperatorOutput(
                    session_id=request.session_id,
                    action=action,
                    status="FAILED",
                    error="navigate requires url",
                ).model_dump()
            try:
                valid_url = self._validate_url(request.url)
            except Exception as val_err:
                return BrowserOperatorOutput(
                    session_id=request.session_id,
                    action=action,
                    status="FAILED",
                    url=request.url,
                    error=f"URL validation error: {val_err}",
                ).model_dump()
            try:
                await page.goto(valid_url, wait_until="domcontentloaded", timeout=45000)
            except Exception as nav_err:
                logger.error("[BrowserOperator] Playwright page.goto failed: %s", nav_err)
                return BrowserOperatorOutput(
                    session_id=request.session_id,
                    action=action,
                    status="FAILED",
                    url=valid_url,
                    error=f"Browser navigation error: {nav_err}",
                ).model_dump()
        elif action == "screenshot":
            try:
                snapshot = await self._snapshot(page)
                self._last_snapshots[request.session_id] = snapshot
                auth_req = _is_auth_required(snapshot["url"], snapshot["title"], snapshot["text"])
                step_status = "WAITING_FOR_AUTH" if auth_req else "COMPLETED"
                return BrowserOperatorOutput(
                    session_id=request.session_id,
                    action=action,
                    status=step_status,
                    auth_required=auth_req,
                    **snapshot,
                ).model_dump()
            except Exception as ss_err:
                logger.error("[BrowserOperator] Playwright screenshot failed: %s", ss_err)
                return BrowserOperatorOutput(
                    session_id=request.session_id,
                    action=action,
                    status="FAILED",
                    url=page.url if hasattr(page, "url") else request.url,
                    error=f"Browser screenshot failed: {ss_err}",
                ).model_dump()
        elif action in {"click", "type", "scroll"}:
            if not request.allow_interactions and _DANGEROUS_TEXT.search(f"{request.selector or ''} {request.text or ''}"):
                return BrowserOperatorOutput(
                    session_id=request.session_id,
                    action=action,
                    status="INTERACTION_DISABLED",
                    url=page.url if hasattr(page, 'url') else request.url,
                    title=await page.title() if hasattr(page, 'title') else "Browser",
                    approval_required=False,
                    error="This high-impact action requires explicit permission.",
                ).model_dump()
            try:
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
            except Exception as act_err:
                logger.error("[BrowserOperator] Browser %s failed: %s", action, act_err)
                return BrowserOperatorOutput(
                    session_id=request.session_id,
                    action=action,
                    status="FAILED",
                    url=page.url if hasattr(page, "url") else request.url,
                    error=f"Browser action '{action}' failed: {act_err}",
                ).model_dump()
        snapshot = await self._snapshot(page)
        self._last_snapshots[request.session_id] = snapshot
        auth_req = _is_auth_required(snapshot["url"], snapshot["title"], snapshot["text"])
        step_status = "WAITING_FOR_AUTH" if auth_req else "COMPLETED"
        return BrowserOperatorOutput(
            session_id=request.session_id,
            action=action,
            status=step_status,
            auth_required=auth_req,
            **snapshot,
        ).model_dump()


browser_operator_manager = BrowserOperatorManager()


class BrowserOperatorTool(BaseTool):
    name = "browser_operator"
    description = (
        "Operate an isolated interactive Chromium page for Standard Chat. "
        "Supports navigate, inspect, click, type, scroll, screenshot, and close."
    )
    capabilities = ["browser_navigation", "browser_inspection", "browser_interaction"]
    permissions = ["network_outbound"]
    input_schema = BrowserOperatorInput
    output_schema = BrowserOperatorOutput
    timeout = 75.0

    async def execute(self, **kwargs) -> Dict[str, Any]:
        context = kwargs.pop("context", {}) or {}
        if not kwargs.get("session_id") and context.get("session_id"):
            kwargs["session_id"] = context.get("session_id")
        if "allow_interactions" not in kwargs or kwargs["allow_interactions"] is None:
            kwargs["allow_interactions"] = bool(
                context.get("browser_allow_interactions", True)
                or context.get("mutation_allowed", True)
                or context.get("access_mode") == "INTERACTIVE"
            )
        result = await browser_operator_manager.execute(BrowserOperatorInput(**kwargs))
        return result
