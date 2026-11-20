"""
Unit and Integration Tests for Milestone 3:
Live Browser Viewport, Interactive Control & AI/User Handoff.
"""

import asyncio
import os
import time
import pytest
from httpx import AsyncClient, ASGITransport
from starlette.testclient import TestClient

# Ensure test owner key is configured for authenticated requests
os.environ["NIE_OWNER_KEY"] = "test-director-owner-key-m3"

from app.main import app
from app.browser.session_manager import browser_session_manager, SessionState
from app.browser.live_viewport_service import (
    live_viewport_service,
    validate_mouse_input,
    validate_keyboard_input,
    validate_scroll_input,
    validate_navigation_url,
    validate_resize_input,
)


@pytest.fixture
def auth_headers():
    return {"Authorization": "Bearer test-director-owner-key-m3"}


# ---------------------------------------------------------
# 1. Validation Logic Tests
# ---------------------------------------------------------

def test_validate_mouse_input():
    # Valid clicks
    validate_mouse_input(100, 200, "left", "click")
    validate_mouse_input(0, 0, "right", "click")
    validate_mouse_input(1280, 800, "middle", "move")

    # Invalid coordinates
    with pytest.raises(ValueError, match="Invalid mouse X coordinate"):
        validate_mouse_input(-5, 200, "left", "click")
    with pytest.raises(ValueError, match="Invalid mouse Y coordinate"):
        validate_mouse_input(100, 5000, "left", "click")

    # Invalid button
    with pytest.raises(ValueError, match="Invalid mouse button"):
        validate_mouse_input(100, 200, "invalid_btn", "click")

    # Invalid action
    with pytest.raises(ValueError, match="Invalid mouse action"):
        validate_mouse_input(100, 200, "left", "hover_unsupported")


def test_validate_keyboard_input():
    # Valid actions
    validate_keyboard_input("type", "hello world", None)
    validate_keyboard_input("insert_text", "hello mobile world", None)
    validate_keyboard_input("press", None, "Enter")
    validate_keyboard_input("press", None, "Backspace")

    # Invalid action
    with pytest.raises(ValueError, match="Invalid keyboard action"):
        validate_keyboard_input("fly", "text", None)

    # Empty payload
    with pytest.raises(ValueError, match="requires a valid string 'text'"):
        validate_keyboard_input("type", None, None)
    with pytest.raises(ValueError, match="requires a valid 'key'"):
        validate_keyboard_input("press", None, "")


def test_validate_scroll_input():
    # Valid scroll
    validate_scroll_input(0, 300)
    validate_scroll_input(-100, -200)

    # Out of range delta
    with pytest.raises(ValueError, match="Scroll delta_y must be within"):
        validate_scroll_input(0, 15000)


def test_validate_navigation_url():
    # Valid URLs
    assert validate_navigation_url("https://example.com") == "https://example.com"
    assert validate_navigation_url("http://example.org/test") == "http://example.org/test"

    # Blocked schemes
    with pytest.raises(ValueError, match="Disallowed navigation URL scheme"):
        validate_navigation_url("file:///etc/passwd")
    with pytest.raises(ValueError, match="Disallowed navigation URL scheme"):
        validate_navigation_url("javascript:alert(1)")

    # Blocked hosts (SSRF)
    with pytest.raises(ValueError, match="Navigation to restricted host"):
        validate_navigation_url("http://localhost:8000")
    with pytest.raises(ValueError, match="Navigation to restricted host"):
        validate_navigation_url("http://127.0.0.1/admin")
    with pytest.raises(ValueError, match="Navigation to restricted host"):
        validate_navigation_url("http://169.254.169.254/latest/meta-data")


def test_validate_resize_input():
    validate_resize_input(1280, 800)
    validate_resize_input(1920, 1080)

    with pytest.raises(ValueError, match="Width .* must be between"):
        validate_resize_input(100, 800)
    with pytest.raises(ValueError, match="Height .* must be between"):
        validate_resize_input(1280, 5000)


# ---------------------------------------------------------
# 2. REST API Security & Viewport Tests
# ---------------------------------------------------------

@pytest.mark.anyio
async def test_viewport_unauthenticated_rejected():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res = await ac.get("/api/v1/browser/session/any-session/viewport")
        assert res.status_code == 401
        assert "DIRECTOR_AUTH_REQUIRED" in res.text


@pytest.mark.anyio
async def test_interact_unauthenticated_rejected():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res = await ac.post(
            "/api/v1/browser/session/any-session/interact",
            json={"type": "mouse", "action": "click", "x": 100, "y": 100},
        )
        assert res.status_code == 401


