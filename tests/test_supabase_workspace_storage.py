import asyncio
import hashlib
import json
import os
import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx

from app.schemas.engineering_workspace import CommandInput
from app.services.engineering_execution_service import (
    LocalDevelopmentCommandRunner,
    PolicyDecision,
    ToolExecutionError,
    ToolExecutionService,
)
from app.services.engineering_workspace_service import WorkspaceManager
from app.services.engineering_workspace_storage import (
    WorkspaceStorageError,
    build_workspace_storage,
    probe_workspace_storage,
    validate_workspace_storage_configuration,
    workspace_storage_readiness,
)
from app.services.supabase_workspace_storage import (
    EMPTY_MANIFEST_SHA256,
    SupabaseStorageHttpClient,
    SupabaseStorageSettings,
    SupabaseWorkspaceStorage,
)
from app.services.vercel_sandbox_runner import (
    SandboxCommandResult,
    VercelSandboxRunner,
    VercelSandboxSettings,
)


class FakeSupabaseClient:
    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.upload_calls: list[str] = []
        self.deleted: list[str] = []
        self.fail_upload_at: int | None = None
        self.fail_delete = False
        self.cancel_upload_at: int | None = None
        self.on_upload = None

    async def upload(self, object_key, content):
        self.upload_calls.append(object_key)
        if self.on_upload is not None:
            self.on_upload(object_key)
        if self.cancel_upload_at == len(self.upload_calls):
            self.objects[object_key] = content
            raise asyncio.CancelledError
        if self.fail_upload_at == len(self.upload_calls):
            raise WorkspaceStorageError("SUPABASE_STORAGE_UPLOAD_FAILED")
        if object_key in self.objects and self.objects[object_key] != content:
            raise WorkspaceStorageError("IMMUTABLE_OBJECT_COLLISION")
        self.objects[object_key] = content

    async def download(self, object_key):
        if object_key not in self.objects:
            raise WorkspaceStorageError("SUPABASE_STORAGE_DOWNLOAD_FAILED")
        return self.objects[object_key]

    async def delete(self, object_keys):
        if self.fail_delete:
            raise WorkspaceStorageError("SUPABASE_STORAGE_DELETE_FAILED")
        for key in object_keys:
            self.objects.pop(key, None)
            self.deleted.append(key)

    async def list(self, prefix):
        return sorted(key for key in self.objects if key.startswith(prefix))

    async def check_bucket(self):
        return True


class FakeManifestRepository:
    def __init__(self):
        self.revision = 0
        self.manifest_hash = EMPTY_MANIFEST_SHA256
        self.files = []
        self.commits = []
        self.staged = {}
        self.fail_commit = False
        self.raise_after_commit = False
        self.fail_commit_status_read = False
        self.state_reads = 0
        self.conflict_on_commit = False
        self.executions = {}
        self.events = {}
        self.separate_file_changes = []

    async def workspace_storage_state(self, workspace_id, owner_id):
        self.state_reads += 1
        if self.fail_commit_status_read and self.fail_commit and self.state_reads >= 3:
            raise RuntimeError("database status unavailable")
        return self.revision, self.manifest_hash, list(self.files)

    async def commit_workspace_manifest(self, **values):
        if self.fail_commit:
            raise RuntimeError("database unavailable")
        if self.conflict_on_commit or values["expected_revision"] != self.revision:
            raise ValueError("WORKSPACE_SYNC_CONFLICT")
        self.revision += 1
        self.manifest_hash = values["manifest_sha256"]
        self.files = [
            SimpleNamespace(
                logical_path=item["path"],
                storage_object_key=item["object_key"],
                content_sha256=item["sha256"],
                size_bytes=item["size"],
                revision=self.revision,
                execution_id=item.get("execution_id") or values["execution_id"],
            )
            for item in values["files"]
        ]
        for item in values["staged_objects"]:
            self.staged[item["object_key"]].status = "COMMITTED"
        self.commits.append(values)
        if self.raise_after_commit:
            raise RuntimeError("commit acknowledgement lost")
        return self.revision

    async def record_pending_staged_objects(self, **values):
        for item in values["objects"]:
            self.staged[item["object_key"]] = SimpleNamespace(
                storage_object_key=item["object_key"],
                status="PENDING",
            )

    async def mark_staged_objects_cleanup_failed(self, object_keys):
        for key in object_keys:
            self.staged[key].status = "CLEANUP_FAILED"

    async def abandoned_staged_objects(self, limit=100, min_age_seconds=3600):
        return [item for item in self.staged.values() if item.status in {"PENDING", "CLEANUP_FAILED"}][:limit]

    async def mark_staged_objects_cleaned(self, object_keys):
        for key in object_keys:
            self.staged[key].status = "CLEANED"

    async def begin_execution(self, execution):
        self.executions[execution.execution_id] = execution
        return execution

    async def finish_execution(self, execution, **values):
        for key, value in values.items():
            setattr(execution, key, list(value) if key == "changed_files" else value)
        return execution

    async def add_event(self, execution_id, event_type, payload):
        events = self.events.setdefault(execution_id, [])
        event = SimpleNamespace(sequence=len(events) + 1, event_type=event_type, payload=payload)
        events.append(event)
        return event

    async def add_file_change(self, **values):
        self.separate_file_changes.append(values)


