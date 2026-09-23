"""Apply the idempotent Engineering Workspace PostgreSQL migration.

This command is intended for a staging/production pre-deploy step. It fails
closed when DATABASE_URL is absent and never prints the connection string.
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

import asyncpg

MIGRATION_DIRECTORY = Path(__file__).resolve().parents[1] / "database" / "migrations"
ADVISORY_LOCK_ID = 731_914_002
ACTIVE_PROCESS_STATUSES = ("STARTING", "RUNNING", "READY", "STOPPING")


def _asyncpg_url(value: str) -> str:
    return value.replace("postgresql+asyncpg://", "postgresql://", 1)


async def assert_no_duplicate_active_processes(connection) -> None:
    table_exists = await connection.fetchval("SELECT to_regclass('workspace_processes') IS NOT NULL")
    if not table_exists:
        return
    duplicate_exists = await connection.fetchval(
        """
        SELECT EXISTS (
            SELECT workspace_id
            FROM workspace_processes
            WHERE status = ANY($1::text[])
            GROUP BY workspace_id
            HAVING count(*) > 1
        )
        """,
        list(ACTIVE_PROCESS_STATUSES),
    )
    if duplicate_exists:
        raise RuntimeError("ENGINEERING_ACTIVE_PROCESS_DUPLICATES_DETECTED")


async def apply_migration(database_url: str) -> None:
    migrations = sorted(MIGRATION_DIRECTORY.glob("*_engineering_*.sql"))
    if not migrations:
        raise RuntimeError("ENGINEERING_MIGRATIONS_NOT_FOUND")
    connection = await asyncpg.connect(_asyncpg_url(database_url), timeout=30)
    try:
        async with connection.transaction():
            await connection.execute("SELECT pg_advisory_xact_lock($1)", ADVISORY_LOCK_ID)
            for migration in migrations:
                if migration.name.startswith("003_"):
                    await assert_no_duplicate_active_processes(connection)
                await connection.execute(migration.read_text(encoding="utf-8"))
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
