"""Bounded cleanup of expired Engineering preview processes.

This maintenance command never selects a process by a client-supplied owner or
workspace. It uses persisted ownership and an age/count-bounded database query.
"""

from __future__ import annotations

import asyncio
import os

from app.database import AsyncSessionLocal
from app.repositories.engineering_workspace_repository import EngineeringWorkspaceRepository
from app.services.engineering_process_service import VercelSandboxProcessManager


def _bounded(name: str, default: int, minimum: int, maximum: int) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except ValueError as exc:
        raise SystemExit(f"{name}_INVALID") from exc
    if value < minimum or value > maximum:
        raise SystemExit(f"{name}_INVALID")
    return value


async def _main() -> None:
    limit = _bounded("NIE_ENGINEERING_PROCESS_CLEANUP_LIMIT", 20, 1, 100)
    minimum_age = _bounded("NIE_ENGINEERING_PROCESS_CLEANUP_MIN_AGE_SECONDS", 30, 0, 86_400)
    async with AsyncSessionLocal() as session:
        result = await VercelSandboxProcessManager(EngineeringWorkspaceRepository(session)).cleanup_expired(
            limit=limit, minimum_age_seconds=minimum_age
        )
    print(
        "ENGINEERING_PROCESS_CLEANUP_OK "
        f"selected={result['selected']} expired={result['expired']} failed={result['failed']}"
    )


if __name__ == "__main__":
    asyncio.run(_main())
