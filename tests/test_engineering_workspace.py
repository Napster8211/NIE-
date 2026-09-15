import asyncio
import os
import re
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import ValidationError
from starlette.requests import Request

from app.api.memory import resolve_memory_owner
from app.schemas.director_auth import DirectorPrincipal
from app.schemas.engineering_workspace import CommandInput, ToolInvocation, WorkspaceCreate
from app.schemas.memory_schemas import ConversationCreate
from app.services.director_auth_service import VerifiedOwnerIdentity
from app.services.engineering_authorization import (
    EngineeringAuthorizationError,
    authorize_engineering_identity,
    require_engineering_access,
    require_engineering_mutation,
)
from app.services.engineering_chat_service import (
    EngineeringChatError,
    EngineeringChatService,
    EngineeringIntentRouter,
)
from app.services.engineering_execution_service import (
    DockerCommandRunner,
    LocalDevelopmentCommandRunner,
    PolicyDecision,
    ToolExecutionService,
    ToolPolicyEngine,
)
from app.services.engineering_process_service import ProcessManager
from app.services.engineering_workspace_service import (
    FileOperationResult,
    FileSystemTool,
    WorkspaceError,
    WorkspaceManager,
    WorkspacePathResolver,
)
from app.services.stream_events import sse_event


def now():
    return datetime.now(timezone.utc)


class MemoryEngineeringRepository:
    def __init__(self):
        self.workspaces = {}
        self.links = {}
        self.executions = {}
        self.events = {}
        self.file_changes = []
        self.conversation_owners = {}

    async def conversation_owned_by_user(self, conversation_id, owner_id):
        return self.conversation_owners.get(conversation_id) == owner_id

    async def create_workspace(self, record, conversation_id=None):
        timestamp = now()
        record.status = record.status or "ACTIVE"
        record.created_at = record.created_at or timestamp
        record.updated_at = record.updated_at or timestamp
        record.last_activity_at = record.last_activity_at or timestamp
        record.metadata_json = record.metadata_json or {}
        self.workspaces[record.workspace_id] = record
        if conversation_id:
            self.links[(record.owner_id, conversation_id)] = record.workspace_id
        return record

    async def get_workspace(self, workspace_id, owner_id):
        record = self.workspaces.get(workspace_id)
        return record if record is not None and record.owner_id == owner_id else None

    async def list_workspaces(self, owner_id, include_archived=False):
        return [
            item
            for item in self.workspaces.values()
            if item.owner_id == owner_id and (include_archived or item.status != "ARCHIVED")
        ]

    async def attach_conversation(self, workspace_id, conversation_id, owner_id):
        key = (owner_id, conversation_id)
        existing = self.links.get(key)
        if existing and existing != workspace_id:
            raise ValueError("CONVERSATION_ALREADY_ATTACHED")
        self.links[key] = workspace_id
        return SimpleNamespace(workspace_id=workspace_id, conversation_id=conversation_id, owner_id=owner_id)

    async def conversation_ids(self, workspace_id, owner_id):
        return [
            conversation
            for (owner, conversation), item in self.links.items()
            if owner == owner_id and item == workspace_id
        ]

    async def update_workspace(self, workspace):
        workspace.updated_at = now()
        workspace.last_activity_at = now()
        return workspace

    async def begin_execution(self, execution):
        execution.started_at = execution.started_at or now()
        execution.status = execution.status or "RUNNING"
        execution.sanitized_arguments = execution.sanitized_arguments or {}
        execution.changed_files = execution.changed_files or []
        execution.stdout = execution.stdout or ""
        execution.stderr = execution.stderr or ""
        self.executions[execution.execution_id] = execution
        return execution

    async def finish_execution(self, execution, **values):
        for key, value in values.items():
            setattr(execution, key, list(value) if key == "changed_files" else value)
        execution.finished_at = now()
        return execution

    async def add_event(self, execution_id, event_type, payload):
        items = self.events.setdefault(execution_id, [])
        event = SimpleNamespace(sequence=len(items) + 1, event_type=event_type, payload=payload)
        items.append(event)
        return event

    async def add_file_change(self, **values):
        self.file_changes.append(values)
        return SimpleNamespace(**values)


class EngineeringWorkspaceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.environment = patch.dict(
            os.environ,
            {
                "NIE_ENV": "test",
                "NIE_ENGINEERING_WORKSPACE_ROOT": self.temp.name,
                "NIE_ENGINEERING_RUNNER": "local",
                "NIE_ENGINEERING_MAX_FILE_BYTES": "1000000",
                "NIE_ENGINEERING_MAX_WORKSPACE_BYTES": "10000000",
            },
        )
        self.environment.start()
        self.repository = MemoryEngineeringRepository()
        self.repository.conversation_owners["conversation-a"] = "owner-a"
        self.manager = WorkspaceManager(self.repository)
        self.workspace = await self.manager.create(
            "owner-a",
            WorkspaceCreate(name="napstertec-capability-test", conversation_id="conversation-a"),
        )
        self.record = await self.manager.require(self.workspace.workspace_id, "owner-a")
        self.runner = LocalDevelopmentCommandRunner()
        self.execution = ToolExecutionService(self.repository, self.runner)

    async def asyncTearDown(self):
        self.environment.stop()
        self.temp.cleanup()

    async def execute(self, tool_name, arguments, owner_id="owner-a", user_approval_verified=False):
        return await self.execution.execute(
            workspace=self.record,
            owner_id=owner_id,
            tool_name=tool_name,
            arguments=arguments,
            conversation_id="conversation-a",
            correlation_id="cor_test",
            user_approval_verified=user_approval_verified,
        )

    async def test_capability_workflow_persists_files_and_execution_evidence(self):
        readme = await self.execute(
            "filesystem.create",
            {
                "path": "README.md",
                "content": "# NapsterTec capability test\n",
            },
        )
        program = await self.execute(
            "filesystem.create",
            {
                "path": "health_check.py",
                "content": "print('HEALTH_CHECK_OK')\n",
            },
        )
        command = await self.execute("command.run", {"argv": ["python", "health_check.py"]})

        self.assertTrue(readme.success)
        self.assertTrue(program.success)
        self.assertTrue(command.success)
        self.assertEqual(0, command.exit_code)
        self.assertEqual("HEALTH_CHECK_OK", command.stdout.strip())
        self.assertTrue(command.execution_id.startswith("tex_"))

        reopened = await WorkspaceManager(self.repository).require(self.workspace.workspace_id, "owner-a")
        content = FileSystemTool(reopened.root_path).read_file("README.md")
        self.assertIn("NapsterTec capability test", content.data["content"])
        self.assertEqual(self.workspace.workspace_id, self.repository.links[("owner-a", "conversation-a")])
        self.assertEqual(
            ["tool.started", "tool.stdout", "tool.completed"],
            [event.event_type for event in self.repository.events[command.execution_id]],
        )

    async def test_workspace_owner_isolation_and_unique_conversation_attachment(self):
        with self.assertRaisesRegex(WorkspaceError, "WORKSPACE_NOT_FOUND"):
            await self.manager.require(self.workspace.workspace_id, "owner-b")
        other = await self.manager.create("owner-b", WorkspaceCreate(name="other"))
        with self.assertRaisesRegex(WorkspaceError, "CONVERSATION_NOT_FOUND"):
            await self.manager.attach(other.workspace_id, "conversation-a", "owner-b")
        self.repository.conversation_owners["conversation-b"] = "owner-b"
        await self.manager.attach(other.workspace_id, "conversation-b", "owner-b")
        self.assertEqual(other.workspace_id, self.repository.links[("owner-b", "conversation-b")])
        with self.assertRaisesRegex(Exception, "WORKSPACE_OWNERSHIP_REJECTED"):
            await self.execute("filesystem.list", {"path": ""}, owner_id="owner-b")

    async def test_file_operations_reject_traversal_absolute_secret_and_symlink_escape(self):
        root = Path(self.record.root_path)
        resolver = WorkspacePathResolver(str(root))
        with self.assertRaisesRegex(WorkspaceError, "PATH_TRAVERSAL_REJECTED"):
            resolver.resolve("../outside.txt")
        with self.assertRaisesRegex(WorkspaceError, "ABSOLUTE_PATH_REJECTED"):
            resolver.resolve(str(root / "absolute.txt"))
        with self.assertRaisesRegex(WorkspaceError, "SECRET_FILE_ACCESS_REJECTED"):
            resolver.resolve(".env.production")

        outside = Path(self.temp.name) / "outside"
        outside.mkdir()
        link = root / "escape"
        try:
            link.symlink_to(outside, target_is_directory=True)
        except OSError as error:
            # Windows CI can deny symlink creation without Developer Mode. Simulate
            # the canonical target so the confinement decision remains covered.
            escaped_file = root / "escape" / "host.txt"
            real_resolve = Path.resolve

            def resolve_with_escape(path, strict=False):
                if path == escaped_file:
                    return outside / "host.txt"
                return real_resolve(path, strict=strict)

            with patch.object(Path, "resolve", resolve_with_escape):
                with self.assertRaisesRegex(WorkspaceError, "PATH_ESCAPE_REJECTED"):
                    resolver.resolve("escape/host.txt")
            self.assertIsInstance(error, OSError)
            return
        with self.assertRaisesRegex(WorkspaceError, "PATH_ESCAPE_REJECTED|SYMLINK_ESCAPE_REJECTED"):
            resolver.resolve("escape/host.txt")

    async def test_patch_search_rename_and_recoverable_delete(self):
        await self.execute("filesystem.create", {"path": "src/app.py", "content": "value = 'old'\n"})
        patched = await self.execute(
            "filesystem.patch",
            {
                "path": "src/app.py",
                "replacements": [{"old": "old", "new": "new", "expected_occurrences": 1}],
            },
        )
        search = await self.execute("search.content", {"query": "value", "path": "src"})
        renamed = await self.execute("filesystem.rename", {"source": "src/app.py", "destination": "src/main.py"})
        approval = await self.execute("filesystem.delete", {"path": "src/main.py"})
        forged = await self.execute("filesystem.delete", {"path": "src/main.py", "approval_granted": True})
        deleted = await self.execute(
            "filesystem.delete",
            {"path": "src/main.py", "approval_granted": True},
            user_approval_verified=True,
        )

        self.assertTrue(patched.success)
        self.assertEqual("src/app.py", search.data["matches"][0]["path"])
        self.assertTrue(renamed.success)
        self.assertEqual("APPROVAL_REQUIRED", approval.status)
        self.assertEqual("APPROVAL_REQUIRED", forged.status)
        self.assertFalse((Path(self.record.root_path) / "src/main.py").exists())
        self.assertTrue(deleted.data["recoverable"])

    async def test_policy_rejects_forbidden_shell_paths_inline_code_and_unapproved_network(self):
        policy = ToolPolicyEngine()
        cases = [
            CommandInput(argv=["powershell", "Get-ChildItem"]),
            CommandInput(argv=["python", "-c", "print('unsafe')"]),
            CommandInput(argv=["git", "push"]),
            CommandInput(argv=["rg", "--pre", "cmd", "pattern"]),
            CommandInput(argv=["python", "../outside.py"]),
            CommandInput(argv=["npm", "install"]),
        ]
        for command in cases:
            with self.subTest(command=command.argv):
                self.assertFalse(policy.classify(command).permitted)

    async def test_docker_runner_builds_a_fail_closed_isolated_invocation(self):
        runner = DockerCommandRunner()
        captured = {}

        async def capture(_execution_id, _root_path, command, _policy):
            captured["argv"] = command.argv
            result = FileOperationResult({"classification": "PROJECT_EXECUTION"})
            result.stdout = "HEALTH_CHECK_OK\n"
            result.stderr = ""
            result.exit_code = 0
            return result

        policy = PolicyDecision("PROJECT_EXECUTION", True)
        with (
            patch(
                "app.services.engineering_execution_service.shutil.which",
                return_value="C:/Program Files/Docker/docker.exe",
            ),
            patch.object(
                runner,
                "_run_docker_host_command",
                new=AsyncMock(side_effect=capture),
            ),
        ):
            result = await runner.run(
                "tex_docker_security",
                self.record.root_path,
                CommandInput(argv=["python", "health_check.py"]),
                policy,
            )

        argv = captured["argv"]
        self.assertEqual("HEALTH_CHECK_OK", result.stdout.strip())
        self.assertIn("--read-only", argv)
        self.assertEqual("none", argv[argv.index("--network") + 1])
        self.assertEqual("65532:65532", argv[argv.index("--user") + 1])
        self.assertEqual("ALL", argv[argv.index("--cap-drop") + 1])
        self.assertIn("no-new-privileges", argv)
        self.assertIn("/tmp:rw,noexec,nosuid,size=128m", argv)
        self.assertEqual(
            "type=bind,source=" + str(Path(self.record.root_path).resolve()) + ",target=/workspace",
            argv[argv.index("--mount") + 1],
        )
        self.assertNotIn("/var/run/docker.sock", " ".join(argv))
        self.assertEqual(["python", "health_check.py"], argv[-2:])

    async def test_docker_runner_preserves_explicit_network_policy_only(self):
        runner = DockerCommandRunner()
        captured = {}

        async def capture(_execution_id, _root_path, command, _policy):
            captured["argv"] = command.argv
            return FileOperationResult({})

        policy = PolicyDecision("DEPENDENCY_INSTALL", True, approval_required=True, network_allowed=True)
        command = CommandInput(
            argv=["python", "-m", "pip", "install", "example"],
            approval_granted=True,
            allow_network=True,
        )
        with (
            patch("app.services.engineering_execution_service.shutil.which", return_value="docker"),
            patch.object(
                runner,
                "_run_docker_host_command",
                new=AsyncMock(side_effect=capture),
            ),
        ):
            await runner.run("tex_network", self.record.root_path, command, policy)
        self.assertEqual("bridge", captured["argv"][captured["argv"].index("--network") + 1])

    async def test_process_shutdown_cleans_abandoned_local_processes(self):
        class FakeProcess:
            returncode = None

            def terminate(self):
                self.returncode = 0

            async def wait(self):
                return self.returncode

        ProcessManager._active = {"wpr_abandoned": FakeProcess()}
        ProcessManager._log_tasks = {}
        ProcessManager._logs = {"wpr_abandoned": "safe output"}
        self.assertEqual(1, await ProcessManager.shutdown_active())
        self.assertEqual({}, ProcessManager._active)
        self.assertEqual({}, ProcessManager._logs)

    async def test_command_timeout_output_truncation_and_environment_secret_isolation(self):
        tool = FileSystemTool(self.record.root_path)
        tool.create_file("timeout.py", "import time\ntime.sleep(2)\n")
        tool.create_file("large.py", "print('x' * 5000)\n")
        tool.create_file("environment.py", "import os\nprint('LEAK' if os.getenv('NIE_OWNER_KEY') else 'ISOLATED')\n")

        timed_out = await self.execute("command.run", {"argv": ["python", "timeout.py"], "timeout_seconds": 1})
        large = await self.execute("command.run", {"argv": ["python", "large.py"], "max_output_bytes": 1000})
        with patch.dict(os.environ, {"NIE_OWNER_KEY": "never-pass-this-secret-to-a-workspace"}):
            isolated = await self.execute("command.run", {"argv": ["python", "environment.py"]})

        self.assertEqual("TIMED_OUT", timed_out.status)
        self.assertIn("OUTPUT TRUNCATED", large.stdout)
        self.assertNotIn("never-pass-this-secret", large.stdout + isolated.stdout)
        self.assertEqual("ISOLATED", isolated.stdout.strip())

    async def test_command_cancellation_has_one_terminal_audit_event(self):
        FileSystemTool(self.record.root_path).create_file("wait.py", "import time\ntime.sleep(10)\n")
        started = asyncio.Event()
        execution_id = None

        async def on_started(payload):
            nonlocal execution_id
            execution_id = payload["execution_id"]
            started.set()

        task = asyncio.create_task(
            self.execution.execute(
                workspace=self.record,
                owner_id="owner-a",
                tool_name="command.run",
                arguments={"argv": ["python", "wait.py"]},
                conversation_id="conversation-a",
                correlation_id="cor_cancel",
                on_started=on_started,
            )
        )
        await asyncio.wait_for(started.wait(), timeout=3)
        self.assertTrue(await self.execution.cancel(execution_id))
        result = await asyncio.wait_for(task, timeout=5)
        self.assertEqual("CANCELLED", result.status)
        terminals = [
            event.event_type
            for event in self.repository.events[execution_id]
            if event.event_type in {"tool.completed", "tool.failed"}
        ]
        self.assertEqual(["tool.failed"], terminals)

    async def test_structured_validation_and_audit_fail_closed(self):
        invalid = await self.execute("filesystem.create", {"path": "file.txt", "content": "x", "unexpected": True})
        unknown = await self.execute("unknown.tool", {})
        self.assertEqual("TOOL_ARGUMENT_VALIDATION_FAILED", invalid.error_type)
        self.assertEqual("UNKNOWN_TOOL", unknown.error_type)
        self.assertFalse(invalid.success)
        self.assertFalse(unknown.success)

    async def test_engineering_intent_does_not_capture_ordinary_explanations(self):
        self.assertTrue(EngineeringIntentRouter.classify("Run the tests in my project"))
        self.assertTrue(EngineeringIntentRouter.classify("Create a workspace file"))
        self.assertFalse(EngineeringIntentRouter.classify("Explain how unit tests work"))
        self.assertFalse(EngineeringIntentRouter.classify("How are you today?"))

    async def test_chat_loop_requires_execution_evidence_and_blocks_repeated_calls(self):
        replies = [
            '{"type":"tool_calls","calls":[{"call_id":"one","name":"filesystem.create","arguments":{"path":"evidence.txt","content":"ok"}}]}',
            '{"type":"tool_calls","calls":[{"call_id":"two","name":"filesystem.create","arguments":{"path":"evidence.txt","content":"ok"}}]}',
        ]

        async def repeated_model(_):
            return replies.pop(0), {"provider": "fake"}

        service = EngineeringChatService(self.execution, repeated_model)
        with self.assertRaisesRegex(EngineeringChatError, "REPEATED_IDENTICAL_TOOL_CALL"):
            async for _ in service.run(
                workspace=self.record,
                owner_id="owner-a",
                prompt="Create evidence",
                conversation_id="conversation-a",
                max_iterations=3,
                correlation_id="cor_repeat",
            ):
                pass

        async def unsupported_claim(_):
            return '{"type":"final","content":"I created the file.","evidence_execution_ids":[]}', {"provider": "fake"}

        with self.assertRaisesRegex(EngineeringChatError, "ENGINEERING_SUCCESS_CLAIM_UNVERIFIED"):
            async for _ in EngineeringChatService(self.execution, unsupported_claim).run(
                workspace=self.record,
                owner_id="owner-a",
                prompt="Create a file",
                conversation_id="conversation-a",
                max_iterations=1,
            ):
                pass

    async def test_chat_loop_completes_once_with_verified_execution_reference(self):
        calls = 0

        async def model(prompt):
            nonlocal calls
            calls += 1
            if calls == 1:
                return (
                    '{"type":"tool_calls","calls":[{"call_id":"one","name":"filesystem.create","arguments":{"path":"done.txt","content":"done"}}]}',
                    {"provider": "fake"},
                )
            execution_id = re.search(r'"execution_id":"(tex_[a-f0-9]+)"', prompt).group(1)
            return (
                '{"type":"final","content":"Created done.txt with verified evidence.","evidence_execution_ids":["'
                + execution_id
                + '"]}',
                {"provider": "fake"},
            )

        events = []
        async for event in EngineeringChatService(self.execution, model).run(
            workspace=self.record,
            owner_id="owner-a",
            prompt="Create done.txt",
            conversation_id="conversation-a",
            max_iterations=3,
            correlation_id="cor_complete",
        ):
            events.append(event)
        terminals = [event for event in events if event[0] in {"message.completed", "message.failed"}]
        self.assertEqual(1, len(terminals))
        self.assertEqual("message.completed", terminals[0][0])
        self.assertEqual(1, len(terminals[0][1]["evidence_execution_ids"]))

    async def test_sse_framing_is_typed_and_terminates_blocks(self):
        block = sse_event("message.completed", {"correlation_id": "cor_1", "ok": True}, "cor_1")
        self.assertIn("event: message.completed\n", block)
        self.assertIn('data: {"correlation_id":"cor_1","ok":true}\n\n', block)


class EngineeringAuthorizationTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def request(*, cookie=None, csrf=None):
        headers = [(b"origin", b"http://localhost:5173")]
        if cookie:
            headers.append((b"cookie", cookie.encode("ascii")))
        if csrf:
            headers.append((b"x-csrf-token", csrf.encode("ascii")))
        return Request(
            {
                "type": "http",
                "method": "POST",
                "path": "/api/v1/engineering",
                "headers": headers,
            }
        )

    async def test_authenticated_standard_chat_user_uses_server_verified_uid(self):
        settings = {
            "NIE_ENV": "test",
            "NIE_TRUSTED_FRONTEND_ORIGINS": "http://localhost:5173",
            "NIE_ENGINEERING_MODE_ENABLED": "true",
            "NIE_ENGINEERING_OWNER_ONLY": "false",
            "FIREBASE_PROJECT_ID": "test-project",
        }
        credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials="firebase-assertion")
        verified = VerifiedOwnerIdentity(uid="standard-user", email="user@example.test")
        with (
            patch.dict(os.environ, settings, clear=True),
            patch(
                "app.services.engineering_authorization.verify_firebase_identity",
                new=AsyncMock(return_value=verified),
            ),
        ):
            principal = await require_engineering_access(self.request(), credentials, AsyncMock())
        self.assertEqual("firebase:standard-user", principal.user_id)
        self.assertEqual("firebase_bearer", principal.auth_method)
        self.assertFalse(principal.is_owner)

    async def test_authenticated_memory_owner_uses_verified_uid(self):
        credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials="firebase-assertion")
        verified = VerifiedOwnerIdentity(uid="standard-user", email="user@example.test")
        with (
            patch.dict(
                os.environ,
                {
                    "NIE_ENV": "test",
                    "NIE_TRUSTED_FRONTEND_ORIGINS": "http://localhost:5173",
                    "FIREBASE_PROJECT_ID": "test-project",
                },
                clear=True,
            ),
            patch("app.api.memory.verify_firebase_identity", new=AsyncMock(return_value=verified)),
        ):
            owner_id = await resolve_memory_owner(self.request(), credentials)
        self.assertEqual("firebase:standard-user", owner_id)

    async def test_anonymous_memory_and_engineering_access_are_rejected(self):
        with self.assertRaises(HTTPException) as memory_error:
            await resolve_memory_owner(self.request(), None)
        self.assertEqual(401, memory_error.exception.status_code)
        self.assertEqual("CHAT_AUTH_REQUIRED", memory_error.exception.detail)
        with patch.dict(
            os.environ,
            {
                "NIE_ENV": "test",
                "NIE_TRUSTED_FRONTEND_ORIGINS": "http://localhost:5173",
                "NIE_ENGINEERING_MODE_ENABLED": "true",
                "NIE_ENGINEERING_OWNER_ONLY": "false",
            },
            clear=True,
        ):
            with self.assertRaises(HTTPException):
                await require_engineering_access(self.request(), None, AsyncMock())

    async def test_anonymous_engineering_access_is_rejected(self):
        settings = {
            "NIE_ENV": "test",
            "NIE_TRUSTED_FRONTEND_ORIGINS": "http://localhost:5173",
            "NIE_ENGINEERING_MODE_ENABLED": "true",
            "NIE_ENGINEERING_OWNER_ONLY": "false",
        }
        with patch.dict(os.environ, settings, clear=True):
            with self.assertRaises(HTTPException) as caught:
                await require_engineering_access(self.request(), None, AsyncMock())
        self.assertEqual(401, caught.exception.status_code)
        self.assertEqual("ENGINEERING_AUTH_REQUIRED", caught.exception.detail)

    async def test_disabled_and_owner_only_feature_flags_fail_closed(self):
        with patch.dict(
            os.environ,
            {
                "NIE_ENV": "production",
                "NIE_ENGINEERING_MODE_ENABLED": "false",
            },
            clear=True,
        ):
            with self.assertRaisesRegex(EngineeringAuthorizationError, "ENGINEERING_MODE_DISABLED"):
                authorize_engineering_identity("owner", None, "firebase_bearer")

        with patch.dict(
            os.environ,
            {
                "NIE_ENV": "test",
                "NIE_ENGINEERING_MODE_ENABLED": "true",
                "NIE_ENGINEERING_OWNER_ONLY": "true",
                "NIE_OWNER_FIREBASE_UIDS": "actual-owner",
            },
            clear=True,
        ):
            with self.assertRaisesRegex(EngineeringAuthorizationError, "ENGINEERING_OWNER_ONLY"):
                authorize_engineering_identity("ordinary-user", None, "firebase_bearer")
            owner = authorize_engineering_identity("actual-owner", None, "firebase_bearer")
        self.assertTrue(owner.is_owner)
        self.assertEqual("firebase:actual-owner", owner.user_id)

    async def test_explicit_standard_user_allowlist_is_enforced(self):
        settings = {
            "NIE_ENV": "test",
            "NIE_ENGINEERING_MODE_ENABLED": "true",
            "NIE_ENGINEERING_OWNER_ONLY": "false",
            "NIE_ENGINEERING_ALLOWED_FIREBASE_UIDS": "allowed-user",
        }
        with patch.dict(os.environ, settings, clear=True):
            allowed = authorize_engineering_identity("allowed-user", None, "firebase_bearer")
            with self.assertRaisesRegex(EngineeringAuthorizationError, "ENGINEERING_USER_NOT_ALLOWED"):
                authorize_engineering_identity("blocked-user", None, "firebase_bearer")
        self.assertEqual("firebase:allowed-user", allowed.user_id)

    async def test_director_session_remains_compatible_and_requires_csrf_for_mutation(self):
        director = SimpleNamespace(validate_session=AsyncMock(), validate_csrf=Mock())
        director.validate_session.return_value = DirectorPrincipal(
            owner_id="firebase:owner-uid",
            owner_uid="owner-uid",
            auth_method="director_session",
            session_id="dss_test",
            csrf_token_hash="hash",
        )
        settings = {
            "NIE_ENV": "test",
            "NIE_TRUSTED_FRONTEND_ORIGINS": "http://localhost:5173",
            "NIE_ENGINEERING_MODE_ENABLED": "true",
            "NIE_ENGINEERING_OWNER_ONLY": "true",
            "NIE_OWNER_FIREBASE_UIDS": "owner-uid",
        }
        request = self.request(cookie="nie_director_session=opaque", csrf="csrf")
        with patch.dict(os.environ, settings, clear=True):
            principal = await require_engineering_access(request, None, director)
            returned = await require_engineering_mutation(request, principal, director)
        self.assertEqual(principal, returned)
        self.assertEqual("director_session", returned.auth_method)
        director.validate_csrf.assert_called_once_with(director.validate_session.return_value, "csrf")

    def test_client_supplied_user_id_is_rejected_by_strict_schema(self):
        with self.assertRaises(ValidationError):
            ToolInvocation.model_validate(
                {
                    "tool_name": "filesystem.list",
                    "arguments": {"path": ""},
                    "user_id": "attacker-selected-user",
                }
            )
        with self.assertRaises(ValidationError):
            ConversationCreate.model_validate({"title": "Untrusted owner", "user_id": "attacker-selected-user"})

    def test_migration_contract_is_idempotent_and_has_operational_constraints(self):
        migration = (
            Path(__file__).resolve().parents[1] / "database" / "migrations" / "001_engineering_workspace.sql"
        ).read_text(encoding="utf-8")
        for table in (
            "engineering_workspaces",
            "workspace_conversations",
            "tool_executions",
            "execution_events",
            "workspace_file_changes",
            "workspace_processes",
        ):
            self.assertIn(f"CREATE TABLE IF NOT EXISTS {table}", migration)
        self.assertIn("ck_engineering_workspace_status", migration)
        self.assertIn("ck_tool_execution_status", migration)
        self.assertIn("ck_workspace_process_port", migration)
        self.assertIn("ON DELETE CASCADE", migration)
        self.assertIn("IF to_regclass('messages') IS NOT NULL", migration)
        self.assertIn("ALTER TABLE messages ADD COLUMN IF NOT EXISTS", migration)
        self.assertGreaterEqual(migration.count("CREATE INDEX IF NOT EXISTS"), 12)


if __name__ == "__main__":
    unittest.main()
