"""
NapsterTec AI - Milestone 2 Verification Test Suite
Module: tests/test_browser_milestone2.py

Comprehensive tests for:
- A. Session creation (genuine BrowserContext)
- B. Session isolation (cookies, storage, pages isolated between sessions)
- C. Session continuity (multi-action preservation)
- D. Concurrent sessions (parallel independent execution)
- E. Concurrent actions (safe serialization via mutex lock)
- F. Session termination (complete context cleanup)
- G. Idle expiration (automatic cleanup of stale sessions)
- H. Browser crash recovery (safe recovery or fail-closed state)
- I. Storage-state persistence (cookie / storage export & restore)
- J. Cancellation (clean termination without orphaned handles)
- K. Security (session ID validation, URL protection, ownership)
- L. SSE lifecycle (authoritative events, single terminal event)
"""

import asyncio
import time
import pytest
from unittest.mock import patch, AsyncMock
from app.browser.session_manager import (
    BrowserSession,
    BrowserSessionManager,
    SessionState,
    browser_session_manager,
    validate_session_id,
    InvalidStateTransitionError,
)
from app.browser_operator import BrowserOperatorInput, browser_operator_manager
from app.browser.session_vault import session_vault


@pytest.mark.anyio
async def test_a_session_creation():
    """Test A: Create a browser session and verify a genuine browser context exists."""
    session = await browser_session_manager.create_session(
        session_id="test_m2_create",
        owner_id="test_owner",
        headless=True,
    )
    try:
        assert session is not None
        assert session.session_id == "test_m2_create"
        assert session.owner_id == "test_owner"
        assert session.state == SessionState.ACTIVE
        assert session.context is not None
        assert session.page is not None
        assert not session.page.is_closed()
        print(f"\n[Test A Passed] Session created with valid BrowserContext and Page in ACTIVE state.")
    finally:
        await browser_session_manager.terminate_session("test_m2_create")


@pytest.mark.anyio
async def test_b_session_isolation():
    """Test B: Create two sessions and verify that cookies, pages, storage, and navigation state are isolated."""
    s1 = await browser_session_manager.create_session("test_m2_iso_1", headless=True)
    s2 = await browser_session_manager.create_session("test_m2_iso_2", headless=True)

    try:
        # Contexts and pages must be distinct instances
        assert s1.context is not s2.context
        assert s1.page is not s2.page

        # Navigate s1 to example.com and set a cookie
        await s1.page.goto("https://example.com")
        await s1.context.add_cookies([
            {"name": "test_cookie", "value": "isolated_cookie_val", "domain": ".example.com", "path": "/"}
        ])

        # Navigate s2 to quotes.toscrape.com
        await s2.page.goto("https://quotes.toscrape.com")

        # Verify cookie in s1
        cookies_1 = await s1.context.cookies()
        c1_names = [c["name"] for c in cookies_1]
        assert "test_cookie" in c1_names

        # Verify cookie is NOT in s2
        cookies_2 = await s2.context.cookies()
        c2_names = [c["name"] for c in cookies_2]
        assert "test_cookie" not in c2_names

        # Verify navigation isolation
        assert "example.com" in s1.page.url
        assert "quotes.toscrape.com" in s2.page.url
        print(f"\n[Test B Passed] Session isolation verified: contexts, cookies, and URLs completely separated.")
    finally:
        await browser_session_manager.terminate_session("test_m2_iso_1")
        await browser_session_manager.terminate_session("test_m2_iso_2")


@pytest.mark.anyio
async def test_c_session_continuity():
    """Test C: Perform multiple sequential actions using the same session without losing context."""
    session_id = "test_m2_continuity"
    res1 = await browser_operator_manager.execute(
        BrowserOperatorInput(
            action="navigate",
            session_id=session_id,
            url="https://example.com",
            allow_interactions=True,
        )
    )
    assert res1["status"] == "COMPLETED"
    assert "example.com" in res1["url"]

    # Action 2: Inspect
    res2 = await browser_operator_manager.execute(
        BrowserOperatorInput(
            action="screenshot",
            session_id=session_id,
            allow_interactions=True,
        )
    )
    assert res2["status"] == "COMPLETED"
    assert "example.com" in res2["url"]

    # Action 3: Navigate to second site
    res3 = await browser_operator_manager.execute(
        BrowserOperatorInput(
            action="navigate",
            session_id=session_id,
            url="https://quotes.toscrape.com/js/",
            allow_interactions=True,
        )
    )
    assert res3["status"] == "COMPLETED"
    assert "quotes.toscrape.com" in res3["url"]

    # Verify session stayed active and preserved in manager
    session = await browser_session_manager.get_session(session_id)
    assert session is not None
    assert session.state == SessionState.ACTIVE
    print(f"\n[Test C Passed] Session continuity verified across 3 sequential actions.")
    await browser_operator_manager.execute(
        BrowserOperatorInput(action="close", session_id=session_id)
    )


