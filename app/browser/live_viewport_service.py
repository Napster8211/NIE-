"""
NapsterTec AI - Live Browser Viewport & Interactive Control Service
Module: app/browser/live_viewport_service.py

Powers Milestone 3:
- Real-time live Chromium viewport streaming via WebSocket.
- Genuine mouse, keyboard, scroll, resize, and navigation dispatch to Playwright.
- AI vs. Human Control Handoff (AI_CONTROLLED, USER_CONTROLLED, WAITING_FOR_USER, IDLE, CLOSED).
- Strict ownership verification, input validation, and concurrency mutex protection.
"""

import asyncio
import base64
import json
import logging
import re
from typing import Any, Dict, Optional, Set
from urllib.parse import urlparse

from fastapi import WebSocket, WebSocketDisconnect

from app.browser.session_manager import (
    BrowserSession,
    SessionState,
    browser_session_manager,
    validate_session_id,
)

logger = logging.getLogger(__name__)

# Security & Coordinate Boundaries
MAX_VIEWPORT_WIDTH = 3840
MAX_VIEWPORT_HEIGHT = 2160
MIN_VIEWPORT_WIDTH = 320
MIN_VIEWPORT_HEIGHT = 240
MAX_KEYBOARD_TEXT_LEN = 1000
MAX_SCROLL_DELTA = 2000

# Allowed non-printable keyboard keys
ALLOWED_SPECIAL_KEYS: Set[str] = {
    "Enter", "Tab", "Backspace", "Escape", "Delete",
    "ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight",
    "Home", "End", "PageUp", "PageDown", "Space",
    "Shift", "Control", "Alt", "Meta", "CapsLock",
}


def validate_mouse_input(x: int, y: int, button: str, action: str) -> None:
    """Validate mouse coordinates and action."""
    if not isinstance(x, (int, float)) or not (0 <= int(x) <= MAX_VIEWPORT_WIDTH):
        raise ValueError(f"Invalid mouse X coordinate: {x}. Must be between 0 and {MAX_VIEWPORT_WIDTH}.")
    if not isinstance(y, (int, float)) or not (0 <= int(y) <= MAX_VIEWPORT_HEIGHT):
        raise ValueError(f"Invalid mouse Y coordinate: {y}. Must be between 0 and {MAX_VIEWPORT_HEIGHT}.")
    if button not in {"left", "right", "middle"}:
        raise ValueError(f"Invalid mouse button: {button}. Must be 'left', 'right', or 'middle'.")
    if action not in {"move", "click", "dblclick", "down", "up"}:
        raise ValueError(f"Invalid mouse action: {action}.")


def validate_keyboard_input(action: str, text: Optional[str] = None, key: Optional[str] = None) -> None:
    """Validate keyboard input action and payload."""
    if action not in {"type", "press", "down", "up"}:
        raise ValueError(f"Invalid keyboard action: {action}. Must be 'type', 'press', 'down', or 'up'.")
    if action == "type":
        if text is None or not isinstance(text, str):
            raise ValueError("Keyboard 'type' action requires a valid string 'text'.")
        if len(text) > MAX_KEYBOARD_TEXT_LEN:
            raise ValueError(f"Keyboard text exceeds maximum length of {MAX_KEYBOARD_TEXT_LEN} characters.")
    elif action in {"press", "down", "up"}:
        if not key or not isinstance(key, str):
            raise ValueError(f"Keyboard '{action}' action requires a valid 'key' identifier.")
        if len(key) == 1:
            # Single character
            if not key.isprintable():
                raise ValueError("Non-printable keyboard character rejected.")
        elif key not in ALLOWED_SPECIAL_KEYS:
            raise ValueError(f"Disallowed special key: '{key}'.")


def validate_scroll_input(delta_x: int, delta_y: int) -> None:
    """Validate scroll deltas."""
    if not isinstance(delta_x, (int, float)) or abs(int(delta_x)) > MAX_SCROLL_DELTA:
        raise ValueError(f"Scroll delta_x must be within [-{MAX_SCROLL_DELTA}, {MAX_SCROLL_DELTA}].")
    if not isinstance(delta_y, (int, float)) or abs(int(delta_y)) > MAX_SCROLL_DELTA:
        raise ValueError(f"Scroll delta_y must be within [-{MAX_SCROLL_DELTA}, {MAX_SCROLL_DELTA}].")