def settings():
    return SupabaseStorageSettings(
        url="https://test-project.supabase.co",
        service_role_key="test-service-role-key-never-a-real-secret",
        bucket="engineering-workspaces",
        timeout_seconds=10,
    )


class SupabaseWorkspaceStorageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.environment = patch.dict(
            os.environ,
            {
                "NIE_ENV": "test",
                "NIE_ENGINEERING_MAX_FILE_BYTES": "64",
                "NIE_ENGINEERING_MAX_WORKSPACE_BYTES": "128",
                "NIE_ENGINEERING_MAX_FILE_COUNT": "3",
            },
            clear=False,
        )
        self.environment.start()
        self.repository = FakeManifestRepository()
        self.client = FakeSupabaseClient()
        self.storage = SupabaseWorkspaceStorage(self.repository, client=self.client, settings=settings())
        self.workspace = SimpleNamespace(
            workspace_id="ews_test",
            owner_id="firebase-owner-a",
            storage_backend="SUPABASE",
        )

    async def asyncTearDown(self):
        self.environment.stop()

    async def test_upload_download_list_and_immutable_private_key(self):
        empty = await self.storage.capture(self.workspace)
        synchronized = await self.storage.reconcile(self.workspace, "tex_one", empty.revision, {"README.md": b"hello"})
        captured = await self.storage.capture(self.workspace)
        keys = await self.storage.list_objects(self.workspace.owner_id, self.workspace.workspace_id)

        self.assertEqual(b"hello", captured.files["README.md"].content)
        self.assertEqual(1, self.repository.revision)
        self.assertNotEqual(empty.revision, synchronized.revision_after)
        self.assertEqual(1, len(keys))
        self.assertNotIn(self.workspace.owner_id, keys[0])
        self.assertTrue(keys[0].endswith(hashlib.sha256(b"hello").hexdigest()))
        self.assertNotIn("README.md", keys[0])

    async def test_owner_separation_and_workspace_context_are_server_derived(self):
        digest = hashlib.sha256(b"same").hexdigest()
        a = self.storage.object_key("owner-a", "ews_one", "tex_one", digest)
        b = self.storage.object_key("owner-b", "ews_one", "tex_one", digest)
        self.assertNotEqual(a, b)
        with self.assertRaisesRegex(WorkspaceStorageError, "SUPABASE_WORKSPACE_CONTEXT_REQUIRED"):
            await self.storage.capture("client-controlled-path")
        with self.assertRaisesRegex(WorkspaceStorageError, "WORKSPACE_STORAGE_BACKEND_MISMATCH"):
            await self.storage.capture(
                SimpleNamespace(workspace_id="ews_legacy", owner_id="firebase-owner-a", storage_backend="LOCAL")
            )

    async def test_unchanged_file_preserves_responsible_execution_lineage(self):
        empty = await self.storage.capture(self.workspace)
        first = await self.storage.reconcile(
            self.workspace,
            "tex_first",
            empty.revision,
            {"unchanged.txt": b"same", "updated.txt": b"before"},
        )
        await self.storage.reconcile(
            self.workspace,
            "tex_second",
            first.revision_after,
            {"unchanged.txt": b"same", "updated.txt": b"after"},
        )
        by_path = {item.logical_path: item for item in self.repository.files}
        self.assertEqual("tex_first", by_path["unchanged.txt"].execution_id)
        self.assertEqual("tex_second", by_path["updated.txt"].execution_id)

    async def test_traversal_secret_duplicate_count_and_size_limits(self):
        empty = await self.storage.capture(self.workspace)
        cases = [
            ({"../escape": b"x"}, "SANDBOX_RETURNED_PATH_ESCAPE"),
            ({".env": b"x"}, "SANDBOX_RETURNED_SECRET_FILE"),
            ({"large": b"x" * 65}, "SANDBOX_RETURNED_FILE_TOO_LARGE"),
            ({"a": b"1", "b": b"2", "c": b"3", "d": b"4"}, "SANDBOX_RETURNED_FILE_COUNT_EXCEEDED"),
        ]
        for returned, expected in cases:
            with self.subTest(expected=expected):
                with self.assertRaisesRegex(WorkspaceStorageError, expected):
                    await self.storage.reconcile(self.workspace, "tex_invalid", empty.revision, returned)
        with self.assertRaisesRegex(WorkspaceStorageError, "SANDBOX_RETURNED_WORKSPACE_TOO_LARGE"):
            await self.storage.reconcile(
                self.workspace, "tex_total", empty.revision, {"a": b"x" * 64, "b": b"y" * 64, "c": b"z"}
            )

    async def test_atomic_manifest_switch_and_revision_conflict(self):
        empty = await self.storage.capture(self.workspace)
        first = await self.storage.reconcile(self.workspace, "tex_first", empty.revision, {"a.txt": b"one"})
        old_manifest = list(self.repository.files)
        self.repository.conflict_on_commit = True
        with self.assertRaisesRegex(WorkspaceStorageError, "WORKSPACE_SYNC_CONFLICT"):
            await self.storage.reconcile(self.workspace, "tex_conflict", first.revision_after, {"a.txt": b"two"})
        self.assertEqual(old_manifest[0].content_sha256, self.repository.files[0].content_sha256)
        self.assertEqual(1, self.repository.revision)

    async def test_partial_upload_is_cleaned_and_never_committed(self):
        empty = await self.storage.capture(self.workspace)
        self.client.fail_upload_at = 2
        with self.assertRaisesRegex(WorkspaceStorageError, "SUPABASE_STORAGE_UPLOAD_FAILED"):
            await self.storage.reconcile(
                self.workspace, "tex_partial", empty.revision, {"a.txt": b"one", "b.txt": b"two"}
            )
        self.assertEqual({}, self.client.objects)
        self.assertEqual(0, self.repository.revision)
        self.assertEqual([], self.repository.commits)

    async def test_pending_object_is_durable_before_provider_upload(self):
        empty = await self.storage.capture(self.workspace)

        def assert_pending(object_key):
            self.assertEqual("PENDING", self.repository.staged[object_key].status)

        self.client.on_upload = assert_pending
        await self.storage.reconcile(self.workspace, "tex_pending_first", empty.revision, {"a.txt": b"one"})

    async def test_database_commit_failure_removes_uploaded_objects(self):
        empty = await self.storage.capture(self.workspace)
        self.repository.fail_commit = True
        with self.assertRaisesRegex(WorkspaceStorageError, "WORKSPACE_SYNC_COMMIT_FAILED"):
            await self.storage.reconcile(self.workspace, "tex_db", empty.revision, {"a.txt": b"one"})
        self.assertEqual({}, self.client.objects)
        self.assertEqual(0, self.repository.revision)

    async def test_unknown_database_commit_status_preserves_tracked_objects(self):
        empty = await self.storage.capture(self.workspace)
        self.repository.fail_commit = True
        self.repository.fail_commit_status_read = True
        with self.assertRaisesRegex(WorkspaceStorageError, "WORKSPACE_SYNC_COMMIT_STATUS_UNKNOWN"):
            await self.storage.reconcile(self.workspace, "tex_unknown", empty.revision, {"a.txt": b"one"})
        self.assertEqual(1, len(self.client.objects))
        self.assertEqual(1, len(self.repository.staged))
        self.assertEqual("PENDING", next(iter(self.repository.staged.values())).status)

    async def test_lost_commit_acknowledgement_confirms_manifest_without_deleting_objects(self):
        empty = await self.storage.capture(self.workspace)
        self.repository.raise_after_commit = True
        synchronized = await self.storage.reconcile(
            self.workspace,
            "tex_lost_ack",
            empty.revision,
            {"a.txt": b"one"},
        )
        self.assertEqual(1, self.repository.revision)
        self.assertTrue(synchronized.revision_after.startswith("1:"))
        self.assertEqual(1, len(self.client.objects))
        self.assertEqual("COMMITTED", next(iter(self.repository.staged.values())).status)

    async def test_failed_cleanup_is_tracked_for_explicit_reconciliation(self):
        empty = await self.storage.capture(self.workspace)
        self.repository.fail_commit = True
        self.client.fail_delete = True
        with self.assertRaisesRegex(WorkspaceStorageError, "SUPABASE_STORAGE_CLEANUP_FAILED"):
            await self.storage.reconcile(self.workspace, "tex_orphan", empty.revision, {"a.txt": b"one"})
        self.assertEqual(1, len(await self.repository.abandoned_staged_objects()))
        self.client.fail_delete = False
        self.assertEqual(1, await self.storage.cleanup_abandoned(min_age_seconds=0))
        self.assertEqual([], await self.repository.abandoned_staged_objects())
        self.assertEqual({}, self.client.objects)

    async def test_cancellation_cleans_uploaded_content(self):
        empty = await self.storage.capture(self.workspace)
        self.client.cancel_upload_at = 1
        with self.assertRaises(asyncio.CancelledError):
            await self.storage.reconcile(self.workspace, "tex_cancel", empty.revision, {"a.txt": b"one"})
        self.assertEqual({}, self.client.objects)
        self.assertEqual(0, self.repository.revision)

    async def test_restart_independence_uses_database_manifest_and_objects(self):
        empty = await self.storage.capture(self.workspace)
        await self.storage.reconcile(self.workspace, "tex_persist", empty.revision, {"persist.txt": b"durable"})
        restarted = SupabaseWorkspaceStorage(self.repository, client=self.client, settings=settings())
        snapshot = await restarted.capture(self.workspace)
        self.assertEqual(b"durable", snapshot.files["persist.txt"].content)

    async def test_content_integrity_failure_is_fail_closed(self):
        empty = await self.storage.capture(self.workspace)
        await self.storage.reconcile(self.workspace, "tex_integrity", empty.revision, {"safe.txt": b"safe"})
        key = self.repository.files[0].storage_object_key
        self.client.objects[key] = b"tampered"
        with self.assertRaisesRegex(WorkspaceStorageError, "SUPABASE_STORAGE_CONTENT_INTEGRITY_FAILED"):
            await self.storage.capture(self.workspace)

    async def test_file_tool_materializes_temporarily_and_commits_once(self):
        service = ToolExecutionService(
            self.repository,
            runner=LocalDevelopmentCommandRunner(),
            storage=self.storage,
        )
        result = await service.execute(
            workspace=self.workspace,
            owner_id=self.workspace.owner_id,
            tool_name="filesystem.create",
            arguments={"path": "README.md", "content": "durable"},
            conversation_id=None,
            correlation_id="cor_supabase",
        )
        captured = await self.storage.capture(self.workspace)
        self.assertTrue(result.success)
        self.assertEqual(b"durable", captured.files["README.md"].content)
        self.assertEqual(1, len(self.repository.commits[0]["changes"]))
        self.assertEqual([], self.repository.separate_file_changes)
        self.assertEqual(
            ["tool.started", "file.created", "tool.completed"],
            [item.event_type for item in self.repository.events[result.execution_id]],
        )

    async def test_vercel_runner_uses_supabase_as_authoritative_storage(self):
        class Session:
            identifier = "sandbox-test"

            async def upload(session, snapshot):
                session.snapshot = snapshot

            async def execute(session, argv, cwd, timeout_seconds):
                return SandboxCommandResult("ok\n", "", 0)

            async def download(session):
                return {"generated.txt": b"persisted"}

            async def cancel(session):
                return None

            async def close(session):
                session.closed = True

        session = Session()

        class Client:
            async def create(self, request):
                return session

        configured = VercelSandboxSettings(
            token="test-token",
            team_id="team-test",
            project_id="project-test",
            image="vercel/sandbox/universal:latest",
            snapshot_id=None,
            sandbox_timeout_seconds=60,
            synchronization_timeout_seconds=5,
            cleanup_timeout_seconds=3,
            max_concurrent_executions=1,
            sandbox_vcpus=1,
            sandbox_memory_mb=2048,
            network_policy="deny_all",
            network_allowlist=(),
        )
        runner = VercelSandboxRunner(client=Client(), storage=self.storage, settings=configured)
        result = await runner.run(
            "tex_sandbox",
            self.workspace,
            CommandInput(argv=["python", "health_check.py"]),
            PolicyDecision("PROJECT_EXECUTION", True),
        )
        captured = await self.storage.capture(self.workspace)
        self.assertEqual(b"persisted", captured.files["generated.txt"].content)
        self.assertTrue(result.changes_persisted)
        self.assertTrue(session.closed)

    async def test_sandbox_failure_cannot_change_durable_manifest(self):
        class Session:
            identifier = "sandbox-failure"

            async def upload(self, snapshot):
                return None

            async def execute(self, argv, cwd, timeout_seconds):
                raise RuntimeError("provider failed")

            async def download(self):
                return {"should-not-exist.txt": b"unsafe"}

            async def cancel(self):
                return None

            async def close(self):
                self.closed = True

        session = Session()

        class Client:
            async def create(self, request):
                return session

        configured = VercelSandboxSettings(
            token="test-token",
            team_id="team-test",
            project_id="project-test",
            image="vercel/sandbox/universal:latest",
            snapshot_id=None,
            sandbox_timeout_seconds=60,
            synchronization_timeout_seconds=5,
            cleanup_timeout_seconds=3,
            max_concurrent_executions=1,
            sandbox_vcpus=1,
            sandbox_memory_mb=2048,
            network_policy="deny_all",
            network_allowlist=(),
        )
        runner = VercelSandboxRunner(client=Client(), storage=self.storage, settings=configured)
        with self.assertRaisesRegex(ToolExecutionError, "VERCEL_SANDBOX_PROVIDER_ERROR"):
            await runner.run(
                "tex_failed_sandbox",
                self.workspace,
                CommandInput(argv=["python", "health_check.py"]),
                PolicyDecision("PROJECT_EXECUTION", True),
            )
        self.assertEqual(0, self.repository.revision)
        self.assertEqual({}, self.client.objects)
        self.assertTrue(session.closed)


