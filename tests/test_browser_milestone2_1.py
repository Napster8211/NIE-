"""
NapsterTec AI - Milestone 2.1 Test Suite
Module: tests/test_browser_milestone2_1.py

Validates Milestone 2.1 Security & Lifecycle Hardening:
- Authentication enforcement on all browser endpoints (401 unauthenticated)
- Tenant/user session ownership and cross-user isolation (403 forbidden)
- Authorized owner and admin bypass (200 OK)
- SessionVault path traversal rejection and permission restrictions
- Background session cleaner loop scheduling, idle expiration, and lock protection
- State transitions from ACTIVE to IDLE and state machine robustness
"""
import asyncio
import os
import pytest
from httpx import AsyncClient, ASGITransport
from pathlib import Path

from app.main import app
from app.browser.session_manager import (
    browser_session_manager,
    BrowserSession,
    SessionState,
    VALID_TRANSITIONS,
    InvalidStateTransitionError,
)
from app.browser.session_vault import SessionVault, validate_profile_name
from app.services.director_auth_service import DirectorPrincipal


# --- 1. SESSION VAULT SECURITY TESTS (SEC-02) ---

def test_session_vault_path_traversal_protection(tmp_path):
    vault = SessionVault(storage_dir=tmp_path / "vault")
    
    # Prohibited traversal patterns
    bad_profiles = [
        "../evil",
        "..\\evil",
        "../../etc/passwd",
        "/etc/passwd",
        "C:\\Windows\\System32\\cmd.exe",
        "sub/folder/profile",
        "foo/bar",
        "foo\\bar",
        "",
        "   ",
        "a" * 150,  # exceeds 128 char limit
        "evil\x00profile",
    ]
    for bad in bad_profiles:
        with pytest.raises(ValueError):
            validate_profile_name(bad)
        with pytest.raises(ValueError):
            vault.get_storage_path(bad)

    # Valid profile names
    valid_profiles = ["user_123", "profile-alpha", "my.profile_01", "normal_session"]
    for valid in valid_profiles:
        path = vault.get_storage_path(valid)
        assert path.name == f"{valid.lower()}.json"
        assert path.is_relative_to(vault.storage_dir)


def test_session_vault_atomic_write_and_load(tmp_path):
    vault = SessionVault(storage_dir=tmp_path / "vault")
    data = {"cookies": [{"name": "test_cookie", "value": "xyz"}], "origins": []}
    
    saved_path = vault.save_session("profile_alpha", data)
    assert saved_path.exists()
    assert vault.has_session("profile_alpha")
    
    loaded = vault.load_session("profile_alpha")
    assert loaded == data
    
    deleted = vault.delete_session("profile_alpha")
    assert deleted is True
    assert not vault.has_session("profile_alpha")


def test_session_vault_optional_encryption(tmp_path, monkeypatch):
    from cryptography.fernet import Fernet
    key = Fernet.generate_key().decode()
    monkeypatch.setenv("NIE_SESSION_VAULT_KEY", key)
    
    vault = SessionVault(storage_dir=tmp_path / "encrypted_vault")
    secret_data = {"secret_token": "super_secret_browser_state"}
    
    saved_path = vault.save_session("secure_profile", secret_data)
    with open(saved_path, "rb") as f:
        raw_bytes = f.read()
    # Stored bytes on disk should be ciphertext, not plaintext JSON
    assert b"super_secret_browser_state" not in raw_bytes
    
    # Vault decrypts seamlessly
    loaded = vault.load_session("secure_profile")
    assert loaded == secret_data


# --- 2. SESSION LIFECYCLE & STATE MACHINE TESTS (LIFE-01 & STATE-01) ---

@pytest.mark.anyio
async def test_active_to_idle_state_transitions():
    session = BrowserSession(session_id="test_m21_state", owner_id="owner_1")
    assert session.state == SessionState.INITIALIZING
    
    # INITIALIZING -> ACTIVE
    session.transition_to(SessionState.ACTIVE, reason="Page ready")
    assert session.state == SessionState.ACTIVE
    
    # ACTIVE -> IDLE (STATE-01 requirement)
    session.transition_to(SessionState.IDLE, reason="Goal completed")
    assert session.state == SessionState.IDLE
    
    # IDLE -> ACTIVE (Resuming subsequent task)
    session.transition_to(SessionState.ACTIVE, reason="New user goal")
    assert session.state == SessionState.ACTIVE
    
    # ACTIVE -> WAITING_FOR_USER
    session.transition_to(SessionState.WAITING_FOR_USER, reason="2FA required")
    assert session.state == SessionState.WAITING_FOR_USER
    
    # WAITING_FOR_USER -> IDLE (User finishes or closes prompt)
    session.transition_to(SessionState.IDLE, reason="Auth handled")
    assert session.state == SessionState.IDLE
    
    # IDLE -> CLOSED
    session.transition_to(SessionState.CLOSED, reason="Shutdown")
    assert session.state == SessionState.CLOSED


@pytest.mark.anyio
async def test_cleaner_skips_active_locked_sessions():
    try:
        session = await browser_session_manager.get_or_create_session(
            session_id="test_locked_session",
            owner_id="owner_test",
            headless=True,
        )
        # Simulate an expired idle time
        session.last_active_at = 0.0
        session.idle_timeout_seconds = 0.1
        
        # Hold the session lock to simulate an active in-flight browser step
        lock = session.get_lock()
        await lock.acquire()
        try:
            assert lock.locked()
            cleaned = await browser_session_manager.cleanup_expired_sessions()
            # Should NOT clean the locked session
            assert cleaned == 0
            still_alive = await browser_session_manager.get_session("test_locked_session")
            assert still_alive is not None
        finally:
            lock.release()

        # Now with lock released, sweeping terminates it
        cleaned = await browser_session_manager.cleanup_expired_sessions()
        assert cleaned >= 1
        assert await browser_session_manager.get_session("test_locked_session") is None
    finally:
        await browser_session_manager.terminate_session("test_locked_session")