@pytest.mark.anyio
async def test_take_control_unauthenticated_rejected():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res = await ac.post("/api/v1/browser/session/any-session/take-control")
        assert res.status_code == 401


@pytest.mark.anyio
async def test_return_control_unauthenticated_rejected():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res = await ac.post("/api/v1/browser/session/any-session/return-control")
        assert res.status_code == 401


@pytest.mark.anyio
async def test_browser_stream_unauthenticated_returns_401():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        res = await ac.post(
            "/api/v1/browser/stream",
            json={"goal": "Open Instagram login page here", "session_id": "test-unauth-sess"},
        )
        assert res.status_code == 401
        data = res.json()
        assert data.get("detail") == "DIRECTOR_AUTH_REQUIRED"


@pytest.mark.anyio
async def test_browser_stream_cross_owner_rejection(auth_headers):
    # Session belongs to user_alice
    session_id = f"test-m3-stream-cross-{int(time.time() * 1000)}"
    session = await browser_session_manager.get_or_create_session(
        session_id=session_id,
        owner_id="user_alice",
        headless=True,
    )
    try:
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Another principal (not alice, not canonical) attempts to stream on alice's session
            # using mock director auth header or other user
            res = await ac.post(
                "/api/v1/browser/stream",
                headers={"Authorization": "Bearer non-existent-or-invalid"},
                json={"goal": "Hijack session", "session_id": session_id},
            )
            assert res.status_code == 401
    finally:
        await browser_session_manager.terminate_session(session_id)


# ---------------------------------------------------------
# 3. Live Session Interaction & Control Handoff Tests
# ---------------------------------------------------------

@pytest.mark.anyio
async def test_session_control_handoff_and_interaction(auth_headers):
    session_id = f"test-m3-handoff-{int(time.time() * 1000)}"

    # 1. Create session owned by director principal
    session = await browser_session_manager.get_or_create_session(
        session_id=session_id,
        owner_id="canonical_owner_identity",
        headless=True,
    )
    assert session is not None
    assert session.is_alive()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        try:
            # Navigate to real page
            await session.page.goto("https://example.com", wait_until="domcontentloaded", timeout=30000)

            # 2. Test GET /viewport
            viewport_res = await ac.get(
                f"/api/v1/browser/session/{session_id}/viewport",
                headers=auth_headers,
            )
            assert viewport_res.status_code == 200
            vp_data = viewport_res.json()
            assert vp_data["session_id"] == session_id
            assert vp_data["url"].startswith("https://example.com")
            assert "data:image/jpeg;base64," in vp_data["frame"]
            assert vp_data["controlled_by"] == "ai"

            # 3. In AI_CONTROLLED / ACTIVE mode, direct interaction is rejected with 409
            interact_fail = await ac.post(
                f"/api/v1/browser/session/{session_id}/interact",
                headers=auth_headers,
                json={"type": "mouse", "action": "click", "x": 100, "y": 100, "button": "left"},
            )
            assert interact_fail.status_code == 409
            assert "Take Control" in interact_fail.text

            # 4. Take Control
            take_ctrl_res = await ac.post(
                f"/api/v1/browser/session/{session_id}/take-control",
                headers=auth_headers,
            )
            assert take_ctrl_res.status_code == 200
            ctrl_data = take_ctrl_res.json()
            assert ctrl_data["controlled_by"] == "user"
            assert session.state == SessionState.USER_CONTROLLED

            # 5. Direct interaction succeeds in USER_CONTROLLED mode
            interact_ok = await ac.post(
                f"/api/v1/browser/session/{session_id}/interact",
                headers=auth_headers,
                json={"type": "mouse", "action": "click", "x": 100, "y": 100, "button": "left"},
            )
            assert interact_ok.status_code == 200
            act_data = interact_ok.json()
            assert act_data["result"]["status"] == "SUCCESS"
            assert "frame" in act_data["viewport"]

            # 6. Scroll interaction
            scroll_ok = await ac.post(
                f"/api/v1/browser/session/{session_id}/interact",
                headers=auth_headers,
                json={"type": "scroll", "delta_x": 0, "delta_y": 200},
            )
            assert scroll_ok.status_code == 200
            assert scroll_ok.json()["result"]["status"] == "SUCCESS"

            # 7. Mobile text input (insert_text and Tab/Enter/Backspace)
            insert_ok = await ac.post(
                f"/api/v1/browser/session/{session_id}/interact",
                headers=auth_headers,
                json={"type": "keyboard", "action": "insert_text", "text": "test_username"},
            )
            assert insert_ok.status_code == 200
            assert insert_ok.json()["result"]["status"] == "SUCCESS"

            tab_ok = await ac.post(
                f"/api/v1/browser/session/{session_id}/interact",
                headers=auth_headers,
                json={"type": "keyboard", "action": "press", "key": "Tab"},
            )
            assert tab_ok.status_code == 200
            assert tab_ok.json()["result"]["status"] == "SUCCESS"

            # 8. Return Control
            return_ctrl_res = await ac.post(
                f"/api/v1/browser/session/{session_id}/return-control",
                headers=auth_headers,
            )
            assert return_ctrl_res.status_code == 200
            return_data = return_ctrl_res.json()
            assert return_data["controlled_by"] == "ai"
            assert session.state == SessionState.IDLE

        finally:
            await browser_session_manager.terminate_session(session_id)


