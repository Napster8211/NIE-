from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, update

from app.database import AsyncSessionLocal
from app.models.director_auth import DirectorBrowserSession, DirectorRealtimeTicket


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class DirectorAuthRepository:
    """Shared PostgreSQL persistence for browser sessions and realtime tickets."""

    async def create_session(self, **values) -> DirectorBrowserSession:
        async with AsyncSessionLocal() as db:
            record = DirectorBrowserSession(**values)
            db.add(record)
            await db.commit()
            await db.refresh(record)
            return record

    async def get_session(self, token_hash: str) -> Optional[DirectorBrowserSession]:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(DirectorBrowserSession).where(DirectorBrowserSession.token_hash == token_hash)
            )
            return result.scalar_one_or_none()

    async def revoke_session(self, token_hash: str, revoked_at: datetime) -> bool:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                update(DirectorBrowserSession)
                .where(
                    DirectorBrowserSession.token_hash == token_hash,
                    DirectorBrowserSession.revoked_at.is_(None),
                )
                .values(revoked_at=revoked_at)
            )
            await db.execute(
                update(DirectorRealtimeTicket)
                .where(
                    DirectorRealtimeTicket.session_id.in_(
                        select(DirectorBrowserSession.session_id).where(
                            DirectorBrowserSession.token_hash == token_hash
                        )
                    ),
                    DirectorRealtimeTicket.revoked_at.is_(None),
                )
                .values(revoked_at=revoked_at)
            )
            await db.commit()
            return bool(result.rowcount)

    async def create_ticket(self, **values) -> DirectorRealtimeTicket:
        async with AsyncSessionLocal() as db:
            record = DirectorRealtimeTicket(**values)
            db.add(record)
            await db.commit()
            await db.refresh(record)
            return record

    async def consume_ticket(
        self,
        ticket_hash: str,
        purpose: str,
        now: Optional[datetime] = None,
    ) -> Optional[DirectorRealtimeTicket]:
        consumed_at = now or _utc_now()
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(DirectorRealtimeTicket)
                .where(DirectorRealtimeTicket.ticket_hash == ticket_hash)
                .with_for_update()
            )
            record = result.scalar_one_or_none()
            if (
                record is None
                or record.purpose != purpose
                or record.consumed_at is not None
                or record.revoked_at is not None
                or record.expires_at <= consumed_at
            ):
                await db.rollback()
                return None

            session_result = await db.execute(
                select(DirectorBrowserSession).where(
                    DirectorBrowserSession.session_id == record.session_id
                )
            )
            session = session_result.scalar_one_or_none()
            if (
                session is None
                or session.revoked_at is not None
                or session.expires_at <= consumed_at
            ):
                await db.rollback()
                return None

            record.consumed_at = consumed_at
            await db.commit()
            await db.refresh(record)
            return record


director_auth_repository = DirectorAuthRepository()