@pytest.mark.anyio
async def test_background_cleanup_task_lifecycle():
    assert browser_session_manager._cleanup_task is None
    browser_session_manager.start_cleanup_loop(interval_seconds=0.1)
    assert browser_session_manager._cleanup_task is not None
    assert not browser_session_manager._cleanup_task.done()
    
    await browser_session_manager.stop_cleanup_loop()
    assert browser_session_manager._cleanup_task is None


# --- 3. API AUTHENTICATION & AUTHORIZATION TESTS (SEC-01) ---

@pytest.mark.anyio
async def test_api_unauthenticated_requests_rejected():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        # GET /api/v1/browser/sessions without auth
        res = await client.get("/api/v1/browser/sessions")
        assert res.status_code == 401
        assert "DIRECTOR_AUTH_REQUIRED" in res.text

        # GET /api/v1/browser/session/s1 without auth
        res = await client.get("/api/v1/browser/session/s1")
        assert res.status_code == 401
        assert "DIRECTOR_AUTH_REQUIRED" in res.text

        # DELETE /api/v1/browser/session/s1 without auth
        res = await client.delete("/api/v1/browser/session/s1")
        assert res.status_code == 401
        assert "DIRECTOR_AUTH_REQUIRED" in res.text

        # POST /api/v1/browser/stream without auth
        res = await client.post("/api/v1/browser/stream", json={"goal": "open example.com"})
        assert res.status_code == 401
        assert "DIRECTOR_AUTH_REQUIRED" in res.text


@pytest.mark.anyio
async def test_cross_user_isolation(monkeypatch):
    test_owner_key = "test_director_admin_key_m21"
    monkeypatch.setenv("NIE_OWNER_KEY", test_owner_key)
    
    try:
        # Create session owned by user_a
        session_a = await browser_session_manager.get_or_create_session(
            session_id="user_a_session_001",
            owner_id="firebase:user_a",
            headless=True,
        )
        assert session_a.owner_id == "firebase:user_a"

        # Create session owned by user_b
        session_b = await browser_session_manager.get_or_create_session(
            session_id="user_b_session_002",
            owner_id="firebase:user_b",
            headless=True,
        )
        assert session_b.owner_id == "firebase:user_b"

        transport = ASGITransport(app=app)
        
        # We simulate authentication principal via dependency override
        from app.services.director_auth_service import require_director_access

        async def override_user_a():
            return DirectorPrincipal(
                owner_id="firebase:user_a",
                owner_uid="user_a",
                auth_method="firebase_bearer",
            )

        async def override_user_b():
            return DirectorPrincipal(
                owner_id="firebase:user_b",
                owner_uid="user_b",
                auth_method="firebase_bearer",
            )

        # 1. As User A:
        app.dependency_overrides[require_director_access] = override_user_a
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Listing sessions only returns user_a's session
            res = await client.get("/api/v1/browser/sessions")
            assert res.status_code == 200
            sessions = res.json()
            session_ids = [s["session_id"] for s in sessions]
            assert "user_a_session_001" in session_ids
            assert "user_b_session_002" not in session_ids

            # Accessing user_a's session succeeds
            res_a = await client.get("/api/v1/browser/session/user_a_session_001")
            assert res_a.status_code == 200

            # Attempting to access user_b's session returns 403 Forbidden
            res_b = await client.get("/api/v1/browser/session/user_b_session_002")
            assert res_b.status_code == 403
            assert res_b.json().get("detail") == "FORBIDDEN_SESSION_ACCESS"

            # Attempting to delete user_b's session returns 403 Forbidden
            del_b = await client.delete("/api/v1/browser/session/user_b_session_002")
            assert del_b.status_code == 403
            assert del_b.json().get("detail") == "FORBIDDEN_SESSION_ACCESS"

            # Attempting to hijack user_b's session via stream returns 403 Forbidden
            stream_b = await client.post(
                "/api/v1/browser/stream",
                json={"session_id": "user_b_session_002", "goal": "steal session"},
            )
            assert stream_b.status_code == 403
            assert stream_b.json().get("detail") == "FORBIDDEN_SESSION_ACCESS"

        # 2. As Admin (canonical_owner_identity):
        async def override_admin():
            return DirectorPrincipal(
                owner_id="canonical_owner_identity",
                owner_uid="canonical_owner_identity",
                auth_method="owner_key",
            )

        app.dependency_overrides[require_director_access] = override_admin
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            # Admin can list all sessions
            res = await client.get("/api/v1/browser/sessions")
            assert res.status_code == 200
            admin_sessions = [s["session_id"] for s in res.json()]
            assert "user_a_session_001" in admin_sessions
            assert "user_b_session_002" in admin_sessions

            # Admin can access user_a session
            res_a = await client.get("/api/v1/browser/session/user_a_session_001")
            assert res_a.status_code == 200

        app.dependency_overrides.clear()
    finally:
        await browser_session_manager.terminate_session("user_a_session_001")
        await browser_session_manager.terminate_session("user_b_session_002")
        app.dependency_overrides.clear()
