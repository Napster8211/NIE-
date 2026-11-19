"""
NapsterTec AI - Browser Session Manager
Module: app/browser/session_manager.py

Authoritative lifecycle, isolation, state machine, and cleanup manager
for browser sessions across the NapsterTec Intelligence Engine.
"""

import asyncio
import logging
import re
import time
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Coroutine, Dict, List, Optional

from playwright.async_api import BrowserContext, Page

from app.browser.browser_pool import browser_pool
from app.browser.session_vault import session_vault

logger = logging.getLogger(__name__)


class SessionState(str, Enum):
    INITIALIZING = "INITIALIZING"
    ACTIVE = "ACTIVE"
    AI_CONTROLLED = "AI_CONTROLLED"
    USER_CONTROLLED = "USER_CONTROLLED"
    IDLE = "IDLE"
    WAITING_FOR_USER = "WAITING_FOR_USER"
    RECOVERING = "RECOVERING"
    CLOSING = "CLOSING"
    CLOSED = "CLOSED"
    FAILED = "FAILED"


VALID_TRANSITIONS: Dict[SessionState, set] = {
    SessionState.INITIALIZING: {
        SessionState.ACTIVE,
        SessionState.AI_CONTROLLED,
        SessionState.IDLE,
        SessionState.FAILED,
        SessionState.CLOSED,
    },
    SessionState.ACTIVE: {
        SessionState.AI_CONTROLLED,
        SessionState.USER_CONTROLLED,
        SessionState.IDLE,
        SessionState.WAITING_FOR_USER,
        SessionState.RECOVERING,
        SessionState.CLOSING,
        SessionState.FAILED,
        SessionState.CLOSED,
    },
    SessionState.AI_CONTROLLED: {
        SessionState.ACTIVE,
        SessionState.USER_CONTROLLED,
        SessionState.IDLE,
        SessionState.WAITING_FOR_USER,
        SessionState.RECOVERING,
        SessionState.CLOSING,
        SessionState.FAILED,
        SessionState.CLOSED,
    },
    SessionState.USER_CONTROLLED: {
        SessionState.ACTIVE,
        SessionState.AI_CONTROLLED,
        SessionState.IDLE,
        SessionState.WAITING_FOR_USER,
        SessionState.RECOVERING,
        SessionState.CLOSING,
        SessionState.FAILED,
        SessionState.CLOSED,
    },
    SessionState.IDLE: {
        SessionState.ACTIVE,
        SessionState.AI_CONTROLLED,
        SessionState.USER_CONTROLLED,
        SessionState.WAITING_FOR_USER,
        SessionState.RECOVERING,
        SessionState.CLOSING,
        SessionState.FAILED,
        SessionState.CLOSED,
    },
    SessionState.WAITING_FOR_USER: {
        SessionState.ACTIVE,
        SessionState.AI_CONTROLLED,
        SessionState.USER_CONTROLLED,
        SessionState.IDLE,
        SessionState.CLOSING,
        SessionState.FAILED,
        SessionState.CLOSED,
    },
    SessionState.RECOVERING: {
        SessionState.ACTIVE,
        SessionState.AI_CONTROLLED,
        SessionState.IDLE,
        SessionState.FAILED,
        SessionState.CLOSED,
    },
    SessionState.CLOSING: {
        SessionState.CLOSED,
        SessionState.FAILED,
    },
    SessionState.CLOSED: set(),
    SessionState.FAILED: {
        SessionState.RECOVERING,
        SessionState.CLOSED,
    },
}


class InvalidStateTransitionError(Exception):
    """Raised when an illegal session state transition is attempted."""
    pass


class SessionExpiredError(Exception):
    """Raised when an operation is attempted on an expired session."""
    pass


_SESSION_ID_REGEX = re.compile(r"^[a-zA-Z0-9_\-\.]{1,128}$")


def validate_session_id(session_id: str) -> str:
    """Validate session ID format to prevent path traversal or injection attacks."""
    clean = str(session_id).strip()
    if not clean or not _SESSION_ID_REGEX.match(clean):
        raise ValueError(
            f"Invalid session_id '{session_id}'. Must be 1-128 alphanumeric, dash, underscore, or dot characters."
        )
    return clean