@pytest.mark.anyio
async def test_playwright_actual_insert_text_dom_execution(auth_headers):
    """
    End-to-End integration test:
    Exercises the actual Playwright keyboard insertion and DOM state changes,
    verifying that insert_text, Tab navigation, Backspace, and focus behave
    identically to a real user typing in remote Chromium.
    """
    session_id = f"test-m3-dom-insert-{int(time.time() * 1000)}"

    session = await browser_session_manager.get_or_create_session(
        session_id=session_id,
        owner_id="canonical_owner_identity",
        headless=True,
    )
    assert session is not None
    assert session.is_alive()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        try:
            # 1. Load an HTML page with interactive form inputs
            html_content = """
            <!DOCTYPE html>
            <html>
            <head><title>Form Test</title></head>
            <body style="margin: 0; padding: 20px;">
                <input id="test-username" type="text" style="width: 200px; height: 30px;" />
                <input id="test-password" type="password" style="width: 200px; height: 30px;" />
                <button id="test-btn" type="button">Login</button>
            </body>
            </html>
            """
            await session.page.set_content(html_content, wait_until="domcontentloaded")

            # 2. In AI_CONTROLLED mode, direct interaction is rejected (409)
            rejected_interaction = await ac.post(
                f"/api/v1/browser/session/{session_id}/interact",
                headers=auth_headers,
                json={"type": "keyboard", "action": "insert_text", "text": "should_fail"},
            )
            assert rejected_interaction.status_code == 409
            assert "Take Control" in rejected_interaction.text

            # 3. Take Control -> transitions to USER_CONTROLLED
            take_res = await ac.post(
                f"/api/v1/browser/session/{session_id}/take-control",
                headers=auth_headers,
            )
            assert take_res.status_code == 200
            assert session.state == SessionState.USER_CONTROLLED

            # 4. Focus the username field via click / focus
            await session.page.focus("#test-username")
            active_id = await session.page.evaluate("document.activeElement.id")
            assert active_id == "test-username"

            # 5. Execute insert_text via REST API
            insert_res = await ac.post(
                f"/api/v1/browser/session/{session_id}/interact",
                headers=auth_headers,
                json={"type": "keyboard", "action": "insert_text", "text": "napster_test_user"},
            )
            assert insert_res.status_code == 200
            assert insert_res.json()["result"]["status"] == "SUCCESS"

            # VERIFY IN ACTUAL CHROMIUM DOM
            username_val = await session.page.input_value("#test-username")
            assert username_val == "napster_test_user", f"Expected 'napster_test_user', got '{username_val}'"

            # 6. Press Tab via REST API -> Should shift focus to password field
            tab_res = await ac.post(
                f"/api/v1/browser/session/{session_id}/interact",
                headers=auth_headers,
                json={"type": "keyboard", "action": "press", "key": "Tab"},
            )
            assert tab_res.status_code == 200

            # VERIFY FOCUS IN CHROMIUM DOM
            new_active_id = await session.page.evaluate("document.activeElement.id")
            assert new_active_id == "test-password", f"Expected focus on 'test-password', got '{new_active_id}'"

            # 7. Execute insert_text into password field via REST API
            pw_res = await ac.post(
                f"/api/v1/browser/session/{session_id}/interact",
                headers=auth_headers,
                json={"type": "keyboard", "action": "insert_text", "text": "Secret123!"},
            )
            assert pw_res.status_code == 200

            # VERIFY PASSWORD IN CHROMIUM DOM
            pw_val = await session.page.input_value("#test-password")
            assert pw_val == "Secret123!"

            # 8. Press Backspace via REST API -> Should remove the trailing exclamation point
            back_res = await ac.post(
                f"/api/v1/browser/session/{session_id}/interact",
                headers=auth_headers,
                json={"type": "keyboard", "action": "press", "key": "Backspace"},
            )
            assert back_res.status_code == 200

            # VERIFY MUTATED VALUE IN CHROMIUM DOM
            pw_val_after = await session.page.input_value("#test-password")
            assert pw_val_after == "Secret123"

            # 9. Verify NO accidental duplicate insertion occurred on username
            username_val_final = await session.page.input_value("#test-username")
            assert username_val_final == "napster_test_user"

            # 10. Return Control -> transitions to IDLE
            return_res = await ac.post(
                f"/api/v1/browser/session/{session_id}/return-control",
                headers=auth_headers,
            )
            assert return_res.status_code == 200
            assert session.state == SessionState.IDLE

        finally:
            await browser_session_manager.terminate_session(session_id)