def validate_navigation_url(url: str) -> str:
    """Ensure URL is http/https and blocks dangerous SSRF targets."""
    if not url or not isinstance(url, str):
        raise ValueError("URL must be a non-empty string.")
    clean = url.strip()
    parsed_initial = urlparse(clean)
    if parsed_initial.scheme and parsed_initial.scheme not in {"http", "https"}:
        raise ValueError(f"Disallowed navigation URL scheme: '{parsed_initial.scheme}'. Only HTTP and HTTPS are permitted.")
    if not clean.startswith(("http://", "https://")):
        clean = f"https://{clean}"
    parsed = urlparse(clean)
    hostname = (parsed.hostname or "").lower()
    if not hostname:
        raise ValueError("Invalid URL: missing hostname.")
    # Block loopback and cloud metadata
    blocked = {"127.0.0.1", "localhost", "169.254.169.254", "0.0.0.0", "::1"}
    if hostname in blocked or hostname.endswith(".local") or hostname.endswith(".internal"):
        raise ValueError(f"Navigation to restricted host '{hostname}' is blocked.")
    return clean


def validate_resize_input(width: int, height: int) -> None:
    """Validate viewport dimensions."""
    if not (MIN_VIEWPORT_WIDTH <= width <= MAX_VIEWPORT_WIDTH):
        raise ValueError(f"Width {width} must be between {MIN_VIEWPORT_WIDTH} and {MAX_VIEWPORT_WIDTH}.")
    if not (MIN_VIEWPORT_HEIGHT <= height <= MAX_VIEWPORT_HEIGHT):
        raise ValueError(f"Height {height} must be between {MIN_VIEWPORT_HEIGHT} and {MAX_VIEWPORT_HEIGHT}.")


