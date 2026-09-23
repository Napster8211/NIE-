import os
import unittest
from unittest.mock import AsyncMock, patch

from app.models.engineering_workspace import WorkspaceFileChange, WorkspaceStagedObject
from scripts.apply_engineering_migration import assert_no_duplicate_active_processes
from scripts.validate_engineering_staging import validate as validate_staging
from scripts.verify_engineering_postgres import _validate_disposable_url


class EngineeringPostgresVerifierTests(unittest.TestCase):
    def test_orm_metadata_keeps_file_change_and_staged_object_schemas_separate(self):
        self.assertEqual(
            {
                "change_id",
                "execution_id",
                "workspace_id",
                "relative_path",
                "operation",
                "bytes_before",
                "bytes_after",
                "content_sha256",
                "created_at",
            },
            set(WorkspaceFileChange.__table__.columns.keys()),
        )
        self.assertEqual(
            {
                "staging_id",
                "workspace_id",
                "owner_id",
                "execution_id",
                "storage_object_key",
                "content_sha256",
                "size_bytes",
                "status",
                "created_at",
                "updated_at",
                "cleaned_at",
            },
            set(WorkspaceStagedObject.__table__.columns.keys()),
        )
        self.assertIn(
            "ck_engineering_staged_object_status",
            {constraint.name for constraint in WorkspaceStagedObject.__table__.constraints},
        )

    def test_staging_files_have_fail_closed_required_configuration(self):
        validate_staging()

    def test_accepts_explicitly_disposable_database_name(self):
        _validate_disposable_url("postgresql://user:password@127.0.0.1:5432/nie_engineering_test")

    def test_rejects_identifiable_production_database(self):
        with self.assertRaisesRegex(SystemExit, "PRODUCTION_POSTGRES_URL_REJECTED"):
            _validate_disposable_url("postgresql://user:password@database.example/nie_production")

    def test_rejects_unlabelled_database_without_separate_explicit_override(self):
        with patch.dict(os.environ, {"NIE_TEST_POSTGRES_ALLOW_UNLABELED": ""}, clear=False):
            with self.assertRaisesRegex(SystemExit, "MUST_IDENTIFY_DISPOSABLE_USE"):
                _validate_disposable_url("postgresql://user:password@127.0.0.1:5432/postgres")

    def test_unlabelled_override_is_deliberate_and_case_sensitive(self):
        with patch.dict(os.environ, {"NIE_TEST_POSTGRES_ALLOW_UNLABELED": "YES"}, clear=False):
            _validate_disposable_url("postgresql://user:password@127.0.0.1:5432/postgres")
        with patch.dict(os.environ, {"NIE_TEST_POSTGRES_ALLOW_UNLABELED": "yes"}, clear=False):
            with self.assertRaises(SystemExit):
                _validate_disposable_url("postgresql://user:password@127.0.0.1:5432/postgres")


class EngineeringMigrationPreflightTests(unittest.IsolatedAsyncioTestCase):
    async def test_duplicate_active_rows_fail_before_remote_process_ddl(self):
        connection = AsyncMock()
        connection.fetchval = AsyncMock(side_effect=[True, True])

        with self.assertRaisesRegex(RuntimeError, "ENGINEERING_ACTIVE_PROCESS_DUPLICATES_DETECTED"):
            await assert_no_duplicate_active_processes(connection)

        self.assertEqual(2, connection.fetchval.await_count)

    async def test_clean_or_absent_process_table_passes_preflight(self):
        absent = AsyncMock()
        absent.fetchval = AsyncMock(return_value=False)
        await assert_no_duplicate_active_processes(absent)
        self.assertEqual(1, absent.fetchval.await_count)

        clean = AsyncMock()
        clean.fetchval = AsyncMock(side_effect=[True, False])
        await assert_no_duplicate_active_processes(clean)
        self.assertEqual(2, clean.fetchval.await_count)


if __name__ == "__main__":
    unittest.main()
