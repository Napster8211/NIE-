"""Exercise the Engineering Workspace migration in a disposable PostgreSQL schema.

The caller must provide a dedicated NIE_TEST_POSTGRES_URL and explicitly attest
that it is disposable. No production URL is accepted implicitly.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path
from urllib.parse import urlparse

import asyncpg

MIGRATION_DIRECTORY = Path(__file__).resolve().parents[1] / "database" / "migrations"
EXPECTED_TABLES = {
    "engineering_workspaces",
    "workspace_conversations",
    "tool_executions",
    "execution_events",
    "workspace_file_changes",
    "workspace_processes",
    "engineering_workspace_files",
    "engineering_workspace_staged_objects",
}
EXPECTED_COLUMNS = {
    "engineering_workspaces": {
        "workspace_id",
        "owner_id",
        "name",
        "slug",
        "root_path",
        "status",
        "runtime_type",
        "metadata",
        "storage_backend",
        "storage_revision",
        "storage_manifest_sha256",
    },
    "workspace_conversations": {"workspace_id", "conversation_id", "owner_id"},
    "tool_executions": {
        "execution_id",
        "workspace_id",
        "owner_id",
        "conversation_id",
        "status",
        "correlation_id",
        "provider_metadata",
    },
    "execution_events": {"execution_id", "sequence", "event_type", "payload"},
    "workspace_file_changes": {"execution_id", "workspace_id", "relative_path", "operation"},
    "workspace_processes": {"workspace_id", "owner_id", "execution_id", "status", "health_status"},
    "engineering_workspace_files": {
        "workspace_id",
        "owner_id",
        "logical_path",
        "storage_object_key",
        "content_sha256",
        "size_bytes",
        "revision",
        "execution_id",
    },
    "engineering_workspace_staged_objects": {
        "workspace_id",
        "owner_id",
        "execution_id",
        "storage_object_key",
        "status",
    },
}
EXPECTED_INDEXES = {
    "ix_engineering_workspaces_owner_id",
    "ix_engineering_workspaces_status",
    "ix_workspace_conversations_workspace_id",
    "ix_workspace_conversations_conversation_id",
    "ix_workspace_conversations_owner_id",
    "ix_tool_executions_workspace_id",
    "ix_tool_executions_owner_id",
    "ix_tool_executions_correlation_id",
    "ix_tool_executions_conversation_id",
    "ix_tool_executions_tool_name",
    "ix_tool_executions_status",
    "ix_execution_events_execution_id",
    "ix_execution_events_event_type",
    "ix_workspace_file_changes_execution_id",
    "ix_workspace_file_changes_workspace_id",
    "ix_workspace_processes_workspace_id",
    "ix_workspace_processes_owner_id",
    "ix_workspace_processes_status",
    "ix_engineering_workspaces_owner_storage",
    "ix_engineering_workspace_files_owner_workspace",
    "ix_engineering_workspace_files_workspace_revision",
    "ix_engineering_staged_objects_workspace_status",
}
EXPECTED_NAMED_CONSTRAINTS = {
    "uq_engineering_workspace_owner_slug",
    "ck_engineering_workspace_status",
    "ck_engineering_workspace_runtime_type",
    "uq_workspace_conversation_owner",
    "ck_tool_execution_status",
    "ck_tool_execution_approval_status",
    "ck_tool_execution_duration",
    "uq_execution_event_sequence",
    "ck_workspace_file_change_bytes_before",
    "ck_workspace_file_change_bytes_after",
    "ck_workspace_process_status",
    "ck_workspace_process_health_status",
    "ck_workspace_process_port",
    "ck_engineering_workspace_storage_backend",
    "ck_engineering_workspace_storage_revision",
    "ck_engineering_workspace_manifest_sha256",
    "uq_engineering_workspace_file_path",
    "ck_engineering_workspace_file_size",
    "ck_engineering_workspace_file_revision",
    "ck_engineering_workspace_file_sha256",
    "ck_engineering_staged_object_size",
    "ck_engineering_staged_object_sha256",
    "ck_engineering_staged_object_status",
}


def _asyncpg_url(value: str) -> str:
    return value.replace("postgresql+asyncpg://", "postgresql://", 1)


def _validate_disposable_url(database_url: str) -> None:
    parsed = urlparse(_asyncpg_url(database_url))
    database_name = parsed.path.strip("/").casefold()
    hostname = (parsed.hostname or "").casefold()
    identifying_text = f"{hostname}/{database_name}"
    if not parsed.scheme.startswith("postgres") or not hostname or not database_name:
        raise SystemExit("NIE_TEST_POSTGRES_URL_MALFORMED")
    if any(marker in identifying_text for marker in ("production", "-prod", "_prod", "/prod")):
        raise SystemExit("PRODUCTION_POSTGRES_URL_REJECTED")
    disposable_markers = ("test", "testing", "disposable", "staging", "stage", "development", "dev")
    explicitly_unlabeled = os.getenv("NIE_TEST_POSTGRES_ALLOW_UNLABELED", "").strip() == "YES"
    if not explicitly_unlabeled and not any(marker in database_name for marker in disposable_markers):
        raise SystemExit("NIE_TEST_POSTGRES_DATABASE_NAME_MUST_IDENTIFY_DISPOSABLE_USE")


async def verify(database_url: str) -> dict[str, int]:
    schema = f"nie_engineering_verify_{uuid.uuid4().hex}"
    migrations = [path.read_text(encoding="utf-8") for path in sorted(MIGRATION_DIRECTORY.glob("*_engineering_*.sql"))]
    if not migrations:
        raise RuntimeError("ENGINEERING_MIGRATIONS_NOT_FOUND")
    connection = await asyncpg.connect(_asyncpg_url(database_url), timeout=30)
    try:
        await connection.execute(f'CREATE SCHEMA "{schema}"')
        await connection.execute(f'SET search_path TO "{schema}"')
        for sql in migrations:
            await connection.execute(sql)
        for sql in migrations:
            await connection.execute(sql)
        # Confirm the legacy-message extension remains safe both when the
        # table is absent and when it exists in an established deployment.
        await connection.execute("CREATE TABLE messages (message_id VARCHAR PRIMARY KEY)")
        for sql in migrations:
            await connection.execute(sql)
        tables = set(
            await connection.fetchval(
                "SELECT array_agg(table_name) FROM information_schema.tables WHERE table_schema = $1",
                schema,
            )
            or []
        )
        missing = EXPECTED_TABLES - tables
        if missing:
            raise RuntimeError(f"ENGINEERING_MIGRATION_TABLES_MISSING:{','.join(sorted(missing))}")

        columns = await connection.fetch(
            """
            SELECT table_name, column_name
            FROM information_schema.columns
            WHERE table_schema = $1
            """,
            schema,
        )
        actual_columns: dict[str, set[str]] = {}
        for row in columns:
            actual_columns.setdefault(row["table_name"], set()).add(row["column_name"])
        for table, expected in EXPECTED_COLUMNS.items():
            missing_columns = expected - actual_columns.get(table, set())
            if missing_columns:
                raise RuntimeError(f"ENGINEERING_MIGRATION_COLUMNS_MISSING:{table}:{','.join(sorted(missing_columns))}")
        if not {"status", "correlation_id", "metadata"}.issubset(actual_columns.get("messages", set())):
            raise RuntimeError("ENGINEERING_MESSAGE_COMPATIBILITY_COLUMNS_MISSING")

        indexes = set(
            await connection.fetchval(
                "SELECT array_agg(indexname) FROM pg_indexes WHERE schemaname = $1",
                schema,
            )
            or []
        )
        missing_indexes = EXPECTED_INDEXES - indexes
        if missing_indexes:
            raise RuntimeError(f"ENGINEERING_MIGRATION_INDEXES_MISSING:{','.join(sorted(missing_indexes))}")
        constraints = set(
            await connection.fetchval(
                """
                SELECT array_agg(constraint_name)
                FROM information_schema.table_constraints
                WHERE constraint_schema = $1
                """,
                schema,
            )
            or []
        )
        missing_constraints = EXPECTED_NAMED_CONSTRAINTS - constraints
        if missing_constraints:
            raise RuntimeError(f"ENGINEERING_MIGRATION_CONSTRAINTS_MISSING:{','.join(sorted(missing_constraints))}")
        foreign_key_count = await connection.fetchval(
            """
            SELECT count(*)
            FROM information_schema.table_constraints
            WHERE constraint_schema = $1 AND constraint_type = 'FOREIGN KEY'
            """,
            schema,
        )
        if int(foreign_key_count) != 11:
            raise RuntimeError(f"ENGINEERING_MIGRATION_FOREIGN_KEY_COUNT_INVALID:{foreign_key_count}")

        await connection.execute(
            """
            INSERT INTO engineering_workspaces
                (workspace_id, owner_id, name, slug, root_path, storage_backend, storage_manifest_sha256)
            VALUES (
                'ews_verify', 'firebase:test-owner', 'verify', 'verify',
                'supabase://engineering-workspaces/ews_verify', 'SUPABASE',
                'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'
            );
            INSERT INTO workspace_conversations
                (link_id, workspace_id, conversation_id, owner_id)
            VALUES ('wcl_verify', 'ews_verify', 'conversation-verify', 'firebase:test-owner');
            INSERT INTO tool_executions
                (execution_id, workspace_id, owner_id, conversation_id, tool_name, correlation_id)
            VALUES (
                'tex_verify', 'ews_verify', 'firebase:test-owner',
                'conversation-verify', 'command.run', 'cor_verify'
            );
            INSERT INTO execution_events (event_id, execution_id, sequence, event_type)
            VALUES ('eev_verify', 'tex_verify', 1, 'tool.started');
            INSERT INTO workspace_file_changes
                (change_id, execution_id, workspace_id, relative_path, operation)
            VALUES ('wfc_verify', 'tex_verify', 'ews_verify', 'health_check.py', 'CREATED');
            INSERT INTO engineering_workspace_files
                (file_id, workspace_id, owner_id, logical_path, storage_object_key,
                 content_sha256, size_bytes, revision, execution_id)
            VALUES (
                'ewf_verify', 'ews_verify', 'firebase:test-owner', 'health_check.py',
                'users/test/workspaces/ews_verify/executions/tex_verify/staged/hash',
                'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', 24, 1, 'tex_verify'
            );
            INSERT INTO engineering_workspace_staged_objects
                (staging_id, workspace_id, owner_id, execution_id, storage_object_key,
                 content_sha256, size_bytes, status)
            VALUES (
                'ewsobj_verify', 'ews_verify', 'firebase:test-owner', 'tex_verify',
                'users/test/workspaces/ews_verify/executions/tex_verify/staged/hash',
                'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa', 24, 'COMMITTED'
            );
            INSERT INTO workspace_processes
                (process_id, workspace_id, owner_id, execution_id, sanitized_command, permitted_port)
            VALUES (
                'wpr_verify', 'ews_verify', 'firebase:test-owner', 'tex_verify',
                '["python","-m","http.server"]', 8000
            );
            """
        )
        await connection.close()

        connection = await asyncpg.connect(_asyncpg_url(database_url), timeout=30)
        await connection.execute(f'SET search_path TO "{schema}"')
        persisted_counts = {}
        for table in EXPECTED_TABLES:
            persisted_counts[table] = int(await connection.fetchval(f'SELECT count(*) FROM "{table}"'))
        if any(count != 1 for count in persisted_counts.values()):
            raise RuntimeError(f"ENGINEERING_PERSISTENCE_RELOAD_FAILED:{persisted_counts}")
        await connection.execute("DELETE FROM engineering_workspaces WHERE workspace_id = 'ews_verify'")
        for table in EXPECTED_TABLES - {"engineering_workspaces"}:
            remaining = int(await connection.fetchval(f'SELECT count(*) FROM "{table}"'))
            if remaining:
                raise RuntimeError(f"ENGINEERING_CASCADE_DELETE_FAILED:{table}:{remaining}")
        return {
            "tables": len(EXPECTED_TABLES),
            "indexes": len(EXPECTED_INDEXES),
            "constraints": len(EXPECTED_NAMED_CONSTRAINTS) + int(foreign_key_count),
            "persisted_workspaces": persisted_counts["engineering_workspaces"],
            "persisted_evidence_records": sum(persisted_counts.values()) - 1,
        }
    finally:
        if connection and not connection.is_closed():
            await connection.execute("SET search_path TO public")
            await connection.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            await connection.close()
        else:
            cleanup = await asyncpg.connect(_asyncpg_url(database_url), timeout=30)
            try:
                await cleanup.execute(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE')
            finally:
                await cleanup.close()


async def _main() -> None:
    database_url = os.getenv("NIE_TEST_POSTGRES_URL", "").strip()
    confirmed = os.getenv("NIE_TEST_POSTGRES_CONFIRM_DISPOSABLE", "").strip() == "YES"
    if not database_url:
        raise SystemExit("NIE_TEST_POSTGRES_URL_REQUIRED")
    if not confirmed:
        raise SystemExit("NIE_TEST_POSTGRES_CONFIRM_DISPOSABLE_MUST_EQUAL_YES")
    if os.getenv("NIE_ENV", "").strip().casefold() in {"production", "prod"}:
        raise SystemExit("DISPOSABLE_POSTGRES_CHECK_REJECTED_IN_PRODUCTION")
    _validate_disposable_url(database_url)
    result = await verify(database_url)
    print(
        "ENGINEERING_POSTGRES_VERIFY_OK "
        f"tables={result['tables']} indexes={result['indexes']} "
        f"constraints={result['constraints']} persisted_workspaces={result['persisted_workspaces']}"
        f" persisted_evidence_records={result['persisted_evidence_records']}"
    )


if __name__ == "__main__":
    asyncio.run(_main())
