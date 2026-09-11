"""Apply the idempotent Engineering Workspace PostgreSQL migration.

This command is intended for a staging/production pre-deploy step. It fails
closed when DATABASE_URL is absent and never prints the connection string.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import asyncpg

MIGRATION_PATH = Path(__file__).resolve().parents[1] / "database" / "migrations" / "001_engineering_workspace.sql"
ADVISORY_LOCK_ID = 731_914_002


def _asyncpg_url(value: str) -> str:
    return value.replace("postgresql+asyncpg://", "postgresql://", 1)


async def apply_migration(database_url: str) -> None:
    sql = MIGRATION_PATH.read_text(encoding="utf-8")
    connection = await asyncpg.connect(_asyncpg_url(database_url), timeout=30)
    try:
        async with connection.transaction():
            await connection.execute("SELECT pg_advisory_xact_lock($1)", ADVISORY_LOCK_ID)
            await connection.execute(sql)
    finally:
        await connection.close()


async def _main() -> None:
    database_url = os.getenv("DATABASE_URL", "").strip()
    if not database_url:
        raise SystemExit("ENGINEERING_MIGRATION_DATABASE_URL_REQUIRED")
    await apply_migration(database_url)
    print("ENGINEERING_WORKSPACE_MIGRATION_OK")


if __name__ == "__main__":
    asyncio.run(_main())