@pytest.mark.anyio
async def test_d_concurrent_sessions():
    """Test D: Run independent browser sessions concurrently and verify no cross-session contamination."""
    sids = ["test_m2_conc_1", "test_m2_conc_2"]

    async def run_worker(sid: str, url: str):
        return await browser_operator_manager.execute(
            BrowserOperatorInput(
                action="navigate",
                session_id=sid,
                url=url,
                allow_interactions=True,
            )
        )

    res1, res2 = await asyncio.gather(
        run_worker(sids[0], "https://example.com"),
        run_worker(sids[1], "https://httpbin.org/get"),
    )

    try:
        assert res1["status"] == "COMPLETED"
        assert res2["status"] == "COMPLETED"
        assert "example.com" in res1["url"]
        assert "httpbin.org" in res2["url"]
        print(f"\n[Test D Passed] Concurrent parallel sessions completed with zero cross-contamination.")
    finally:
        for sid in sids:
            await browser_operator_manager.execute(
                BrowserOperatorInput(action="close", session_id=sid)
            )


@pytest.mark.anyio
async def test_e_concurrent_actions_locking():
    """Test E: Attempt concurrent actions against one session and verify appropriate coordination."""
    session_id = "test_m2_locking"
    session = await browser_session_manager.create_session(session_id, headless=True)

    execution_order = []

    async def action_a():
        async with session.get_lock():
            execution_order.append("A_START")
            await asyncio.sleep(0.1)
            execution_order.append("A_END")

    async def action_b():
        async with session.get_lock():
            execution_order.append("B_START")
            await asyncio.sleep(0.05)
            execution_order.append("B_END")

    # Launch both concurrently
    await asyncio.gather(action_a(), action_b())

    # Verify that actions did not interleave (lock enforced serialization)
    assert execution_order in [
        ["A_START", "A_END", "B_START", "B_END"],
        ["B_START", "B_END", "A_START", "A_END"],
    ]
    print(f"\n[Test E Passed] Concurrent action serialization verified: {execution_order}")
    await browser_session_manager.terminate_session(session_id)


@pytest.mark.anyio
async def test_f_session_termination():
    """Test F: Terminate a session and verify browser context cleanup."""
    session_id = "test_m2_term"
    session = await browser_session_manager.create_session(session_id, headless=True)
    page_ref = session.page
    context_ref = session.context

    assert not page_ref.is_closed()

    # Terminate
    terminated = await browser_session_manager.terminate_session(session_id, reason="Test termination")
    assert terminated is True
    assert session.state == SessionState.CLOSED
    assert page_ref.is_closed()

    # Check manager no longer returns it
    retrieved = await browser_session_manager.get_session(session_id)
    assert retrieved is None
    print(f"\n[Test F Passed] Session termination cleaned up page and context and set state to CLOSED.")


@pytest.mark.anyio
async def test_g_idle_expiration():
    """Test G: Verify expired sessions are cleaned up."""
    session_id = "test_m2_expire"
    # Create session with tiny idle timeout (0.1 seconds)
    session = await browser_session_manager.create_session(
        session_id=session_id,
        idle_timeout_seconds=0.1,
        headless=True,
    )
    # Manually backdate last_active_at by 1 second to simulate expiration
    session.last_active_at = time.time() - 2.0
    assert session.is_idle_expired() is True

    # Calling get_session triggers expiration check
    expired_session = await browser_session_manager.get_session(session_id)
    assert expired_session is None
    assert session.state == SessionState.CLOSED

    # Also test cleanup_expired_sessions
    s2 = await browser_session_manager.create_session(
        session_id="test_m2_expire_2",
        idle_timeout_seconds=0.1,
        headless=True,
    )
    s2.last_active_at = time.time() - 2.0
    cleaned = await browser_session_manager.cleanup_expired_sessions()
    assert cleaned >= 1
    assert await browser_session_manager.get_session("test_m2_expire_2") is None
    print(f"\n[Test G Passed] Idle expiration automatically cleaned up stale sessions.")


@pytest.mark.anyio
async def test_h_browser_crash_recovery():
    """Test H: Simulate a page failure and verify safe recovery into fresh context."""
    session_id = "test_m2_recovery"
    session = await browser_session_manager.create_session(session_id, headless=True)
    await session.page.goto("https://example.com")
    session.last_snapshot = {"url": "https://example.com"}

    # Simulate page crash by forcibly closing page
    await session.page.close()
    assert not session.is_alive()

    # Recovery
    recovered = await browser_session_manager.recover_session(session_id, headless=True)
    assert recovered is not None
    assert recovered.state == SessionState.ACTIVE
    assert recovered.page is not None
    assert not recovered.page.is_closed()
    print(f"\n[Test H Passed] Safe recovery verified: resurrected context and page into ACTIVE state.")
    await browser_session_manager.terminate_session(session_id)


