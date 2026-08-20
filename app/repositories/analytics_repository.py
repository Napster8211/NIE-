from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import func, case, cast, Date
from datetime import datetime, timedelta
from app.models.analytics import AnalyticsLog

class AnalyticsRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_total_requests(self, days: int = 30) -> int:
        cutoff = datetime.utcnow() - timedelta(days=days)
        result = await self.session.execute(
            select(func.count(AnalyticsLog.id)).where(AnalyticsLog.timestamp >= cutoff)
        )
        return result.scalar() or 0

    async def get_total_fallbacks(self, days: int = 30) -> int:
        cutoff = datetime.utcnow() - timedelta(days=days)
        result = await self.session.execute(
            select(func.count(AnalyticsLog.id))
            .where(AnalyticsLog.is_fallback == True)
            .where(AnalyticsLog.timestamp >= cutoff)
        )
        return result.scalar() or 0

    async def get_average_latency(self, days: int = 30) -> float:
        cutoff = datetime.utcnow() - timedelta(days=days)
        result = await self.session.execute(
            select(func.avg(AnalyticsLog.latency_ms)).where(AnalyticsLog.timestamp >= cutoff)
        )
        return round(result.scalar() or 0.0, 2)

    async def get_success_rate(self, days: int = 30) -> float:
        cutoff = datetime.utcnow() - timedelta(days=days)
        total_res = await self.session.execute(
            select(func.count(AnalyticsLog.id)).where(AnalyticsLog.timestamp >= cutoff)
        )
        total = total_res.scalar() or 1 # Prevent division by zero
        
        success_res = await self.session.execute(
            select(func.count(AnalyticsLog.id))
            .where(AnalyticsLog.status_code < 400)
            .where(AnalyticsLog.timestamp >= cutoff)
        )
        success = success_res.scalar() or 0
        return round((success / total) * 100, 2)

    async def get_provider_stats(self, days: int = 30) -> list:
        cutoff = datetime.utcnow() - timedelta(days=days)
        stmt = (
            select(
                AnalyticsLog.provider_used,
                func.count(AnalyticsLog.id).label("request_count"),
                func.avg(AnalyticsLog.latency_ms).label("average_latency"),
                (func.sum(case((AnalyticsLog.status_code >= 400, 1), else_=0)) * 100.0 / func.count(AnalyticsLog.id)).label("error_rate")
            )
            .where(AnalyticsLog.timestamp >= cutoff)
            .where(AnalyticsLog.provider_used.isnot(None))
            .group_by(AnalyticsLog.provider_used)
        )
        result = await self.session.execute(stmt)
        return [{"provider": row.provider_used, "request_count": row.request_count, "average_latency_ms": round(row.average_latency or 0, 2), "error_rate": round(row.error_rate or 0, 2)} for row in result.all()]

    async def get_skill_stats(self, days: int = 30) -> list:
        cutoff = datetime.utcnow() - timedelta(days=days)
        stmt = (
            select(AnalyticsLog.skill_used, func.count(AnalyticsLog.id).label("usage_count"))
            .where(AnalyticsLog.timestamp >= cutoff)
            .where(AnalyticsLog.skill_used.isnot(None))
            .group_by(AnalyticsLog.skill_used)
            .order_by(func.count(AnalyticsLog.id).desc())
        )
        result = await self.session.execute(stmt)
        return [{"skill": row.skill_used, "usage_count": row.usage_count} for row in result.all()]

    async def get_daily_usage(self, days: int = 7) -> list:
        cutoff = datetime.utcnow() - timedelta(days=days)
        stmt = (
            select(
                cast(AnalyticsLog.timestamp, Date).label("date"),
                func.count(AnalyticsLog.id).label("requests"),
                func.sum(AnalyticsLog.tokens_used).label("tokens")
            )
            .where(AnalyticsLog.timestamp >= cutoff)
            .group_by(cast(AnalyticsLog.timestamp, Date))
            .order_by(cast(AnalyticsLog.timestamp, Date))
        )
        result = await self.session.execute(stmt)
        return [{"date": str(row.date), "requests": row.requests, "tokens": row.tokens or 0} for row in result.all()]

    async def get_fallback_stats(self, days: int = 30) -> list:
        cutoff = datetime.utcnow() - timedelta(days=days)
        stmt = (
            select(
                AnalyticsLog.fallback_from.label("original_provider"),
                AnalyticsLog.provider_used.label("fallback_provider"),
                func.count(AnalyticsLog.id).label("count")
            )
            .where(AnalyticsLog.is_fallback == True)
            .where(AnalyticsLog.timestamp >= cutoff)
            .group_by(AnalyticsLog.fallback_from, AnalyticsLog.provider_used)
        )
        result = await self.session.execute(stmt)
        return [{"original_provider": row.original_provider, "fallback_provider": row.fallback_provider, "count": row.count} for row in result.all()]