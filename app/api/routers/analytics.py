from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from typing import List

from app.schemas.analytics import DashboardResponse, ProviderStat, SkillStat, DailyUsageStat, FallbackStat
from app.repositories.analytics_repository import AnalyticsRepository
# from app.db.session import get_db

router = APIRouter(prefix="/analytics", tags=["Analytics"])

async def get_db():
    yield None

@router.get("/dashboard", response_model=DashboardResponse)
async def get_dashboard_aggregated(days: int = 30, db: AsyncSession = Depends(get_db)):
    repo = AnalyticsRepository(db)
    
    # Execute concurrently in production using asyncio.gather for performance
    total_reqs = await repo.get_total_requests(days)
    total_falls = await repo.get_total_fallbacks(days)
    avg_lat = await repo.get_average_latency(days)
    success_rt = await repo.get_success_rate(days)
    providers = await repo.get_provider_stats(days)
    skills = await repo.get_skill_stats(days)
    daily = await repo.get_daily_usage(days)

    return DashboardResponse(
        total_requests=total_reqs,
        total_fallbacks=total_falls,
        average_response_time_ms=avg_lat,
        success_rate=success_rt,
        active_providers=len(providers),
        provider_breakdown=providers,
        skill_usage=skills,
        daily_trend=daily
    )

@router.get("/providers", response_model=List[ProviderStat])
async def get_providers(days: int = 30, db: AsyncSession = Depends(get_db)):
    repo = AnalyticsRepository(db)
    return await repo.get_provider_stats(days)

@router.get("/skills", response_model=List[SkillStat])
async def get_skills(days: int = 30, db: AsyncSession = Depends(get_db)):
    repo = AnalyticsRepository(db)
    return await repo.get_skill_stats(days)

@router.get("/usage", response_model=List[DailyUsageStat])
async def get_usage(days: int = 30, db: AsyncSession = Depends(get_db)):
    repo = AnalyticsRepository(db)
    return await repo.get_daily_usage(days)

@router.get("/latency")
async def get_latency(days: int = 30, db: AsyncSession = Depends(get_db)):
    repo = AnalyticsRepository(db)
    latency = await repo.get_average_latency(days)
    return {"average_latency_ms": latency, "period_days": days}

@router.get("/fallbacks", response_model=List[FallbackStat])
async def get_fallbacks(days: int = 30, db: AsyncSession = Depends(get_db)):
    repo = AnalyticsRepository(db)
    return await repo.get_fallback_stats(days)