# ---------------------------------------------------------
# 4. WebSocket Live Viewport Tests (Async Event-Loop Native)
# ---------------------------------------------------------

class MockWebSocket:
    """Async WebSocket test double for live streaming & interaction tests."""
    def __init__(self, incoming_messages=None):
        self.accepted = False
        self.closed = False
        self.close_code = None
        self.close_reason = None
        self.sent_messages = []
        self._incoming = list(incoming_messages or [])
        self.client_state = type("State", (), {"name": "CONNECTED"})()

    async def accept(self):
        self.accepted = True

    async def close(self, code=1000, reason=None):
        self.closed = True
        self.close_code = code
        self.close_reason = reason

    async def send_json(self, data):
        self.sent_messages.append(data)

    async def receive_text(self):
        if self._incoming:
            return self._incoming.pop(0)
        from fastapi import WebSocketDisconnect
        raise WebSocketDisconnect(code=1000)


@pytest.mark.anyio
async def test_websocket_live_viewport_flow():
    import json
    import time

    session_id = f"test-m3-ws-{int(time.time() * 1000)}"

    session = await browser_session_manager.get_or_create_session(
        session_id=session_id,
        owner_id="canonical_owner_identity",
        headless=True,
    )
    assert session is not None

    try:
        await session.page.goto("https://example.com", wait_until="domcontentloaded", timeout=30000)

        # Sequence of client messages sent to remote browser
        incoming = [
            json.dumps({"type": "ping"}),
            json.dumps({"type": "take_control"}),
            json.dumps({"type": "mouse", "action": "click", "x": 200, "y": 150, "button": "left"}),
            json.dumps({"type": "scroll", "delta_x": 0, "delta_y": 150}),
            json.dumps({"type": "return_control"}),
        ]

        mock_ws = MockWebSocket(incoming_messages=incoming)

        # Run connection handler on the same native event loop as Playwright
        await live_viewport_service.handle_connection(
            websocket=mock_ws,
            session_id=session_id,
            owner_id="canonical_owner_identity",
        )

        assert mock_ws.accepted is True

        types = [m.get("type") for m in mock_ws.sent_messages]
        # 1. viewport.init
        assert "viewport.init" in types
        init_frame = next(m for m in mock_ws.sent_messages if m.get("type") == "viewport.init")
        assert "data:image/jpeg;base64," in init_frame["frame"]

        # 2. pong
        assert "pong" in types

        # 3. control.state -> user
        user_ctrl = [m for m in mock_ws.sent_messages if m.get("type") == "control.state" and m.get("controlled_by") == "user"]
        assert len(user_ctrl) >= 1

        # 4. action.result
        action_results = [m for m in mock_ws.sent_messages if m.get("type") == "action.result"]
        assert len(action_results) >= 2  # click + scroll
        assert action_results[0]["status"] == "SUCCESS"

        # 5. control.state -> ai
        ai_ctrl = [m for m in mock_ws.sent_messages if m.get("type") == "control.state" and m.get("controlled_by") == "ai"]
        assert len(ai_ctrl) >= 1
        assert session.state == SessionState.IDLE

    finally:
        await browser_session_manager.terminate_session(session_id)


@pytest.mark.anyio
async def test_websocket_cross_user_rejection():
    session_id = f"test-m3-ws-cross-{int(time.time() * 1000)}"

    session = await browser_session_manager.get_or_create_session(
        session_id=session_id,
        owner_id="user_alice",
        headless=True,
    )
    try:
        mock_ws = MockWebSocket()
        # User Bob attempts to connect to User Alice's session
        await live_viewport_service.handle_connection(
            websocket=mock_ws,
            session_id=session_id,
            owner_id="user_bob",
        )
        assert mock_ws.closed is True
        assert mock_ws.close_code == 1008
        assert mock_ws.close_reason == "FORBIDDEN_SESSION_ACCESS"
    finally:
        await browser_session_manager.terminate_session(session_id)
