import asyncio
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.schemas.engineering_workspace import CommandInput
from app.services.engineering_execution_service import (
    PolicyDecision,
    ToolExecutionError,
    ToolExecutionService,
    UnavailableCommandRunner,
    build_command_runner,
)
from app.services.engineering_workspace_storage import LocalDurableWorkspaceStorage
from app.services.vercel_sandbox_runner import (
    SandboxCommandResult,
    SandboxCreateRequest,
    VercelPythonSandboxSession,
    VercelSandboxRunner,
    VercelSandboxSettings,
)


def settings(**overrides):
    values = {
        "token": "test-token-not-a-production-secret",
        "team_id": "team_test",
        "project_id": "project_test",
        "image": "vercel/sandbox/universal:latest",
        "snapshot_id": None,
        "sandbox_timeout_seconds": 60,
        "synchronization_timeout_seconds": 5,
        "cleanup_timeout_seconds": 3,
        "max_concurrent_executions": 1,
        "sandbox_vcpus": 1,
        "sandbox_memory_mb": 2048,
        "network_policy": "deny_all",
        "network_allowlist": (),
    }
    values.update(overrides)
    return VercelSandboxSettings(**values)


class FakeProviderError(Exception):
    def __init__(self, *, status_code=None, code=""):
        super().__init__("provider details token=must-be-redacted")
        self.status_code = status_code
        self.code = code


class FakeSandboxSession:
    def __init__(self, *, returned=None, result=None, execute_error=None, close_error=None, mutate=None):
        self.identifier = "sbx_provider_identifier"
        self.returned = returned
        self.result = result or SandboxCommandResult("ok\n", "", 0)
        self.execute_error = execute_error
        self.close_error = close_error
        self.mutate = mutate
        self.uploaded = None
        self.executed = None
        self.cancelled = False
        self.closed = False
        self.wait_for_cancel = False
        self._cancel_event = asyncio.Event()

    async def upload(self, snapshot):
        self.uploaded = snapshot

    async def execute(self, argv, cwd, timeout_seconds):
        self.executed = (argv, cwd, timeout_seconds)
        if self.wait_for_cancel:
            await self._cancel_event.wait()
            raise RuntimeError("sandbox terminated")
        if self.execute_error:
            raise self.execute_error
        return self.result

    async def download(self):
        if self.mutate:
            self.mutate()
        if self.returned is not None:
            return self.returned
        return {path: stored.content for path, stored in self.uploaded.files.items()}

    async def cancel(self):
        self.cancelled = True
        self._cancel_event.set()

    async def close(self):
        self.closed = True
        if self.close_error:
            raise self.close_error


class FakeSandboxClient:
    def __init__(self, session=None, error=None):
        self.session = session or FakeSandboxSession()
        self.error = error
        self.requests = []

    async def create(self, request: SandboxCreateRequest):
        self.requests.append(request)
        if self.error:
            raise self.error
        return self.session


class MemoryAuditRepository:
    def __init__(self):
        self.executions = {}
        self.events = {}
        self.file_changes = []

    async def begin_execution(self, execution):
        execution.started_at = datetime.now(timezone.utc)
        self.executions[execution.execution_id] = execution
        return execution

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

    async def add_file_change(self, **values):
        self.file_changes.append(values)
        return SimpleNamespace(**values)


class VercelSandboxRunnerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.environment = patch.dict(
            os.environ,
            {
                "NIE_ENV": "test",
                "NIE_ENGINEERING_MAX_FILE_BYTES": "1024",
                "NIE_ENGINEERING_MAX_WORKSPACE_BYTES": "4096",
                "NIE_ENGINEERING_MAX_FILE_COUNT": "20",
                "NIE_ENGINEERING_MAX_CONCURRENT_EXECUTIONS": "1",
                "NIE_ENGINEERING_MAX_OUTPUT_BYTES": "1000",
                "TEST_SECRET_TOKEN": "never-return-this-value",
            },
            clear=False,
        )
        self.environment.start()
        self.storage = LocalDurableWorkspaceStorage()

    async def asyncTearDown(self):
        self.environment.stop()
        self.temp.cleanup()

    def runner(self, session=None, *, client=None, configured=None):
        fake = client or FakeSandboxClient(session)
        return VercelSandboxRunner(
            client=fake,
            storage=self.storage,
            settings=configured or settings(),
        ), fake

    async def run_command(self, runner, *, network=False, approved=False, timeout=10, max_output_bytes=100_000):
        command = CommandInput(
            argv=["python", "health_check.py"],
            timeout_seconds=timeout,
            max_output_bytes=max_output_bytes,
            allow_network=network,
            approval_granted=approved,
        )
        policy = PolicyDecision(
            "DEPENDENCY_INSTALL" if network else "PROJECT_EXECUTION",
            True,
            approval_required=network,
            network_allowed=network,
        )
        return await runner.run("tex_test", str(self.root), command, policy)

    async def test_runner_uploads_executes_downloads_and_persists_changed_files(self):
        (self.root / "health_check.py").write_text("print('before')\n", encoding="utf-8")
        session = FakeSandboxSession(
            returned={
                "health_check.py": b"print('after')\n",
                "result.txt": b"HEALTH_CHECK_OK\n",
            },
            result=SandboxCommandResult("HEALTH_CHECK_OK\n", "warning\n", 0),
        )
        runner, client = self.runner(session)

        result = await self.run_command(runner)

        self.assertEqual(0, result.exit_code)
        self.assertEqual("HEALTH_CHECK_OK", result.stdout.strip())
        self.assertEqual(["python", "health_check.py"], session.executed[0])
        self.assertEqual({"health_check.py"}, set(session.uploaded.files))
        self.assertEqual("HEALTH_CHECK_OK\n", (self.root / "result.txt").read_text(encoding="utf-8"))
        self.assertEqual({"health_check.py", "result.txt"}, set(result.changed_files))
        request = client.requests[0]
        self.assertEqual((), request.network_allowed_hosts)
        self.assertEqual(1024, request.max_file_bytes)
        self.assertEqual(1, request.vcpus)
        self.assertEqual(2048, request.memory_mb)
        self.assertTrue(session.closed)
        self.assertNotIn(session.identifier, str(result.data))
        self.assertRegex(result.data["sandbox_reference"], r"^[0-9a-f]{20}$")

    async def test_stdout_stderr_are_bounded_and_secrets_redacted(self):
        secret = os.environ["TEST_SECRET_TOKEN"]
        session = FakeSandboxSession(result=SandboxCommandResult(secret + "x" * 3000, "token=unsafe", 0))
        runner, _ = self.runner(session)
        result = await self.run_command(runner, max_output_bytes=1000)
        self.assertNotIn(secret, result.stdout)
        self.assertIn("[REDACTED]", result.stdout)
        self.assertIn("OUTPUT TRUNCATED", result.stdout)
        self.assertEqual("token=[REDACTED]", result.stderr)

    async def test_network_is_denied_by_default_and_requires_configured_allowlist(self):
        runner, client = self.runner()
        await self.run_command(runner)
        self.assertEqual((), client.requests[0].network_allowed_hosts)

        with self.assertRaisesRegex(ToolExecutionError, "VERCEL_SANDBOX_NETWORK_NOT_APPROVED"):
            await self.run_command(runner, network=True, approved=True)

        allowed = settings(
            network_policy="allowlist",
            network_allowlist=("pypi.org", "files.pythonhosted.org"),
        )
        runner, client = self.runner(configured=allowed)
        await self.run_command(runner, network=True, approved=True)
        self.assertEqual(("pypi.org", "files.pythonhosted.org"), client.requests[0].network_allowed_hosts)

    async def test_resource_settings_are_bounded(self):
        environment = {
            "VERCEL_TOKEN": "test-token",
            "VERCEL_TEAM_ID": "team-test",
            "VERCEL_PROJECT_ID": "project-test",
            "NIE_ENGINEERING_SANDBOX_VCPUS": "1",
            "NIE_ENGINEERING_SANDBOX_MEMORY_MB": "2048",
        }
        with patch.dict(os.environ, environment, clear=False):
            configured = VercelSandboxSettings.from_environment()
        self.assertEqual(1, configured.sandbox_vcpus)
        self.assertEqual(2048, configured.sandbox_memory_mb)

        environment["NIE_ENGINEERING_SANDBOX_MEMORY_MB"] = "512"
        with patch.dict(os.environ, environment, clear=False):
            with self.assertRaisesRegex(ToolExecutionError, "NIE_ENGINEERING_SANDBOX_MEMORY_MB_INVALID"):
                VercelSandboxSettings.from_environment()

        environment["NIE_ENGINEERING_SANDBOX_VCPUS"] = "2"
        environment["NIE_ENGINEERING_SANDBOX_MEMORY_MB"] = "2048"
        with patch.dict(os.environ, environment, clear=False):
            with self.assertRaisesRegex(ToolExecutionError, "VERCEL_SANDBOX_RESOURCE_RATIO_INVALID"):
                VercelSandboxSettings.from_environment()

    async def test_invalid_network_hosts_and_missing_credentials_fail_closed(self):
        for host in ("*", "localhost", "169.254.169.254", "metadata.google.internal"):
            with (
                self.subTest(host=host),
                patch.dict(
                    os.environ,
                    {
                        "VERCEL_TOKEN": "token",
                        "VERCEL_TEAM_ID": "team",
                        "VERCEL_PROJECT_ID": "project",
                        "NIE_ENGINEERING_NETWORK_ALLOWLIST": host,
                        "NIE_ENGINEERING_NETWORK_POLICY": "allowlist",
                    },
                    clear=False,
                ),
            ):
                with self.assertRaises(ToolExecutionError):
                    VercelSandboxSettings.from_environment()

        with patch.dict(os.environ, {"VERCEL_TOKEN": "", "VERCEL_TEAM_ID": "", "VERCEL_PROJECT_ID": ""}):
            unavailable = build_command_runner("vercel_sandbox")
        self.assertIsInstance(unavailable, UnavailableCommandRunner)
        with self.assertRaisesRegex(ToolExecutionError, "VERCEL_SANDBOX_CREDENTIALS_MISSING"):
            await unavailable.run(
                "tex_missing",
                str(self.root),
                CommandInput(argv=["pwd"]),
                PolicyDecision("READ_ONLY", True),
            )

    async def test_provider_quota_auth_termination_and_generic_errors_are_classified(self):
        cases = [
            (FakeProviderError(status_code=429), "VERCEL_SANDBOX_QUOTA_EXCEEDED"),
            (FakeProviderError(status_code=401), "VERCEL_SANDBOX_AUTHENTICATION_FAILED"),
            (type("SandboxTerminalStateError", (Exception,), {})(), "VERCEL_SANDBOX_TERMINATED"),
            (FakeProviderError(status_code=500), "VERCEL_SANDBOX_PROVIDER_ERROR"),
        ]
        for error, expected in cases:
            with self.subTest(expected=expected):
                runner, _ = self.runner(client=FakeSandboxClient(error=error))
                with self.assertRaisesRegex(ToolExecutionError, expected):
                    await self.run_command(runner)

    async def test_command_timeout_and_cleanup_after_execution_error(self):
        session = FakeSandboxSession(execute_error=asyncio.TimeoutError())
        runner, _ = self.runner(session)
        with self.assertRaises(ToolExecutionError) as captured:
            await self.run_command(runner)
        self.assertEqual("TIMED_OUT", captured.exception.status)
        self.assertEqual("COMMAND_TIMEOUT", captured.exception.code)
        self.assertTrue(session.closed)

        session = FakeSandboxSession(execute_error=FakeProviderError(status_code=500))
        runner, _ = self.runner(session)
        with self.assertRaisesRegex(ToolExecutionError, "VERCEL_SANDBOX_PROVIDER_ERROR"):
            await self.run_command(runner)
        self.assertTrue(session.closed)

    async def test_cancellation_stops_sandbox_and_has_one_cancelled_result(self):
        session = FakeSandboxSession()
        session.wait_for_cancel = True
        runner, _ = self.runner(session)
        task = asyncio.create_task(self.run_command(runner))
        for _ in range(100):
            if session.executed:
                break
            await asyncio.sleep(0.01)
        self.assertTrue(await runner.cancel("tex_test"))
        with self.assertRaises(ToolExecutionError) as captured:
            await asyncio.wait_for(task, timeout=2)
        self.assertEqual("CANCELLED", captured.exception.status)
        self.assertTrue(session.cancelled)
        self.assertTrue(session.closed)

    async def test_sync_rejects_traversal_oversize_secret_and_revision_conflict(self):
        invalid_results = [
            ({"../escape.txt": b"no"}, "SANDBOX_RETURNED_PATH_ESCAPE"),
            ({"large.txt": b"x" * 1025}, "SANDBOX_RETURNED_FILE_TOO_LARGE"),
            ({".env": b"secret"}, "SANDBOX_RETURNED_SECRET_FILE"),
        ]
        for returned, expected in invalid_results:
            with self.subTest(expected=expected):
                session = FakeSandboxSession(returned=returned)
                runner, _ = self.runner(session)
                with self.assertRaises(ToolExecutionError) as captured:
                    await self.run_command(runner)
                self.assertEqual("SYNC_FAILED", captured.exception.status)
                self.assertEqual(expected, captured.exception.code)
                self.assertTrue(session.closed)

        (self.root / "initial.txt").write_text("initial", encoding="utf-8")
        session = FakeSandboxSession(
            returned={"initial.txt": b"sandbox"},
            mutate=lambda: (self.root / "initial.txt").write_text("newer", encoding="utf-8"),
        )
        runner, _ = self.runner(session)
        with self.assertRaises(ToolExecutionError) as captured:
            await self.run_command(runner)
        self.assertEqual("WORKSPACE_SYNC_CONFLICT", captured.exception.code)
        self.assertEqual("newer", (self.root / "initial.txt").read_text(encoding="utf-8"))

    async def test_official_session_adapter_rejects_symlink_and_skips_dependency_trees(self):
        class FakeApiSession:
            async def __aexit__(self, *_args):
                return None

        class FakeFileSystem:
            def __init__(self, symlink=False):
                self.symlink = symlink
                self.read_paths = []

            async def listdir(self, path):
                if path.endswith("node_modules"):
                    raise AssertionError("ignored dependency tree must not be traversed")
                entries = [
                    SimpleNamespace(path="safe.txt", kind="file"),
                    SimpleNamespace(path="node_modules", kind="directory"),
                ]
                if self.symlink:
                    entries.append(SimpleNamespace(path="escape", kind="symlink"))
                return entries

            async def read_bytes(self, path):
                self.read_paths.append(path)
                return b"safe"

        request = SandboxCreateRequest(60, 5, "image", None, (), 1024, 4096, 20)
        file_system = FakeFileSystem()
        box = SimpleNamespace(name="sbx_test", fs=file_system)
        adapter = VercelPythonSandboxSession(box, FakeApiSession(), request)
        files = await adapter.download()
        self.assertEqual({"safe.txt": b"safe"}, files)
        self.assertEqual(1, len(file_system.read_paths))

        symlink_box = SimpleNamespace(name="sbx_test", fs=FakeFileSystem(symlink=True))
        adapter = VercelPythonSandboxSession(symlink_box, FakeApiSession(), request)
        with self.assertRaisesRegex(Exception, "SANDBOX_RETURNED_SYMLINK"):
            await adapter.download()

    async def test_official_session_adapter_stops_then_destroys_once(self):
        calls = []

        class FakeApiSession:
            async def __aexit__(self, *_args):
                calls.append("session_closed")
                return None

        box = SimpleNamespace(
            name="sbx_test",
            stop=AsyncMock(side_effect=lambda: calls.append("stopped")),
            destroy=AsyncMock(side_effect=lambda: calls.append("destroyed")),
        )
        request = SandboxCreateRequest(60, 5, "image", None, (), 1024, 4096, 20)
        adapter = VercelPythonSandboxSession(box, FakeApiSession(), request)

        await adapter.close()
        await adapter.close()

        self.assertEqual(["stopped", "destroyed", "session_closed"], calls)
        box.stop.assert_awaited_once()
        box.destroy.assert_awaited_once()

    async def test_nonzero_exit_preserves_evidence_and_cleanup(self):
        session = FakeSandboxSession(result=SandboxCommandResult("partial", "failed", 7))
        runner, _ = self.runner(session)
        with self.assertRaises(ToolExecutionError) as captured:
            await self.run_command(runner)
        self.assertEqual("COMMAND_EXIT_NONZERO", captured.exception.code)
        self.assertEqual(7, captured.exception.exit_code)
        self.assertIsNotNone(captured.exception.result)
        self.assertTrue(session.closed)

    async def test_tool_service_persists_evidence_and_exactly_one_terminal_event(self):
        session = FakeSandboxSession(returned={"created.txt": b"created"})
        runner, _ = self.runner(session)
        repository = MemoryAuditRepository()
        service = ToolExecutionService(repository, runner)
        workspace = SimpleNamespace(
            workspace_id="ewk_test",
            owner_id="owner-test",
            root_path=str(self.root),
        )
        result = await service.execute(
            workspace=workspace,
            owner_id="owner-test",
            tool_name="command.run",
            arguments={"argv": ["python", "health_check.py"]},
            conversation_id="conversation-test",
            correlation_id="correlation-test",
        )
        events = repository.events[result.execution_id]
        terminal = [event.event_type for event in events if event.event_type in {"tool.completed", "tool.failed"}]
        self.assertTrue(result.evidence_verified)
        self.assertEqual(["tool.completed"], terminal)
        self.assertEqual("vercel_sandbox", repository.executions[result.execution_id].provider_metadata["provider"])
        self.assertEqual(1, len(repository.file_changes))

    async def test_request_cancellation_is_persisted_and_never_reported_successful(self):
        session = FakeSandboxSession()
        session.wait_for_cancel = True
        runner, _ = self.runner(session)
        repository = MemoryAuditRepository()
        service = ToolExecutionService(repository, runner)
        workspace = SimpleNamespace(
            workspace_id="ewk_cancelled",
            owner_id="owner-test",
            root_path=str(self.root),
        )
        task = asyncio.create_task(
            service.execute(
                workspace=workspace,
                owner_id="owner-test",
                tool_name="command.run",
                arguments={"argv": ["python", "health_check.py"]},
                conversation_id="conversation-test",
                correlation_id="correlation-cancelled",
            )
        )
        for _ in range(100):
            if session.executed:
                break
            await asyncio.sleep(0.01)
        task.cancel()
        result = await asyncio.wait_for(task, timeout=2)
        terminal = [
            event.event_type
            for event in repository.events[result.execution_id]
            if event.event_type in {"tool.completed", "tool.failed"}
        ]
        self.assertEqual("CANCELLED", result.status)
        self.assertFalse(result.evidence_verified)
        self.assertEqual(["tool.failed"], terminal)
        self.assertTrue(session.closed)


@unittest.skipUnless(
    os.getenv("NIE_RUN_VERCEL_SANDBOX_SMOKE", "").strip().upper() == "YES"
    and all(os.getenv(name) for name in ("VERCEL_TOKEN", "VERCEL_TEAM_ID", "VERCEL_PROJECT_ID")),
    "real Vercel Sandbox smoke test requires explicit opt-in and server-side credentials",
)
class RealVercelSandboxSmokeTest(unittest.IsolatedAsyncioTestCase):
    async def test_create_execute_sync_and_destroy_one_real_sandbox(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "health_check.py").write_text("print('HEALTH_CHECK_OK')\n", encoding="utf-8")
            runner = VercelSandboxRunner()
            result = await runner.run(
                "tex_real_smoke",
                str(root),
                CommandInput(argv=["python", "health_check.py"], timeout_seconds=30),
                PolicyDecision("PROJECT_EXECUTION", True),
            )
            self.assertEqual("HEALTH_CHECK_OK", result.stdout.strip())
            self.assertEqual(0, result.exit_code)


if __name__ == "__main__":
    unittest.main()
