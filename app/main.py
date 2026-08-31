import os
import sys
import logging
import asyncio
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# --- WINDOWS PLAYWRIGHT / SUBPROCESS EVENT LOOP OVERRIDE ---
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

logger = logging.getLogger(__name__)

# Database and Memory Models
from app.database import engine, Base

import app.models.memory_models 
import app.models.image  
import app.models.document 
import app.models.director_auth

# Core APIs and Engine Memory
from app.api.endpoints import router as api_router
from app.api.memory import router as memory_router

# Enterprise modules
from app.api.routers.documents import router as documents_router
from app.api.routers.images import router as images_router
from app.api.routers.analytics import router as analytics_router
from app.api.routers.system import system_router
from app.api.director_desktop import router as director_desktop_router
from app.api.director_auth import router as director_auth_router
from app.services.director_auth_service import trusted_frontend_origins

# --- SPRINT 25.5: AUTONOMOUS MISSION WORKER IMPORT ---
from app.engine.autonomous_worker import autonomous_worker

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup Phase: Connect to PostgreSQL and provision missing tables.
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    # Launch the Autonomous Mission Worker background polling loop (Sprint 25.5)
    logger.info("[Main] Launching Autonomous Mission Worker background loop...")
    await autonomous_worker.start_worker_loop()
    
    yield  # Application processes requests here
    
    # Shutdown Phase: Stop worker loop and clean up DB connections.
    logger.info("[Main] Shutting down Autonomous Mission Worker...")
    await autonomous_worker.stop_worker_loop()
    await engine.dispose()

# Initialize FastAPI with the lifespan context manager
app = FastAPI(
    title="NapsterTec Intelligence Engine (NIE)",
    description="Capability-Centric AI Engine Architecture with Autonomous Mission Worker",
    version="25.5.0",
    lifespan=lifespan
)

# Configure CORS Middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(trusted_frontend_origins()),
    allow_credentials=True,
    allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
    allow_headers=["Accept", "Authorization", "Content-Type", "X-CSRF-Token"],
)

# Register routers
app.include_router(api_router, prefix="/api/v1")
app.include_router(memory_router)
app.include_router(director_desktop_router, prefix="/api/v1")
app.include_router(director_auth_router, prefix="/api/v1")

app.include_router(documents_router)
app.include_router(images_router)
app.include_router(analytics_router)
app.include_router(system_router)

@app.get("/health")
async def health_check():
    return {
        "status": "online",
        "architecture": "capability_centric",
        "engine": "NIE v25.5",
        "database_status": "connected"
    }

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("app.main:app", host="0.0.0.0", port=port, reload=True)