class BrowserSession:
    """Represents a single isolated browser session with its own context, state, and lifecycle."""

    def __init__(
        self,
        session_id: str,
        owner_id: str = "default",
        profile_name: Optional[str] = None,
        idle_timeout_seconds: float = 300.0,
        max_lifetime_seconds: float = 3600.0,
    ) -> None:
        self.session_id: str = validate_session_id(session_id)
        self.owner_id: str = owner_id.strip() or "default"
        self.profile_name: Optional[str] = profile_name.strip() if profile_name else None
        self.state: SessionState = SessionState.INITIALIZING
        self.state_reason: str = "Session created"

        self.created_at: float = time.time()
        self.last_active_at: float = self.created_at
        self.idle_timeout_seconds: float = max(0.05, float(idle_timeout_seconds))
        self.max_lifetime_seconds: float = max(0.1, float(max_lifetime_seconds))

        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        self._lock: Optional[asyncio.Lock] = None
        self._loop = None
        self.metadata: Dict[str, Any] = {}
        self.last_snapshot: Optional[Dict[str, Any]] = None
        self.viewport_size: Dict[str, int] = {"width": 1280, "height": 800}

    def is_user_controlled(self) -> bool:
        return self.state == SessionState.USER_CONTROLLED

    def is_ai_controlled(self) -> bool:
        return self.state in {SessionState.ACTIVE, SessionState.AI_CONTROLLED}

    def get_lock(self) -> asyncio.Lock:
        """Lazily initialize an event-loop-aware lock for coordinating concurrent actions."""
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if self._lock is None or self._loop != loop:
            self._lock = asyncio.Lock()
            self._loop = loop
        return self._lock

    def touch(self) -> None:
        """Update activity timestamp."""
        self.last_active_at = time.time()

    def is_idle_expired(self) -> bool:
        """Check if session exceeded idle timeout while not actively executing."""
        if self.state in {SessionState.CLOSED, SessionState.FAILED}:
            return True
        if self._lock is not None and self._lock.locked():
            return False
        now = time.time()
        return (now - self.last_active_at) > self.idle_timeout_seconds

    def is_lifetime_expired(self) -> bool:
        """Check if session exceeded maximum total lifetime."""
        now = time.time()
        return (now - self.created_at) > self.max_lifetime_seconds

    def transition_to(self, new_state: SessionState, reason: str = "") -> None:
        """Validate and transition to a new lifecycle state."""
        if self.state == new_state:
            self.state_reason = reason or self.state_reason
            return

        valid_targets = VALID_TRANSITIONS.get(self.state, set())
        if new_state not in valid_targets:
            raise InvalidStateTransitionError(
                f"Cannot transition session '{self.session_id}' from {self.state.value} to {new_state.value}. "
                f"Valid targets: {[s.value for s in valid_targets]}"
            )

        old_state = self.state
        self.state = new_state
        self.state_reason = reason or f"Transitioned from {old_state.value}"
        self.touch()
        logger.info(
            "[BrowserSession:%s] State changed %s -> %s (reason: %s)",
            self.session_id,
            old_state.value,
            new_state.value,
            self.state_reason,
        )

    def is_alive(self) -> bool:
        """Return True if session page and context are open and responsive."""
        if self.state in {SessionState.CLOSED, SessionState.FAILED, SessionState.CLOSING}:
            return False
        if self.page is None or self.page.is_closed():
            return False
        if self.context is None:
            return False
        return True

    async def save_storage_state(self) -> Optional[Path]:
        """Export browser storage state (cookies, localStorage) to SessionVault."""
        if not self.context:
            return None
        profile_key = self.profile_name or f"session_{self.session_id}"
        try:
            state = await self.context.storage_state()
            saved_path = session_vault.save_session(profile_key, state)
            logger.info(
                "[BrowserSession:%s] Storage state persisted to %s",
                self.session_id,
                saved_path,
            )
            return saved_path
        except Exception as e:
            logger.warning(
                "[BrowserSession:%s] Failed to export storage state: %s",
                self.session_id,
                e,
            )
            return None

    async def close(self, reason: str = "Closed by caller") -> None:
        """Cleanly terminate pages and context and release memory."""
        if self.state in {SessionState.CLOSED, SessionState.FAILED}:
            return

        try:
            self.transition_to(SessionState.CLOSING, reason=reason)
        except InvalidStateTransitionError:
            pass

        if self.page and not self.page.is_closed():
            try:
                await self.page.close()
            except Exception as e:
                logger.debug("[BrowserSession:%s] Page close error: %s", self.session_id, e)
            self.page = None

        if self.context:
            try:
                await self.context.close()
            except Exception as e:
                logger.debug("[BrowserSession:%s] Context close error: %s", self.session_id, e)
            self.context = None

        session_key = f"standard_chat_{self.session_id}_ephemeral"
        await browser_pool.close_session(session_key)

        try:
            self.transition_to(SessionState.CLOSED, reason=reason)
        except InvalidStateTransitionError:
            self.state = SessionState.CLOSED
            self.state_reason = reason

    def to_dict(self) -> Dict[str, Any]:
        """Return a safe summary dict of session status."""
        snapshot = self.last_snapshot or {}
        return {
            "session_id": self.session_id,
            "owner_id": self.owner_id,
            "state": self.state.value,
            "state_reason": self.state_reason,
            "url": snapshot.get("url", "about:blank"),
            "title": snapshot.get("title", ""),
            "created_at": self.created_at,
            "last_active_at": self.last_active_at,
            "idle_timeout_seconds": self.idle_timeout_seconds,
            "max_lifetime_seconds": self.max_lifetime_seconds,
            "has_storage_profile": self.profile_name is not None,
            "viewport_size": self.viewport_size,
            "is_user_controlled": self.is_user_controlled(),
            "is_ai_controlled": self.is_ai_controlled(),
            "screenshot_data_url": snapshot.get("screenshot_data_url"),
        }