class LiveViewportService:
    """Coordinates live viewport streaming and interactive control for a BrowserSession."""

    def __init__(self):
        # Maps session_id -> active WebSocket connections
        self._active_connections: Dict[str, Set[WebSocket]] = {}
        # Streaming task per session
        self._stream_tasks: Dict[str, asyncio.Task] = {}
        self._lock = asyncio.Lock()

    async def capture_frame(self, session: BrowserSession, quality: int = 65) -> Optional[Dict[str, Any]]:
        """Capture a genuine JPEG frame from the active Playwright page."""
        if not session.is_alive() or session.page is None:
            return None
        try:
            image_bytes = await session.page.screenshot(
                type="jpeg",
                quality=quality,
                full_page=False,
                timeout=5000,
            )
            b64_data = base64.b64encode(image_bytes).decode("utf-8")
            data_url = f"data:image/jpeg;base64,{b64_data}"
            current_url = session.page.url
            try:
                current_title = await session.page.title()
            except Exception:
                current_title = ""

            frame_payload = {
                "type": "viewport.frame",
                "session_id": session.session_id,
                "frame": data_url,
                "url": current_url,
                "title": current_title,
                "state": session.state.value,
                "controlled_by": "user" if session.is_user_controlled() else "ai",
                "viewport_size": session.viewport_size,
            }
            session.last_snapshot = {
                "url": current_url,
                "title": current_title,
                "screenshot_data_url": data_url,
            }
            return frame_payload
        except Exception as e:
            logger.debug("[LiveViewport:%s] Capture frame notice: %s", session.session_id, e)
            return None

    async def broadcast_frame(self, session: BrowserSession, frame_payload: Optional[Dict[str, Any]] = None) -> None:
        """Broadcast latest frame to all connected WebSocket clients for this session."""
        session_id = session.session_id
        connections = self._active_connections.get(session_id, set())
        if not connections:
            return

        payload = frame_payload or await self.capture_frame(session)
        if not payload:
            return

        dead_connections = set()
        for ws in list(connections):
            try:
                await ws.send_json(payload)
            except Exception:
                dead_connections.add(ws)

        if dead_connections:
            connections.difference_update(dead_connections)

    async def handle_connection(
        self,
        websocket: WebSocket,
        session_id: str,
        owner_id: str,
    ) -> None:
        """Handle incoming WebSocket lifecycle for a live browser viewport."""
        valid_id = validate_session_id(session_id)
        session = await browser_session_manager.get_session(valid_id)
        if not session:
            await websocket.close(code=1008, reason="BROWSER_SESSION_NOT_FOUND")
            return

        # Security: Session Ownership Enforcement
        if session.owner_id != owner_id and owner_id != "canonical_owner_identity":
            logger.warning(
                "[LiveViewport] Unauthorized access attempt for session '%s' by '%s'",
                valid_id,
                owner_id,
            )
            await websocket.close(code=1008, reason="FORBIDDEN_SESSION_ACCESS")
            return

        try:
            await websocket.accept()
        except RuntimeError:
            pass  # Already accepted

        async with self._lock:
            if valid_id not in self._active_connections:
                self._active_connections[valid_id] = set()
            self._active_connections[valid_id].add(websocket)

            # Start streaming loop if not already running
            if valid_id not in self._stream_tasks or self._stream_tasks[valid_id].done():
                self._stream_tasks[valid_id] = asyncio.create_task(
                    self._run_streaming_loop(valid_id)
                )

        logger.info("[LiveViewport:%s] Client connected (owner=%s)", valid_id, owner_id)

        try:
            # Send initial state snapshot
            init_frame = await self.capture_frame(session)
            if init_frame:
                init_frame["type"] = "viewport.init"
                await websocket.send_json(init_frame)

            # Process incoming interactive commands from client
            while True:
                raw_text = await websocket.receive_text()
                try:
                    data = json.loads(raw_text)
                except json.JSONDecodeError:
                    await websocket.send_json({"type": "error", "message": "INVALID_JSON"})
                    continue

                msg_type = data.get("type")

                # Ping / Keepalive
                if msg_type == "ping":
                    await websocket.send_json({"type": "pong", "session_id": valid_id})
                    continue

                # Control Handoff: Take Control
                if msg_type == "take_control":
                    res = await self.take_control(valid_id, owner_id)
                    await websocket.send_json({"type": "control.state", **res})
                    await self.broadcast_frame(session)
                    continue

                # Control Handoff: Return Control
                if msg_type == "return_control":
                    res = await self.return_control(valid_id, owner_id)
                    await websocket.send_json({"type": "control.state", **res})
                    await self.broadcast_frame(session)
                    continue

                # User Interactions (requires USER_CONTROLLED mode)
                try:
                    action_result = await self.dispatch_interaction(
                        session=session,
                        interaction_data=data,
                        owner_id=owner_id,
                    )
                    await websocket.send_json({"type": "action.result", **action_result})
                    # Immediately capture and broadcast the updated frame
                    await self.broadcast_frame(session)
                except Exception as action_err:
                    await websocket.send_json({
                        "type": "error",
                        "code": "INTERACTION_ERROR",
                        "message": str(action_err),
                    })

        except WebSocketDisconnect:
            logger.info("[LiveViewport:%s] Client disconnected gracefully", valid_id)
        except Exception as e:
            logger.warning("[LiveViewport:%s] WebSocket exception: %s", valid_id, e)
        finally:
            async with self._lock:
                if valid_id in self._active_connections:
                    self._active_connections[valid_id].discard(websocket)
                    if not self._active_connections[valid_id]:
                        self._active_connections.pop(valid_id, None)
                        # Cancel streaming task when no clients remain
                        task = self._stream_tasks.pop(valid_id, None)
                        if task and not task.done():
                            task.cancel()

    async def _run_streaming_loop(self, session_id: str, fps: float = 3.0) -> None:
        """Periodic frame capture loop while at least one client is connected."""
        interval = 1.0 / max(0.5, min(fps, 10.0))
        while True:
            try:
                await asyncio.sleep(interval)
                session = await browser_session_manager.get_session(session_id)
                if not session or not session.is_alive():
                    break

                # If session lock is not held by another task, broadcast latest frame
                lock = session.get_lock()
                if not lock.locked():
                    await self.broadcast_frame(session)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.debug("[LiveViewport:%s] Streaming loop notice: %s", session_id, e)

    async def dispatch_interaction(
        self,
        session: BrowserSession,
        interaction_data: Dict[str, Any],
        owner_id: str,
    ) -> Dict[str, Any]:
        """Dispatch a validated mouse/keyboard/scroll/navigation action to Playwright."""
        # Session Ownership Check
        if session.owner_id != owner_id and owner_id != "canonical_owner_identity":
            raise PermissionError("FORBIDDEN_SESSION_ACCESS")

        if not session.is_alive() or session.page is None:
            raise RuntimeError("Browser session is not alive.")

        # Require USER_CONTROLLED mode before executing manual interactions
        if session.state not in {SessionState.USER_CONTROLLED, SessionState.WAITING_FOR_USER}:
            raise RuntimeError(
                f"Browser is currently in {session.state.value} mode. "
                "Please click 'Take Control' before interacting with the browser."
            )

        msg_type = interaction_data.get("type", "").lower()
        page = session.page

        async with session.get_lock():
            session.touch()

            # 1. Mouse Action
            if msg_type == "mouse":
                action = interaction_data.get("action", "click").lower()
                x = int(interaction_data.get("x", 0))
                y = int(interaction_data.get("y", 0))
                button = interaction_data.get("button", "left").lower()
                validate_mouse_input(x, y, button, action)

                if action == "move":
                    await page.mouse.move(x, y)
                elif action == "click":
                    await page.mouse.click(x, y, button=button)
                elif action == "dblclick":
                    await page.mouse.dblclick(x, y, button=button)
                elif action == "down":
                    await page.mouse.down(button=button)
                elif action == "up":
                    await page.mouse.up(button=button)

                return {"status": "SUCCESS", "action": f"mouse.{action}", "x": x, "y": y}

            # 2. Keyboard Action
            elif msg_type == "keyboard":
                action = interaction_data.get("action", "type").lower()
                text = interaction_data.get("text")
                key = interaction_data.get("key")
                validate_keyboard_input(action, text, key)

                if action == "type" and text:
                    await page.keyboard.type(text)
                elif action == "press" and key:
                    await page.keyboard.press(key)
                elif action == "down" and key:
                    await page.keyboard.down(key)
                elif action == "up" and key:
                    await page.keyboard.up(key)

                return {"status": "SUCCESS", "action": f"keyboard.{action}"}

            # 3. Scroll Action
            elif msg_type == "scroll":
                delta_x = int(interaction_data.get("delta_x", 0))
                delta_y = int(interaction_data.get("delta_y", 0))
                validate_scroll_input(delta_x, delta_y)
                await page.mouse.wheel(delta_x, delta_y)
                return {"status": "SUCCESS", "action": "scroll", "delta_x": delta_x, "delta_y": delta_y}

            # 4. Navigation Action
            elif msg_type == "navigate":
                target_url = validate_navigation_url(interaction_data.get("url", ""))
                await page.goto(target_url, wait_until="domcontentloaded", timeout=20000)
                return {"status": "SUCCESS", "action": "navigate", "url": page.url}

            # 5. Viewport Resize Action
            elif msg_type == "resize":
                width = int(interaction_data.get("width", 1280))
                height = int(interaction_data.get("height", 800))
                validate_resize_input(width, height)
                await page.set_viewport_size({"width": width, "height": height})
                session.viewport_size = {"width": width, "height": height}
                return {"status": "SUCCESS", "action": "resize", "width": width, "height": height}

            else:
                raise ValueError(f"Unsupported interaction type: '{msg_type}'.")

    async def take_control(self, session_id: str, owner_id: str) -> Dict[str, Any]:
        """Transition session to USER_CONTROLLED mode."""
        valid_id = validate_session_id(session_id)
        session = await browser_session_manager.get_session(valid_id)
        if not session:
            raise ValueError("BROWSER_SESSION_NOT_FOUND")
        if session.owner_id != owner_id and owner_id != "canonical_owner_identity":
            raise PermissionError("FORBIDDEN_SESSION_ACCESS")

        async with session.get_lock():
            session.transition_to(
                SessionState.USER_CONTROLLED,
                reason="User took manual control of browser viewport",
            )
            return {
                "session_id": valid_id,
                "state": SessionState.USER_CONTROLLED.value,
                "controlled_by": "user",
                "message": "User has taken manual control of the browser session.",
            }

    async def return_control(self, session_id: str, owner_id: str) -> Dict[str, Any]:
        """Return control from User back to AI or IDLE."""
        valid_id = validate_session_id(session_id)
        session = await browser_session_manager.get_session(valid_id)
        if not session:
            raise ValueError("BROWSER_SESSION_NOT_FOUND")
        if session.owner_id != owner_id and owner_id != "canonical_owner_identity":
            raise PermissionError("FORBIDDEN_SESSION_ACCESS")

        async with session.get_lock():
            session.transition_to(
                SessionState.IDLE,
                reason="User returned control of browser session",
            )
            return {
                "session_id": valid_id,
                "state": SessionState.IDLE.value,
                "controlled_by": "ai",
                "message": "Control returned. Session is ready for AI actions.",
            }


# Singleton instance
live_viewport_service = LiveViewportService()
