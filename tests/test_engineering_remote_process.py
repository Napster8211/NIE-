import asyncio
import hashlib
import os
import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.schemas.engineering_workspace import ProcessStartInput
from app.services.engineering_execution_service import ToolExecutionError, probe_engineering_runner_configuration
from app.services.engineering_process_service import VercelSandboxProcessManager, build_process_manager
from app.services.engineering_workspace_storage import (
    WorkspaceSnapshot,
    WorkspaceStorageError,
    WorkspaceStoredFile,
    WorkspaceSyncResult,
)
from app.services.vercel_sandbox_runner import (
    _LIVE_CAPABILITY_STATE,
    SandboxPreviewHandle,
    SandboxPreviewState,
    VercelSandboxSettings,
    mark_vercel_sandbox_capability_verified,
    vercel_sandbox_readiness,
)


def sandbox_settings() -> VercelSandboxSettings:
    return VercelSandboxSettings(
        token="test-token-not-a-secret",
        team_id="team_test",
        project_id="project_test",
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


class FakeProcessRepository:
    def __init__(self, workspace):
        self.workspace = workspace
        self.executions = {}
        self.processes = {}
        self.events = {}
        self.create_process_error = None

    async def begin_execution(self, execution):
        execution.started_at = datetime.now(timezone.utc)
        self.executions[execution.execution_id] = execution
        return execution

    async def get_execution(self, execution_id, workspace_id, owner_id):
        execution = self.executions.get(execution_id)
        if execution and execution.workspace_id == workspace_id and execution.owner_id == owner_id:
            return execution
        return None

    async def finish_execution(self, execution, **values):
        for key, value in values.items():
            setattr(execution, key, value)
        execution.finished_at = datetime.now(timezone.utc)
        return execution

    async def add_event(self, execution_id, event_type, payload):
        events = self.events.setdefault(execution_id, [])
        event = SimpleNamespace(sequence=len(events) + 1, event_type=event_type, payload=payload)
        events.append(event)
        return event

    async def create_process(self, process):
        if self.create_process_error:
            raise self.create_process_error
        self.processes[process.process_id] = process
        return process

    async def update_process(self, process):
        self.processes[process.process_id] = process
        return process

    async def count_active_processes(self, owner_id):
        return sum(
            process.owner_id == owner_id and process.status in {"STARTING", "RUNNING", "READY", "STOPPING"}
            for process in self.processes.values()
        )

    async def get_process(self, process_id, workspace_id, owner_id):
        process = self.processes.get(process_id)
        if process and process.workspace_id == workspace_id and process.owner_id == owner_id:
            return process
        return None

    async def get_workspace(self, workspace_id, owner_id):
        if self.workspace.workspace_id == workspace_id and self.workspace.owner_id == owner_id:
            return self.workspace
        return None

    async def list_active_processes(self, workspace_id, owner_id):
        return [
            process
            for process in self.processes.values()
            if process.workspace_id == workspace_id
            and process.owner_id == owner_id
            and process.status in {"STARTING", "RUNNING", "READY", "STOPPING"}
        ]

    async def list_expired_processes(self, *, limit, minimum_age_seconds):
        now = datetime.now(timezone.utc)
        return [
            process
            for process in self.processes.values()
            if process.status in {"STARTING", "RUNNING", "READY", "STOPPING"} and process.expires_at <= now
        ][:limit]

    async def claim_process_start(
        self,
        process_id,
        workspace_id,
        owner_id,
        claim_id,
        *,
        stale_after_seconds=60,
    ):
        process = await self.get_process(process_id, workspace_id, owner_id)
        if process is None or process.status != "STARTING" or process.cancellation_requested_at:
            return False
        if process.startup_claim_id:
            return False
        process.startup_claim_id = claim_id
        process.startup_claimed_at = datetime.now(timezone.utc)
        return True

    async def transition_process(
        self,
        *,
        process_id,
        workspace_id,
        owner_id,
        expected_statuses,
        status,
        health_status,
        values=None,
    ):
        process = await self.get_process(process_id, workspace_id, owner_id)
        if process is None:
            return None, False
        if process.status not in expected_statuses:
            return process, False
        for key, value in (values or {}).items():
            setattr(process, key, value)
        process.status = status
        process.health_status = health_status
        process.last_checked_at = datetime.now(timezone.utc)
        return process, True

    async def claim_process_cleanup(
        self,
        process_id,
        workspace_id,
        owner_id,
        claim_id,
        *,
        stale_after_seconds=60,
    ):
        process = await self.get_process(process_id, workspace_id, owner_id)
        if process is None or process.status != "STOPPING":
            return False
        if process.cleanup_claim_id:
            return False
        process.cleanup_claim_id = claim_id
        process.cleanup_claimed_at = datetime.now(timezone.utc)
        return True

    async def get_process_for_preview(self, *, process_id, workspace_id, owner_id, token_hash):
        process = self.processes.get(process_id)
        if (
            process
            and process.workspace_id == workspace_id
            and process.owner_id == owner_id
            and process.preview_session_token_hash == token_hash
            and process.preview_session_expires_at > datetime.now(timezone.utc)
        ):
            return process
        return None

    async def workspace_revision_token(self, workspace_id, owner_id):
        return self.workspace.storage_revision_token


class FakeAsyncStorage:
    is_async = True
    max_file_bytes = 1_024
    max_workspace_bytes = 4_096
    max_file_count = 20

    def __init__(self, files=None):
        self.files = dict(files or {})
        self.revision = "revision-0"
        self.captured = 0
        self.reconciled = []
        self.fail_capture = None
        self.fail_reconcile = None

    async def capture(self, _workspace):
        self.captured += 1
        if self.fail_capture:
            raise WorkspaceStorageError(self.fail_capture)
        stored = {
            path: WorkspaceStoredFile(path, content, hashlib.sha256(content).hexdigest())
            for path, content in self.files.items()
        }
        return WorkspaceSnapshot(self.revision, stored, sum(len(content) for content in self.files.values()))

    async def reconcile(self, _workspace, execution_id, base_revision, returned_files):
        if self.fail_reconcile:
            raise WorkspaceStorageError(self.fail_reconcile)
        if base_revision != self.revision:
            raise WorkspaceStorageError("WORKSPACE_SYNC_CONFLICT")
        changes = []
        for path, content in returned_files.items():
            if self.files.get(path) != content:
                changes.append({"path": path, "operation": "CREATED" if path not in self.files else "MODIFIED"})
        for path in set(self.files) - set(returned_files):
            changes.append({"path": path, "operation": "DELETED"})
        self.files = dict(returned_files)
        previous = self.revision
        self.revision = f"revision-{len(self.reconciled) + 1}"
        self.reconciled.append((execution_id, base_revision, dict(returned_files)))
        return WorkspaceSyncResult(previous, self.revision, tuple(changes))


class FakePreviewClient:
    def __init__(self):
        self.started = []
        self.inspections = []
        self.destroyed = []
        self.stopped = []
        self.start_error = None
        self.inspect_error = None
        self.stop_error = None
        self.state = SandboxPreviewState("RUNNING", None, "https://preview.example.test")
        self.returned_files = {}

    async def start(self, request, snapshot, argv, cwd, port, timeout_seconds):
        self.started.append((request, snapshot, argv, cwd, port, timeout_seconds))
        if self.start_error:
            raise self.start_error
        return SandboxPreviewHandle("sandbox_test", "process_test", "https://preview.example.test")

    async def inspect(self, sandbox_identifier, process_id, port):
        self.inspections.append((sandbox_identifier, process_id, port))
        if self.inspect_error:
            raise self.inspect_error
        return self.state

    async def stop_and_collect(self, sandbox_identifier, process_id, port, request):
        self.stopped.append((sandbox_identifier, process_id, port, request))
        if self.stop_error:
            raise self.stop_error
        return SandboxPreviewState(
            "STOPPED",
            0,
            "https://preview.example.test",
            "SERVER_STOPPED\n",
            "",
            self.returned_files,
        )

    async def destroy(self, sandbox_identifier):
        self.destroyed.append(sandbox_identifier)


class FakeProviderNotFound(RuntimeError):
    status_code = 404


class RemoteProcessManagerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.workspace = SimpleNamespace(
            workspace_id="ews_test",
            owner_id="firebase:owner",
            root_path="supabase://private/ews_test",
            storage_backend="LOCAL",
            storage_revision_token="revision-0",
        )
        self.repository = FakeProcessRepository(self.workspace)
        self.storage = FakeAsyncStorage({"README.md": b"# Test\n"})
        self.client = FakePreviewClient()
        self.environment = patch.dict(
            os.environ,
            {
                "NIE_ENV": "test",
                "NIE_ENGINEERING_RUNNER": "vercel_sandbox",
                "NIE_ENGINEERING_MAX_PROCESSES": "1",
                "NIE_ENGINEERING_MAX_OUTPUT_BYTES": "10000",
                "NIE_ENGINEERING_PREVIEW_TIMEOUT_SECONDS": "60",
                "NIE_ENGINEERING_PREVIEW_READINESS_TIMEOUT_SECONDS": "2",
            },
            clear=False,
        )
        self.environment.start()

    async def asyncTearDown(self):
        self.environment.stop()

    def manager(self, *, ready=True):
        return VercelSandboxProcessManager(
            self.repository,
            client=self.client,
            settings=sandbox_settings(),
            storage=self.storage,
            readiness_probe=AsyncMock(return_value=ready),
        )

    @staticmethod
    def request(argv=None, cwd=""):
        return ProcessStartInput(argv=argv or ["npm", "run", "dev"], cwd=cwd, port=5173)

    async def start_ready(self, manager=None):
        manager = manager or self.manager()
        started = await manager.start(self.workspace, self.workspace.owner_id, self.request())
        self.assertEqual("STARTING", started.status)
        return await manager.inspect(self.workspace.workspace_id, self.workspace.owner_id, started.process_id)

    async def test_preview_hydrates_authoritative_workspace_and_persists_remote_handle(self):
        response = await self.start_ready()

        self.assertEqual("READY", response.status)
        self.assertEqual("READY", response.health_status)
        self.assertEqual("vercel_sandbox", response.runner)
        self.assertEqual(
            f"/api/v1/engineering/workspaces/ews_test/processes/{response.process_id}/preview/",
            response.preview_url,
        )
        self.assertNotIn("preview.example.test", response.model_dump_json())
        request, snapshot, argv, cwd, port, timeout = self.client.started[0]
        self.assertEqual({"README.md"}, set(snapshot.files))
        self.assertEqual(["npm", "run", "dev", "--", "--host", "0.0.0.0", "--port", "5173"], argv)
        self.assertEqual((), request.network_allowed_hosts)
        self.assertEqual(5173, request.ports[0])
        self.assertEqual("", cwd)
        self.assertEqual(60, timeout)
        process = self.repository.processes[response.process_id]
        self.assertEqual("sandbox_test", process.sandbox_identifier)
        self.assertRegex(response.execution_id, r"^tex_[0-9a-f]{32}$")
        self.assertEqual("RUNNING", self.repository.executions[response.execution_id].status)

    async def test_stop_reconciles_changes_and_emits_one_durable_terminal_event(self):
        manager = self.manager()
        response = await self.start_ready(manager)
        self.client.returned_files = {"README.md": b"# Updated\n", "src/main.js": b"console.log('ok')\n"}

        stopped = await manager.stop(self.workspace.workspace_id, self.workspace.owner_id, response.process_id)
        repeated = await manager.stop(self.workspace.workspace_id, self.workspace.owner_id, response.process_id)

        self.assertEqual("STOPPED", stopped.status)
        self.assertEqual("STOPPED", repeated.status)
        self.assertEqual({"README.md", "src/main.js"}, set(self.storage.files))
        execution = self.repository.executions[response.execution_id]
        self.assertEqual("SUCCEEDED", execution.status)
        events = self.repository.events[response.execution_id]
        self.assertEqual(1, sum(event.event_type == "tool.completed" for event in events))
        self.assertEqual(1, sum(event.event_type == "process.stopped" for event in events))
        self.assertEqual(1, len(self.client.stopped))

    async def test_new_manager_recovers_running_preview_from_persisted_remote_identifiers(self):
        response = await self.start_ready()

        restarted_manager = self.manager()
        inspected = await restarted_manager.inspect(
            self.workspace.workspace_id, self.workspace.owner_id, response.process_id
        )

        self.assertEqual("READY", inspected.health_status)
        self.assertEqual([("sandbox_test", "process_test", 5173)] * 2, self.client.inspections)

    async def test_readiness_timeout_destroys_sandbox_and_fails_execution_once(self):
        manager = self.manager(ready=False)
        started = await manager.start(self.workspace, self.workspace.owner_id, self.request())
        with self.assertRaises(ToolExecutionError) as caught:
            await manager.inspect(self.workspace.workspace_id, self.workspace.owner_id, started.process_id)

        self.assertEqual("PREVIEW_READINESS_TIMEOUT", caught.exception.code)
        process = next(iter(self.repository.processes.values()))
        self.assertEqual("FAILED", process.status)
        self.assertEqual(["sandbox_test"], self.client.destroyed)
        events = self.repository.events[process.execution_id]
        self.assertEqual(1, sum(event.event_type == "tool.failed" for event in events))

    async def test_sandbox_creation_and_process_execution_failure_is_audited(self):
        self.client.start_error = RuntimeError("provider failed token=must-not-leak")
        manager = self.manager()
        started = await manager.start(self.workspace, self.workspace.owner_id, self.request())
        with self.assertRaisesRegex(ToolExecutionError, "VERCEL_SANDBOX_PROVIDER_ERROR"):
            await manager.inspect(self.workspace.workspace_id, self.workspace.owner_id, started.process_id)
        process = next(iter(self.repository.processes.values()))
        self.assertNotIn("must-not-leak", process.log_excerpt)
        self.assertEqual("FAILED", self.repository.executions[process.execution_id].status)

    async def test_storage_capture_failure_does_not_leave_running_execution(self):
        self.storage.fail_capture = "SUPABASE_STORAGE_DOWNLOAD_FAILED"
        manager = self.manager()
        started = await manager.start(self.workspace, self.workspace.owner_id, self.request())
        with self.assertRaisesRegex(ToolExecutionError, "SUPABASE_STORAGE_DOWNLOAD_FAILED"):
            await manager.inspect(self.workspace.workspace_id, self.workspace.owner_id, started.process_id)
        process = next(iter(self.repository.processes.values()))
        self.assertEqual("SYNC_FAILED", self.repository.executions[process.execution_id].status)

    async def test_process_record_commit_failure_does_not_leave_running_execution(self):
        self.repository.create_process_error = RuntimeError("database commit acknowledgement lost")
        with self.assertRaisesRegex(ToolExecutionError, "PROCESS_PERSISTENCE_FAILED"):
            await self.manager().start(self.workspace, self.workspace.owner_id, self.request())
        execution = next(iter(self.repository.executions.values()))
        self.assertEqual("FAILED", execution.status)
        self.assertEqual("PROCESS_PERSISTENCE_FAILED", execution.error_type)
        self.assertEqual(
            ["tool.requested", "tool.started", "tool.failed"],
            [e.event_type for e in self.repository.events[execution.execution_id]],
        )

    async def test_reconciliation_conflict_fails_closed_without_false_success(self):
        manager = self.manager()
        response = await self.start_ready(manager)
        self.client.returned_files = {"README.md": b"changed"}
        self.storage.fail_reconcile = "WORKSPACE_SYNC_CONFLICT"
        with self.assertRaisesRegex(ToolExecutionError, "WORKSPACE_SYNC_CONFLICT"):
            await manager.stop(self.workspace.workspace_id, self.workspace.owner_id, response.process_id)
        self.assertEqual("SYNC_FAILED", self.repository.executions[response.execution_id].status)
        self.assertNotEqual(b"changed", self.storage.files["README.md"])

    async def test_commands_are_structured_and_shell_injection_paths_are_rejected(self):
        rejected = [
            ["npm", "run", "dev", ";", "whoami"],
            ["sh", "-c", "npm run dev"],
            ["python", "-m", "http.server", "5173", "&&", "whoami"],
        ]
        for argv in rejected:
            with self.subTest(argv=argv):
                with self.assertRaisesRegex(ToolExecutionError, "PROCESS_COMMAND_REJECTED"):
                    await self.manager().start(self.workspace, self.workspace.owner_id, self.request(argv=argv))
        with self.assertRaisesRegex(ToolExecutionError, "PROCESS_CWD_ESCAPE_REJECTED"):
            await self.manager().start(self.workspace, self.workspace.owner_id, self.request(cwd="../other"))
        self.assertEqual([], self.client.started)

    async def test_wrong_owner_and_process_limit_fail_closed(self):
        with self.assertRaisesRegex(ToolExecutionError, "WORKSPACE_OWNERSHIP_REJECTED"):
            await self.manager().start(self.workspace, "firebase:attacker", self.request())
        await self.manager().start(self.workspace, self.workspace.owner_id, self.request())
        with self.assertRaisesRegex(ToolExecutionError, "PROCESS_LIMIT_REACHED"):
            await self.manager().start(self.workspace, self.workspace.owner_id, self.request())

    async def test_start_is_durable_before_provider_work_and_can_be_cancelled(self):
        manager = self.manager()
        started = await manager.start(self.workspace, self.workspace.owner_id, self.request())

        self.assertEqual("STARTING", started.status)
        self.assertEqual([], self.client.started)
        cancelled = await manager.stop(self.workspace.workspace_id, self.workspace.owner_id, started.process_id)
        repeated = await manager.stop(self.workspace.workspace_id, self.workspace.owner_id, started.process_id)

        self.assertEqual("CANCELLED", cancelled.status)
        self.assertEqual("CANCELLED", repeated.status)
        self.assertEqual([], self.client.started)
        events = self.repository.events[started.execution_id]
        self.assertEqual(1, sum(event.event_type == "tool.failed" for event in events))

    async def test_expiry_before_start_is_terminal_and_idempotent(self):
        manager = self.manager()
        started = await manager.start(self.workspace, self.workspace.owner_id, self.request())
        process = self.repository.processes[started.process_id]
        process.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)

        expired = await manager.inspect(self.workspace.workspace_id, self.workspace.owner_id, started.process_id)
        repeated = await manager.inspect(self.workspace.workspace_id, self.workspace.owner_id, started.process_id)

        self.assertEqual("EXPIRED", expired.status)
        self.assertEqual("EXPIRED", repeated.status)
        self.assertEqual([], self.client.started)
        self.assertEqual("TIMED_OUT", self.repository.executions[started.execution_id].status)

    async def test_ready_process_expiry_reconciles_and_destroys_sandbox(self):
        manager = self.manager()
        ready = await self.start_ready(manager)
        self.client.returned_files = {"README.md": b"# expired but saved\n"}
        self.repository.processes[ready.process_id].expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)

        expired = await manager.inspect(self.workspace.workspace_id, self.workspace.owner_id, ready.process_id)

        self.assertEqual("EXPIRED", expired.status)
        self.assertEqual(b"# expired but saved\n", self.storage.files["README.md"])
        self.assertEqual(1, len(self.client.stopped))

    async def test_provider_already_missing_is_successful_expiry_cleanup(self):
        manager = self.manager()
        ready = await self.start_ready(manager)
        process = self.repository.processes[ready.process_id]
        process.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        self.client.stop_error = FakeProviderNotFound("gone")

        expired = await manager.inspect(self.workspace.workspace_id, self.workspace.owner_id, ready.process_id)

        self.assertEqual("EXPIRED", expired.status)
        self.assertEqual("PROCESS_EXPIRED_PROVIDER_ALREADY_MISSING", process.terminal_reason)

    async def test_provider_cleanup_failure_is_preserved_on_expiry(self):
        manager = self.manager()
        ready = await self.start_ready(manager)
        process = self.repository.processes[ready.process_id]
        process.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        self.client.stop_error = RuntimeError("provider cleanup failed token=never-log")

        expired = await manager.inspect(self.workspace.workspace_id, self.workspace.owner_id, ready.process_id)

        self.assertEqual("EXPIRED", expired.status)
        self.assertEqual("PROCESS_EXPIRED_CLEANUP_FAILED", process.terminal_reason)
        self.assertNotIn("never-log", self.repository.executions[ready.execution_id].stderr)

    async def test_active_process_list_is_workspace_and_owner_isolated(self):
        manager = self.manager()
        started = await manager.start(self.workspace, self.workspace.owner_id, self.request())

        restored = await manager.list_active(self.workspace.workspace_id, self.workspace.owner_id)

        self.assertEqual([started.process_id], [item.process_id for item in restored])
        with self.assertRaisesRegex(ToolExecutionError, "WORKSPACE_NOT_FOUND"):
            await manager.list_active(self.workspace.workspace_id, "firebase:attacker")

    async def test_backend_restart_resumes_unclaimed_startup_from_database(self):
        started = await self.manager().start(self.workspace, self.workspace.owner_id, self.request())

        ready = await self.manager().inspect(
            self.workspace.workspace_id,
            self.workspace.owner_id,
            started.process_id,
        )

        self.assertEqual("READY", ready.status)
        self.assertEqual(1, len(self.client.started))

    async def test_preview_session_is_short_lived_and_provider_url_is_not_returned(self):
        configured = {
            "NIE_ENGINEERING_MODE_ENABLED": "true",
            "NIE_ENGINEERING_OWNER_ONLY": "true",
            "NIE_OWNER_FIREBASE_UIDS": "owner",
        }
        with patch.dict(os.environ, configured, clear=False):
            manager = self.manager()
            ready = await self.start_ready(manager)
            preview, token = await manager.issue_preview_session(
                self.workspace.workspace_id,
                self.workspace.owner_id,
                ready.process_id,
            )
            authorized = await manager.authorize_preview(
                self.workspace.workspace_id,
                self.workspace.owner_id,
                ready.process_id,
                token,
            )

        self.assertIs(authorized, self.repository.processes[ready.process_id])
        self.assertTrue(preview.preview_url.startswith("/api/v1/engineering/"))
        self.assertNotIn("preview.example.test", preview.model_dump_json())

    async def test_preview_session_rejects_unauthenticated_cross_workspace_and_revoked_owner(self):
        configured = {
            "NIE_ENGINEERING_MODE_ENABLED": "true",
            "NIE_ENGINEERING_OWNER_ONLY": "true",
            "NIE_OWNER_FIREBASE_UIDS": "owner",
        }
        with patch.dict(os.environ, configured, clear=False):
            manager = self.manager()
            ready = await self.start_ready(manager)
            _, token = await manager.issue_preview_session(
                self.workspace.workspace_id,
                self.workspace.owner_id,
                ready.process_id,
            )
            for workspace_id, supplied_token in (
                (self.workspace.workspace_id, ""),
                ("ews_other", token),
            ):
                with self.subTest(workspace_id=workspace_id):
                    with self.assertRaisesRegex(ToolExecutionError, "PROCESS_NOT_FOUND"):
                        await manager.authorize_preview(
                            workspace_id,
                            self.workspace.owner_id,
                            ready.process_id,
                            supplied_token,
                        )
            with patch.dict(os.environ, {"NIE_OWNER_FIREBASE_UIDS": "different-owner"}, clear=False):
                with self.assertRaisesRegex(ToolExecutionError, "PROCESS_NOT_FOUND"):
                    await manager.authorize_preview(
                        self.workspace.workspace_id,
                        "firebase:other-owner",
                        ready.process_id,
                        token,
                    )

    async def test_stop_and_expiry_race_has_one_terminal_execution_event(self):
        manager = self.manager()
        ready = await self.start_ready(manager)
        process = self.repository.processes[ready.process_id]
        process.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)

        outcomes = await asyncio.gather(
            manager.stop(self.workspace.workspace_id, self.workspace.owner_id, ready.process_id),
            manager.inspect(self.workspace.workspace_id, self.workspace.owner_id, ready.process_id),
        )

        self.assertTrue(all(item.status == "EXPIRED" for item in outcomes))
        events = self.repository.events[ready.execution_id]
        self.assertEqual(1, sum(event.event_type == "tool.failed" for event in events))
        self.assertEqual(1, sum(event.event_type == "process.stopped" for event in events))

    async def test_production_vercel_selection_never_falls_back_to_local_processes(self):
        sentinel = object()
        with patch(
            "app.services.engineering_process_service.VercelSandboxProcessManager", return_value=sentinel
        ) as remote:
            selected = build_process_manager(self.repository)
        self.assertIs(sentinel, selected)
        remote.assert_called_once_with(self.repository)

    def test_health_distinguishes_credentials_sdk_support_and_live_execution(self):
        configured = {
            "VERCEL_TOKEN": "test-token",
            "VERCEL_TEAM_ID": "team-test",
            "VERCEL_PROJECT_ID": "project-test",
            "NIE_ENGINEERING_SANDBOX_VCPUS": "1",
            "NIE_ENGINEERING_SANDBOX_MEMORY_MB": "2048",
        }
        original = dict(_LIVE_CAPABILITY_STATE)
        try:
            _LIVE_CAPABILITY_STATE.update(
                command_execution_verified=False,
                preview_execution_verified=False,
                last_error=None,
            )
            with patch.dict(os.environ, configured, clear=False):
                unverified = vercel_sandbox_readiness()
                self.assertTrue(unverified["configured"])
                self.assertTrue(unverified["command_execution_supported"])
                self.assertFalse(unverified["ready"])
                self.assertFalse(unverified["process_execution_ready"])
                self.assertEqual("CONFIGURED_UNVERIFIED", unverified["readiness_classification"])

                mark_vercel_sandbox_capability_verified("command")
                command_ready = vercel_sandbox_readiness()
                self.assertTrue(command_ready["ready"])
                self.assertFalse(command_ready["process_execution_ready"])

                mark_vercel_sandbox_capability_verified("preview")
                preview_ready = vercel_sandbox_readiness()
                self.assertTrue(preview_ready["process_execution_ready"])
        finally:
            _LIVE_CAPABILITY_STATE.clear()
            _LIVE_CAPABILITY_STATE.update(original)

    async def test_enabled_service_requires_an_explicit_successful_live_probe(self):
        configured = {
            "NIE_ENGINEERING_MODE_ENABLED": "true",
            "NIE_ENGINEERING_RUNNER": "vercel_sandbox",
            "NIE_ENGINEERING_RUNNER_LIVE_PROBE": "false",
            "VERCEL_TOKEN": "test-token",
            "VERCEL_TEAM_ID": "team-test",
            "VERCEL_PROJECT_ID": "project-test",
            "NIE_ENGINEERING_SANDBOX_VCPUS": "1",
            "NIE_ENGINEERING_SANDBOX_MEMORY_MB": "2048",
        }
        with patch.dict(os.environ, configured, clear=False):
            with self.assertRaisesRegex(RuntimeError, "VERCEL_SANDBOX_LIVE_PROBE_REQUIRED"):
                await probe_engineering_runner_configuration()

        configured["NIE_ENGINEERING_RUNNER_LIVE_PROBE"] = "true"
        with (
            patch.dict(os.environ, configured, clear=False),
            patch(
                "app.services.vercel_sandbox_runner.probe_vercel_sandbox_execution",
                new=AsyncMock(return_value={"configured": True, "ready": True}),
            ) as probe,
        ):
            result = await probe_engineering_runner_configuration()
        self.assertTrue(result["ready"])
        probe.assert_awaited_once()

    def test_remote_process_migration_is_additive_and_reconnectable(self):
        from pathlib import Path

        migration = (
            Path(__file__).resolve().parents[1] / "database" / "migrations" / "003_engineering_remote_processes.sql"
        ).read_text(encoding="utf-8")
        self.assertIn("ADD COLUMN IF NOT EXISTS sandbox_identifier", migration)
        self.assertIn("ADD COLUMN IF NOT EXISTS workspace_revision", migration)
        self.assertIn("ADD COLUMN IF NOT EXISTS cancellation_requested_at", migration)
        self.assertIn("'EXPIRED'", migration)
        self.assertIn("ENGINEERING_ACTIVE_PROCESS_DUPLICATES_DETECTED", migration)
        self.assertIn("uq_workspace_processes_active_workspace", migration)
        self.assertNotIn("DROP TABLE", migration.upper())


if __name__ == "__main__":
    unittest.main()
