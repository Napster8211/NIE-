# The Windows event-loop policy must be selected before importing application
# modules that may initialize subprocess clients.
# ruff: noqa: E402
import asyncio
import logging
import os
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# --- WINDOWS PLAYWRIGHT / SUBPROCESS EVENT LOOP OVERRIDE ---
if sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

logger = logging.getLogger(__name__)


class ApplicationCORSMiddleware(CORSMiddleware):
    """Global CORS wrapper that preserves FastAPI inspection attributes.

    Starlette's server-error middleware sits outside middleware registered with
    ``add_middleware``. Wrapping the complete FastAPI application ensures even
    unexpected error responses retain CORS headers for approved origins.
    """

    def __getattr__(self, name: str):
        return getattr(self.app, name)


# Database and Memory Models
from sqlalchemy import text

import app.models.director_auth
import app.models.document
import app.models.engineering_workspace
import app.models.image
import app.models.memory_models
from app.api.director_auth import router as director_auth_router
from app.api.director_desktop import router as director_desktop_router

# Core APIs and Engine Memory
from app.api.endpoints import router as api_router
from app.api.engineering_workspace import router as engineering_workspace_router
from app.api.memory import router as memory_router
from app.api.routers.analytics import router as analytics_router

# Enterprise modules
from app.api.routers.documents import router as documents_router
from app.api.routers.images import router as images_router
from app.api.routers.system import system_router
from app.database import Base, engine

# --- SPRINT 25.5: AUTONOMOUS MISSION WORKER IMPORT ---
from app.engine.autonomous_worker import autonomous_worker
from app.services.director_auth_service import trusted_frontend_origins
from app.services.director_speech_service import director_speech_service
from app.services.engineering_execution_service import (
    engineering_runner_readiness,
    validate_engineering_runner_configuration,
)
from app.services.engineering_process_service import ProcessManager
from app.services.engineering_workspace_storage import (
    probe_workspace_storage,
    validate_workspace_storage_configuration,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    # Engineering mode may be staged while disabled, but can never start
    # enabled with an unavailable isolated runner.
    validate_engineering_runner_configuration()
    validate_workspace_storage_configuration()
    # Startup Phase: Connect to PostgreSQL and provision missing tables.
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # Warm the self-hosted STT model once per application process. Loading is
    # asynchronous; readiness stays false and transcription fails closed until ready.
    director_speech_service.startup()

    # Launch the Autonomous Mission Worker background polling loop (Sprint 25.5)
    logger.info("[Main] Launching Autonomous Mission Worker background loop...")
    await autonomous_worker.start_worker_loop()

    yield  # Application processes requests here

    # Shutdown Phase: Stop worker loop and clean up DB connections.
    logger.info("[Main] Shutting down Autonomous Mission Worker...")
    await autonomous_worker.stop_worker_loop()
    await ProcessManager.shutdown_active()
    await director_speech_service.shutdown()
    await engine.dispose()


# Initialize FastAPI with the lifespan context manager
app = FastAPI(
    title="NapsterTec Intelligence Engine (NIE)",
    description="Capability-Centric AI Engine Architecture with Autonomous Mission Worker",
    version="25.5.0",
    lifespan=lifespan,
)

# Register routers
app.include_router(api_router, prefix="/api/v1")
app.include_router(memory_router)
app.include_router(director_desktop_router, prefix="/api/v1")
app.include_router(director_auth_router, prefix="/api/v1")
app.include_router(engineering_workspace_router, prefix="/api/v1")

app.include_router(documents_router)
app.include_router(images_router)
app.include_router(analytics_router)
app.include_router(system_router)


@app.get("/health")
async def health_check():
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
        database_status = "ready"
    except Exception:
        database_status = "unavailable"
    runner_readiness = engineering_runner_readiness()
    return {
        "status": "online",
        "architecture": "capability_centric",
        "engine": "NIE v25.5",
        "database_status": database_status,
        "director_stt": director_speech_service.readiness(),
        "engineering": {**runner_readiness, "storage": await probe_workspace_storage()},
    }


# Wrap the complete application so approved-origin CORS headers are also
# present on errors produced by Starlette's outer server-error boundary.
app = ApplicationCORSMiddleware(
    app,
    allow_origins=list(trusted_frontend_origins()),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Accept", "Authorization", "Content-Type", "X-CSRF-Token"],
)


if __name__ == "__main__":
    import uvicorn

    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("app.main:app", host="0.0.0.0", port=port, reload=True)