class SupabaseConfigurationTests(unittest.TestCase):
    def test_supabase_mode_does_not_require_workspace_root(self):
        environment = {
            "NIE_ENV": "production",
            "NIE_ENGINEERING_STORAGE_BACKEND": "supabase",
            "SUPABASE_URL": "https://test-project.supabase.co",
            "SUPABASE_SERVICE_ROLE_KEY": "test-service-role-key",
            "NIE_ENGINEERING_STORAGE_BUCKET": "engineering-workspaces",
            "NIE_ENGINEERING_MODE_ENABLED": "true",
            "NIE_ENGINEERING_RUNNER": "vercel_sandbox",
            "NIE_ENGINEERING_WORKSPACE_ROOT": "",
        }
        with patch.dict(os.environ, environment, clear=False):
            self.assertTrue(workspace_storage_readiness()["configured"])
            validate_workspace_storage_configuration()
            storage = build_workspace_storage(SimpleNamespace())
            self.assertIsInstance(storage, SupabaseWorkspaceStorage)

    def test_local_storage_fails_closed_in_render_shaped_environment(self):
        with patch.dict(
            os.environ,
            {
                "NIE_ENV": "staging",
                "NIE_ENGINEERING_STORAGE_BACKEND": "local",
                "NIE_ENGINEERING_MODE_ENABLED": "true",
            },
            clear=False,
        ):
            readiness = workspace_storage_readiness()
            self.assertFalse(readiness["safe_for_production"])
            with self.assertRaisesRegex(RuntimeError, "EPHEMERAL_LOCAL_STORAGE_FORBIDDEN"):
                validate_workspace_storage_configuration()

    def test_supabase_storage_rejects_incompatible_runner_when_enabled(self):
        with patch.dict(
            os.environ,
            {
                "NIE_ENV": "production",
                "NIE_ENGINEERING_STORAGE_BACKEND": "supabase",
                "SUPABASE_URL": "https://test-project.supabase.co",
                "SUPABASE_SERVICE_ROLE_KEY": "test-service-role-key",
                "NIE_ENGINEERING_MODE_ENABLED": "true",
                "NIE_ENGINEERING_RUNNER": "docker",
            },
            clear=False,
        ):
            readiness = workspace_storage_readiness()
            self.assertFalse(readiness["compatible_runner"])
            with self.assertRaisesRegex(RuntimeError, "ENGINEERING_STORAGE_RUNNER_INCOMPATIBLE"):
                validate_workspace_storage_configuration()

    def test_sensitive_configuration_is_never_returned_by_readiness(self):
        secret = "service-role-secret-that-must-not-appear"
        with patch.dict(
            os.environ,
            {
                "NIE_ENV": "production",
                "NIE_ENGINEERING_STORAGE_BACKEND": "supabase",
                "SUPABASE_URL": "https://test-project.supabase.co",
                "SUPABASE_SERVICE_ROLE_KEY": secret,
            },
            clear=False,
        ):
            self.assertNotIn(secret, str(workspace_storage_readiness()))


class SupabaseHttpClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_upload_download_list_delete_and_private_bucket_probe(self):
        seen: list[httpx.Request] = []

        async def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request)
            path = request.url.path
            if request.method == "POST" and "/object/list/" in path:
                return httpx.Response(200, json=[{"name": "object.txt"}])
            if request.method == "POST" and "/object/" in path:
                return httpx.Response(200, json={"Key": "stored"})
            if request.method == "GET" and "/object/authenticated/" in path:
                return httpx.Response(200, content=b"content")
            if request.method == "DELETE":
                return httpx.Response(200, json=[])
            if request.method == "GET" and "/bucket/" in path:
                return httpx.Response(200, json={"public": False})
            return httpx.Response(404)

        client = SupabaseStorageHttpClient(settings(), transport=httpx.MockTransport(handler))
        await client.upload("users/safe/object", b"content")
        self.assertEqual(b"content", await client.download("users/safe/object"))
        self.assertEqual(["users/safe/object.txt"], await client.list("users/safe"))
        await client.delete(["users/safe/object"])
        self.assertTrue(await client.check_bucket())

        self.assertEqual(5, len(seen))
        self.assertIn("/storage/v1/object/authenticated/engineering-workspaces/", seen[1].url.path)
        self.assertEqual({"prefixes": ["users/safe/object"]}, json.loads(seen[3].content))
        self.assertEqual("false", seen[0].headers["x-upsert"])

    async def test_bucket_probe_rejects_public_bucket(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json={"public": True})

        client = SupabaseStorageHttpClient(settings(), transport=httpx.MockTransport(handler))
        self.assertFalse(await client.check_bucket())

    async def test_provider_transport_error_returns_only_safe_classification(self):
        async def handler(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("provider transport unavailable", request=request)

        client = SupabaseStorageHttpClient(settings(), transport=httpx.MockTransport(handler))
        with self.assertRaisesRegex(WorkspaceStorageError, "SUPABASE_STORAGE_UPLOAD_FAILED") as captured:
            await client.upload("users/safe/object", b"content")
        self.assertEqual("SUPABASE_STORAGE_UPLOAD_FAILED", str(captured.exception))


class SupabaseReadinessProbeTests(unittest.IsolatedAsyncioTestCase):
    async def test_unreachable_or_public_bucket_is_not_production_safe(self):
        environment = {
            "NIE_ENV": "production",
            "NIE_ENGINEERING_STORAGE_BACKEND": "supabase",
            "SUPABASE_URL": "https://test-project.supabase.co",
            "SUPABASE_SERVICE_ROLE_KEY": "test-service-role-key",
            "NIE_ENGINEERING_RUNNER": "vercel_sandbox",
        }
        with (
            patch.dict(os.environ, environment, clear=False),
            patch(
                "app.services.supabase_workspace_storage.probe_supabase_storage",
                new=AsyncMock(
                    return_value={
                        "backend": "supabase",
                        "configured": True,
                        "reachable": False,
                        "ready": False,
                    }
                ),
            ),
        ):
            readiness = await probe_workspace_storage()
        self.assertFalse(readiness["safe_for_production"])
        self.assertEqual("SUPABASE_STORAGE_PRIVATE_BUCKET_UNAVAILABLE", readiness["reason"])


class SupabaseWorkspaceManagerTests(unittest.IsolatedAsyncioTestCase):
    async def test_workspace_creation_uses_opaque_locator_without_local_directory(self):
        class Repository:
            async def create_workspace(self, record, conversation_id=None):
                timestamp = datetime.now(timezone.utc)
                record.created_at = timestamp
                record.updated_at = timestamp
                record.last_activity_at = timestamp
                record.status = "ACTIVE"
                self.record = record
                return record

            async def conversation_ids(self, workspace_id, owner_id):
                return []

        repository = Repository()
        with patch.dict(
            os.environ,
            {
                "NIE_ENV": "production",
                "NIE_ENGINEERING_STORAGE_BACKEND": "supabase",
                "NIE_ENGINEERING_STORAGE_BUCKET": "engineering-workspaces",
                "NIE_ENGINEERING_WORKSPACE_ROOT": "",
            },
            clear=False,
        ):
            manager = WorkspaceManager(repository)
            await manager.create("firebase-owner", SimpleNamespace(name="workspace", conversation_id=None, metadata={}))
        self.assertEqual("SUPABASE", repository.record.storage_backend)
        self.assertTrue(repository.record.root_path.startswith("supabase://engineering-workspaces/ews_"))


if __name__ == "__main__":
    unittest.main()
