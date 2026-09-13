"""Explicitly clean only database-tracked abandoned Engineering Storage objects."""

from __future__ import annotations

import asyncio
import os

from app.database import AsyncSessionLocal
from app.repositories.engineering_workspace_repository import EngineeringWorkspaceRepository
from app.services.supabase_workspace_storage import SupabaseWorkspaceStorage


async def _main() -> None:
    if os.getenv("NIE_ENGINEERING_STAGED_CLEANUP_CONFIRM", "") != "YES":
        raise SystemExit("NIE_ENGINEERING_STAGED_CLEANUP_CONFIRM_MUST_EQUAL_YES")
    try:
        limit = int(os.getenv("NIE_ENGINEERING_STAGED_CLEANUP_LIMIT", "100"))
    except ValueError as exc:
        raise SystemExit("NIE_ENGINEERING_STAGED_CLEANUP_LIMIT_INVALID") from exc
    if limit < 1 or limit > 1000:
        raise SystemExit("NIE_ENGINEERING_STAGED_CLEANUP_LIMIT_INVALID")
    try:
        min_age_seconds = int(os.getenv("NIE_ENGINEERING_STAGED_CLEANUP_MIN_AGE_SECONDS", "3600"))
    except ValueError as exc:
        raise SystemExit("NIE_ENGINEERING_STAGED_CLEANUP_MIN_AGE_SECONDS_INVALID") from exc
    if min_age_seconds < 300:
        raise SystemExit("NIE_ENGINEERING_STAGED_CLEANUP_MIN_AGE_SECONDS_TOO_LOW")
    async with AsyncSessionLocal() as session:
        repository = EngineeringWorkspaceRepository(session)
        storage = SupabaseWorkspaceStorage(repository)
        cleaned = await storage.cleanup_abandoned(limit, min_age_seconds)
    print(f"ENGINEERING_STAGED_OBJECT_CLEANUP_OK count={cleaned}")


if __name__ == "__main__":
    asyncio.run(_main())