class BrowserSessionManager:
    """Authoritative singleton coordinating all BrowserSession instances."""

    def __init__(self) -> None:
        self._sessions: Dict[str, BrowserSession] = {}
        self._lock: Optional[asyncio.Lock] = None
        self._loop = None
        self._cleanup_task: Optional[asyncio.Task] = None

    def _get_lock(self) -> asyncio.Lock:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if self._lock is None or self._loop != loop:
            self._lock = asyncio.Lock()
            self._loop = loop
        return self._lock

    async def get_session(self, session_id: str) -> Optional[BrowserSession]:
        """Retrieve existing session if active and not expired."""
        valid_id = validate_session_id(session_id)
        async with self._get_lock():
            session = self._sessions.get(valid_id)
            if not session:
                return None

            if session.is_lifetime_expired():
                logger.info(
                    "[SessionManager] Session '%s' lifetime expired. Closing.",
                    valid_id,
                )
                await session.close("Lifetime expired")
                self._sessions.pop(valid_id, None)
                return None

            if session.is_idle_expired():
                logger.info(
                    "[SessionManager] Session '%s' idle timeout expired. Closing.",
                    valid_id,
                )
                await session.close("Idle timeout expired")
                self._sessions.pop(valid_id, None)
                return None

            return session

    async def create_session(
        self,
        session_id: str,
        owner_id: str = "default",
        profile_name: Optional[str] = None,
        idle_timeout_seconds: float = 300.0,
        max_lifetime_seconds: float = 3600.0,
        headless: bool = True,
    ) -> BrowserSession:
        """Create and initialize a new isolated BrowserSession."""
        valid_id = validate_session_id(session_id)
        async with self._get_lock():
            existing = self._sessions.get(valid_id)
            if existing and existing.is_alive():
                return existing

            if existing:
                await existing.close("Re-creating session")
                self._sessions.pop(valid_id, None)

            session = BrowserSession(
                session_id=valid_id,
                owner_id=owner_id,
                profile_name=profile_name,
                idle_timeout_seconds=idle_timeout_seconds,
                max_lifetime_seconds=max_lifetime_seconds,
            )
            self._sessions[valid_id] = session

        # Launch page and context through browser pool
        try:
            page = await browser_pool.get_or_create_page(
                department="standard_chat",
                agent_id=valid_id,
                profile_name=profile_name,
                headless=headless,
            )
            session.page = page
            session_key = f"standard_chat_{valid_id}_{profile_name or 'ephemeral'}"
            session.context = browser_pool.active_contexts.get(session_key)
            session.transition_to(SessionState.ACTIVE, reason="Chromium context ready")
            return session
        except Exception as e:
            logger.error("[SessionManager] Failed to launch session '%s': %s", valid_id, e)
            try:
                session.transition_to(SessionState.FAILED, reason=str(e))
            except Exception:
                pass
            raise

    async def get_or_create_session(
        self,
        session_id: str,
        owner_id: str = "default",
        profile_name: Optional[str] = None,
        headless: bool = True,
    ) -> BrowserSession:
        """Retrieve existing active session or initialize a fresh one."""
        session = await self.get_session(session_id)
        if session and session.is_alive():
            return session
        return await self.create_session(
            session_id=session_id,
            owner_id=owner_id,
            profile_name=profile_name,
            headless=headless,
        )

    async def recover_session(self, session_id: str, headless: bool = True) -> BrowserSession:
        """Attempt to restore a crashed or closed session into a fresh context."""
        valid_id = validate_session_id(session_id)
        session = self._sessions.get(valid_id)
        if not session:
            return await self.create_session(session_id=valid_id, headless=headless)

        async with session.get_lock():
            logger.info("[SessionManager] Initiating session recovery for '%s'", valid_id)
            try:
                session.transition_to(SessionState.RECOVERING, reason="Crash or disconnect detected")
            except InvalidStateTransitionError:
                # If session was closed or failed, reset state to RECOVERING for recovery attempt
                session.state = SessionState.RECOVERING

            # Close any dead handles
            if session.page:
                try:
                    await session.page.close()
                except Exception:
                    pass
                session.page = None

            if session.context:
                try:
                    await session.context.close()
                except Exception:
                    pass
                session.context = None

            try:
                # Re-create context & page with preserved profile if available
                page = await browser_pool.get_or_create_page(
                    department="standard_chat",
                    agent_id=valid_id,
                    profile_name=session.profile_name,
                    headless=headless,
                )
                session.page = page
                session_key = f"standard_chat_{valid_id}_{session.profile_name or 'ephemeral'}"
                session.context = browser_pool.active_contexts.get(session_key)

                # If there was a previous URL, restore navigation
                if session.last_snapshot and session.last_snapshot.get("url") and session.last_snapshot["url"] != "about:blank":
                    try:
                        await page.goto(
                            session.last_snapshot["url"],
                            wait_until="domcontentloaded",
                            timeout=30000,
                        )
                    except Exception as nav_e:
                        logger.warning("[SessionManager] Recovery page restore notice: %s", nav_e)

                session.transition_to(SessionState.ACTIVE, reason="Session recovered successfully")
                return session
            except Exception as rec_err:
                logger.error("[SessionManager] Recovery failed for '%s': %s", valid_id, rec_err)
                session.transition_to(SessionState.FAILED, reason=f"Recovery failed: {rec_err}")
                raise

    async def terminate_session(self, session_id: str, reason: str = "Terminated by request") -> bool:
        """Terminate a specific session and clean up all associated resources."""
        valid_id = validate_session_id(session_id)
        async with self._get_lock():
            session = self._sessions.pop(valid_id, None)
            if not session:
                return False

        await session.close(reason=reason)
        logger.info("[SessionManager] Terminated session '%s' (reason: %s)", valid_id, reason)
        return True

    async def cleanup_expired_sessions(self) -> int:
        """Sweep and terminate any idle or expired sessions, skipping active locked sessions."""
        cleaned_count = 0
        now = time.time()
        to_remove = []

        async with self._get_lock():
            for sid, session in list(self._sessions.items()):
                # HIGH (LIFE-01): Skip sessions with active in-flight operations (lock held)
                if session.get_lock().locked():
                    continue
                if session.state in {SessionState.CLOSED, SessionState.FAILED}:
                    to_remove.append((sid, "Already closed/failed"))
                elif (now - session.created_at) > session.max_lifetime_seconds:
                    to_remove.append((sid, "Max lifetime exceeded"))
                elif (now - session.last_active_at) > session.idle_timeout_seconds:
                    to_remove.append((sid, "Idle timeout exceeded"))

        for sid, reason in to_remove:
            await self.terminate_session(sid, reason=reason)
            cleaned_count += 1

        if cleaned_count > 0:
            logger.info("[SessionManager] Cleaned up %d expired browser sessions.", cleaned_count)
        return cleaned_count

    def start_cleanup_loop(self, interval_seconds: float = 60.0) -> None:
        """Start recurring background cleanup task if not already running."""
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(self._run_cleanup_loop(interval_seconds))
            logger.info(
                "[SessionManager] Started background session cleanup loop (interval=%.1fs)",
                interval_seconds,
            )

    async def stop_cleanup_loop(self) -> None:
        """Gracefully stop recurring background cleanup task."""
        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None
            logger.info("[SessionManager] Stopped background session cleanup loop")

    async def _run_cleanup_loop(self, interval_seconds: float) -> None:
        """Background loop executing cleanup_expired_sessions periodically."""
        while True:
            try:
                await asyncio.sleep(interval_seconds)
                await self.cleanup_expired_sessions()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("[SessionManager] Background cleanup loop error: %s", e)

    async def list_sessions(self) -> List[Dict[str, Any]]:
        """List summary info for all tracked sessions."""
        async with self._get_lock():
            return [s.to_dict() for s in self._sessions.values()]

    async def shutdown(self) -> None:
        """Shutdown all active sessions and release browser processes."""
        await self.stop_cleanup_loop()
        async with self._get_lock():
            sessions = list(self._sessions.values())
            self._sessions.clear()

        for session in sessions:
            try:
                await session.close("System shutdown")
            except Exception as e:
                logger.warning("[SessionManager] Error closing session on shutdown: %s", e)

        await browser_pool.shutdown()
        logger.info("[SessionManager] All browser sessions shut down.")


# Singleton instance
browser_session_manager = BrowserSessionManager()
