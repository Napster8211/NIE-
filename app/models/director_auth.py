from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Index, String

from app.database import Base


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class DirectorBrowserSession(Base):
    """Server-side, revocable Director browser session.

    Only SHA-256 digests of browser credentials are persisted. The raw session
    and CSRF tokens exist only in the browser and the issuing response.
    """

    __tablename__ = "director_browser_sessions"

    token_hash = Column(String(64), primary_key=True)
    session_id = Column(String(64), nullable=False, unique=True, index=True)
    owner_uid = Column(String(128), nullable=False, index=True)
    owner_email = Column(String(320), nullable=True)
    csrf_token_hash = Column(String(64), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)
    last_seen_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)

    __table_args__ = (
        Index("ix_director_sessions_owner_active", "owner_uid", "expires_at", "revoked_at"),
    )


class DirectorRealtimeTicket(Base):
    """Short-lived, single-use credential for one Director WebSocket."""

    __tablename__ = "director_realtime_tickets"

    ticket_hash = Column(String(64), primary_key=True)
    ticket_id = Column(String(64), nullable=False, unique=True, index=True)
    session_id = Column(String(64), nullable=False, index=True)
    owner_uid = Column(String(128), nullable=False, index=True)
    purpose = Column(String(64), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=utc_now)
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)
    consumed_at = Column(DateTime(timezone=True), nullable=True)
    revoked_at = Column(DateTime(timezone=True), nullable=True)

