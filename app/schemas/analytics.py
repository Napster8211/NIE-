from pydantic import BaseModel
from typing import List, Dict, Any, Optional
from datetime import datetime

class ProviderStat(BaseModel):
    provider: str
    request_count: int
    average_latency_ms: float
    error_rate: float

class SkillStat(BaseModel):
    skill: str
    usage_count: int

class FallbackStat(BaseModel):
    original_provider: str
    fallback_provider: str
    count: int

class DailyUsageStat(BaseModel):
    date: str
    requests: int
    tokens: int

class DashboardResponse(BaseModel):
    total_requests: int
    total_fallbacks: int
    average_response_time_ms: float
    success_rate: float
    active_providers: int
    provider_breakdown: List[ProviderStat]
    skill_usage: List[SkillStat]
    daily_trend: List[DailyUsageStat]

class SystemHealthResponse(BaseModel):
    status: str
    uptime_seconds: int
    database_connected: bool
    router_active: bool
    version: str

class RouterStatusResponse(BaseModel):
    state: str
    registered_providers: List[str]
    active_capabilities: List[str]
    fallback_pipeline_status: str