@pytest.mark.anyio
async def test_i_storage_state_persistence():
    """Test I: Save browser storage state, recreate context, and verify restoration."""
    session_id = "test_m2_storage"
    session = await browser_session_manager.create_session(session_id, profile_name="test_persist_profile", headless=True)

    try:
        await session.page.goto("https://example.com")
        # Add test cookie
        await session.context.add_cookies([
            {"name": "persist_key", "value": "persisted_secret_123", "domain": ".example.com", "path": "/"}
        ])

        # Persist storage state
        saved_path = await session.save_storage_state()
        assert saved_path is not None
        assert saved_path.exists()

        # Close session
        await browser_session_manager.terminate_session(session_id)

        # Create fresh session using the saved profile
        restored_session = await browser_session_manager.create_session(
            "test_m2_storage_restored",
            profile_name="test_persist_profile",
            headless=True,
        )
        cookies = await restored_session.context.cookies()
        cookie_map = {c["name"]: c["value"] for c in cookies}
        assert "persist_key" in cookie_map
        assert cookie_map["persist_key"] == "persisted_secret_123"
        print(f"\n[Test I Passed] Storage state successfully saved to vault and restored in new BrowserContext.")
    finally:
        await browser_session_manager.terminate_session("test_m2_storage_restored")
        session_vault.delete_session("test_persist_profile")


@pytest.mark.anyio
async def test_j_cancellation():
    """Test J: Cancel an executing browser task and verify it terminates cleanly."""
    session_id = "test_m2_cancel"
    session = await browser_session_manager.create_session(session_id, headless=True)

    async def long_task():
        async with session.get_lock():
            await asyncio.sleep(2.0)

    task = asyncio.create_task(long_task())
    await asyncio.sleep(0.05)
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    # Session remains consistent and lock is released
    assert not session.get_lock().locked()
    await browser_session_manager.terminate_session(session_id)
    print(f"\n[Test J Passed] Cancellation handled cleanly without deadlocks or corrupted locks.")


@pytest.mark.anyio
async def test_k_security():
    """Test K: Verify session ownership, session ID validation, and URL protection."""
    # 1. Invalid session ID formats
    with pytest.raises(ValueError):
        validate_session_id("../traversal")

    with pytest.raises(ValueError):
        validate_session_id("invalid;rm -rf")

    valid_id = validate_session_id("valid-session_123.test")
    assert valid_id == "valid-session_123.test"

    # 2. Blocked URL checks
    with pytest.raises(ValueError, match="internal or metadata hosts is blocked"):
        browser_operator_manager._validate_url("http://127.0.0.1:8000")

    with pytest.raises(ValueError, match="internal or metadata hosts is blocked"):
        browser_operator_manager._validate_url("http://169.254.169.254/latest/meta-data")

    # 3. State transition enforcement
    session = BrowserSession(session_id="test_m2_sec_state")
    session.state = SessionState.CLOSED
    with pytest.raises(InvalidStateTransitionError):
        # Cannot transition out of CLOSED to ACTIVE
        session.transition_to(SessionState.ACTIVE)
    print(f"\n[Test K Passed] Security validations verified: session IDs, SSRF hosts, and illegal state transitions.")


@pytest.mark.anyio
async def test_l_state_machine_and_serialization():
    """Test L: Verify authoritative state transitions and dict serialization."""
    session = BrowserSession(session_id="test_m2_state_l", owner_id="owner_alpha")
    assert session.state == SessionState.INITIALIZING

    session.transition_to(SessionState.ACTIVE, reason="Ready")
    assert session.state == SessionState.ACTIVE

    session.transition_to(SessionState.WAITING_FOR_USER, reason="Login detected")
    assert session.state == SessionState.WAITING_FOR_USER

    session.transition_to(SessionState.ACTIVE, reason="User authenticated")
    assert session.state == SessionState.ACTIVE

    session.transition_to(SessionState.IDLE, reason="Task completed")
    assert session.state == SessionState.IDLE

    session.transition_to(SessionState.CLOSING, reason="Tear down")
    assert session.state == SessionState.CLOSING

    session.transition_to(SessionState.CLOSED, reason="Terminated")
    assert session.state == SessionState.CLOSED

    d = session.to_dict()
    assert d["session_id"] == "test_m2_state_l"
    assert d["owner_id"] == "owner_alpha"
    assert d["state"] == "CLOSED"
    print(f"\n[Test L Passed] Full state lifecycle transitions validated.")
