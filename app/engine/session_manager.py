"""
NapsterTec AI - Enterprise Agent Session Manager
Module: app/engine/session_manager.py
"""
import uuid
import logging
import json
import os
from typing import Dict, List, Optional
from pydantic import BaseModel

logger = logging.getLogger(__name__)

SESSION_FILE = ".napstertec_sessions.json"

class SessionParticipant(BaseModel):
    agent_name: str
    role: str
    status: str

class SessionRecord(BaseModel):
    session_id: str
    session_type: str
    active_specialist: str
    participants: List[SessionParticipant]
    status: str
    context_preserved: bool = True

class SessionManager:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(SessionManager, cls).__new__(cls)
            cls._instance.sessions: Dict[str, SessionRecord] = {}
            cls._instance._load_state()
        return cls._instance

    def _load_state(self):
        """Force sync from disk to ensure cross-worker state consistency."""
        if os.path.exists(SESSION_FILE):
            try:
                with open(SESSION_FILE, "r") as f:
                    data = json.load(f)
                    self.sessions = {k: SessionRecord(**v) for k, v in data.items()}
            except Exception as e:
                logger.error(f"Failed to load session state: {e}")

    def _save_state(self):
        """Atomically dump current RAM state to the JSON registry."""
        try:
            with open(SESSION_FILE, "w") as f:
                json.dump({k: v.model_dump() for k, v in self.sessions.items()}, f)
        except Exception as e:
            logger.error(f"Failed to save session state: {e}")

    def create_session(self, session_type: str, active_specialist: str, participants: List[str]) -> SessionRecord:
        self._load_state()
        session_id = f"ses_{uuid.uuid4().hex[:8]}"
        parts = [SessionParticipant(agent_name="Director Intelligence", role="SUPERVISOR", status="ACTIVE")]
        
        for p in participants:
            if p != "Director Intelligence":
                parts.append(SessionParticipant(agent_name=p, role="SPECIALIST", status="ACTIVE"))
                
        record = SessionRecord(
            session_id=session_id, 
            session_type=session_type,
            active_specialist=active_specialist, 
            participants=parts, 
            status="Active"
        )
        self.sessions[session_id] = record
        self._save_state()
        logger.info(f"[Session Registry] Created new immutable session: {session_id}")
        return record

    def get_active_session(self) -> Optional[SessionRecord]:
        self._load_state()
        active_sessions = [s for s in self.sessions.values() if s.status == "Active"]
        if len(active_sessions) > 1:
            logger.error("[Session Registry] SessionStateConflict: Multiple active sessions detected.")
            return None
        return active_sessions[0] if active_sessions else None

    def get_suspended_session(self, specialist_hint: str = None) -> Optional[SessionRecord]:
        self._load_state()
        for s in self.sessions.values():
            if s.status == "Suspended":
                if specialist_hint and specialist_hint.lower() in s.active_specialist.lower():
                    return s
                elif not specialist_hint:
                    return s
        return None

    def suspend_active_session(self) -> Optional[SessionRecord]:
        session = self.get_active_session()
        if session:
            session.status = "Suspended"
            for p in session.participants:
                if p.role == "SPECIALIST":
                    p.status = "SUSPENDED"
            self.sessions[session.session_id] = session
            self._save_state()
            logger.info(f"[Session Registry] Suspended session: {session.session_id}")
            return session
        return None

    def resume_session(self, session_id: str) -> Optional[SessionRecord]:
        self._load_state()
        if session_id in self.sessions:
            session = self.sessions[session_id]
            session.status = "Active"
            for p in session.participants:
                p.status = "ACTIVE"
            self.sessions[session_id] = session
            self._save_state()
            logger.info(f"[Session Registry] Resumed session: {session.session_id}")
            return session
        return None

    def get_counts(self):
        self._load_state()
        active = sum(1 for s in self.sessions.values() if s.status == "Active")
        suspended = sum(1 for s in self.sessions.values() if s.status == "Suspended")
        return active, suspended

# Global Singleton
session_manager = SessionManager()