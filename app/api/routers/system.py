import time
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from app.schemas.analytics import SystemHealthResponse, RouterStatusResponse
# from app.router.engine import capability_router

system_router = APIRouter(prefix="/system", tags=["System"])

# Record boot time for uptime calculation
BOOT_TIME = time.time()

async def get_db():
    yield None

@system_router.get("/health", response_model=SystemHealthResponse)
async def system_health(db: AsyncSession = Depends(get_db)):
    db_connected = False
    try:
        # Ping DB
        if db:
            await db.execute(text("SELECT 1"))
            db_connected = True
    except Exception:
        pass

    return SystemHealthResponse(
        status="operational" if db_connected else "degraded",
        uptime_seconds=int(time.time() - BOOT_TIME),
        database_connected=db_connected,
        router_active=True, # Assuming router is singleton and loaded
        version="1.4.0" # Current sprint version
    )

@system_router.get("/router", response_model=RouterStatusResponse)
async def router_status():
    # Introspects your existing capability_router
    # registered = [p.name for p in capability_router._providers.values()]
    # capabilities = capability_router.get_all_capabilities()
    
    # Simulated for architecture drop-in
    registered = ["openrouter", "gemini", "ollama"]
    capabilities = ["text_generation", "vision", "image_generation", "summarization"]
    
    return RouterStatusResponse(
        state="active",
        registered_providers=registered,
        active_capabilities=capabilities,
        fallback_pipeline_status="enabled"
    )