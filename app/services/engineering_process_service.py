"""Bounded local-development process manager behind the engineering runner contract."""

import asyncio
import os
import shutil
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

import httpx

from app.models.engineering_workspace import ToolExecution, WorkspaceProcess
from app.repositories.engineering_workspace_repository import EngineeringWorkspaceRepository
from app.schemas.engineering_workspace import ProcessResponse, ProcessStartInput
from app.services.engineering_execution_service import SecretRedactor, ToolExecutionError
from app.services.engineering_workspace_storage import (
    WorkspaceStorageError,
    build_workspace_storage,
    capture_workspace,
    reconcile_workspace,
)
from app.services.runtime_environment import runtime_environment
from app.services.vercel_sandbox_runner import (
    SandboxCreateRequest,
    SandboxPreviewClient,
    SandboxPreviewState,
    VercelPythonPreviewClient,
    VercelSandboxRunner,
    VercelSandboxSettings,
    mark_vercel_sandbox_capability_verified,
)


class ProcessManager:
    """Owner-only local preview manager; production must use an isolated remote runner."""

    _active: dict[str, asyncio.subprocess.Process] = {}
    _log_tasks: dict[str, asyncio.Task] = {}
    _logs: dict[str, str] = {}
    _lock = asyncio.Lock()

    def __init__(self, repository: EngineeringWorkspaceRepository):
        self.repository = repository

    @staticmethod
    def _validate(request: ProcessStartInput) -> list[str]:
        environment = runtime_environment()
        runner = os.getenv("NIE_ENGINEERING_RUNNER", "local").strip().casefold()
        if environment not in {"development", "test"} or runner != "local":
            raise ToolExecutionError("PROCESS_RUNNER_REQUIRES_ISOLATED_REMOTE_IMPLEMENTATION")
        executable = Path(request.argv[0]).name.casefold()
        args = request.argv[1:]
        allowed = (
            executable in {"npm", "npm.cmd"}
            and len(args) >= 2
            and args[0] == "run"
            and args[1] in {"dev", "start", "preview"}
        ) or (
            executable in {"python", "python3", "python.exe", "py"} and args == ["-m", "http.server", str(request.port)]
        )
        if not allowed:
            raise ToolExecutionError("PROCESS_COMMAND_REJECTED")
        if any(Path(item).is_absolute() or ".." in Path(item).parts for item in args if not item.startswith("-")):
            raise ToolExecutionError("PROCESS_PATH_REJECTED")
        argv = list(request.argv)
        if executable in {"python", "python3", "python.exe", "py"}:
            argv[0] = sys.executable
        else:
            resolved = shutil.which(argv[0])
            if not resolved:
                raise ToolExecutionError("PROCESS_COMMAND_NOT_AVAILABLE")
            argv[0] = resolved
        return argv

    async def start(self, workspace: Any, owner_id: str, request: ProcessStartInput) -> ProcessResponse:
        argv = self._validate(request)
        root = Path(workspace.root_path).resolve()
        cwd = (root / request.cwd).resolve()
        if cwd != root and root not in cwd.parents:
            raise ToolExecutionError("PROCESS_CWD_ESCAPE_REJECTED")
        if not cwd.is_dir():
            raise ToolExecutionError("PROCESS_CWD_NOT_FOUND")
        temp = root / ".nie-tmp"
        temp.mkdir(exist_ok=True)
        async with self._lock:
            if len([item for item in self._active.values() if item.returncode is None]) >= int(
                os.getenv("NIE_ENGINEERING_MAX_PROCESSES", "2")
            ):
                raise ToolExecutionError("PROCESS_LIMIT_REACHED")
            process = await asyncio.create_subprocess_exec(
                *argv,
                cwd=str(cwd),
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env={
                    "PATH": os.environ.get("PATH", ""),
                    "SystemRoot": os.environ.get("SystemRoot", ""),
                    "PORT": str(request.port),
                    "HOME": str(root),
                    "USERPROFILE": str(root),
                    "TMP": str(temp),
                    "TEMP": str(temp),
                    "CI": "1",
                },
            )
            process_id = f"wpr_{uuid.uuid4().hex}"
            self._active[process_id] = process
        record = WorkspaceProcess(
            process_id=process_id,
            workspace_id=workspace.workspace_id,
            owner_id=owner_id,
            sanitized_command=request.argv,
            runner_process_id=str(process.pid),
            permitted_port=request.port,
            status="RUNNING",
            health_status="STARTING",
        )
        try:
            await self.repository.create_process(record)
        except Exception:
            process.terminate()
            await process.wait()
            async with self._lock:
                self._active.pop(process_id, None)
            raise
        self._log_tasks[process_id] = asyncio.create_task(self._capture_logs(process_id, process))
        ready = await self._wait_for_port(process, request.port)
        if ready:
            record.health_status = "READY"
        elif process.returncode is not None:
            record.status = "FAILED"
            record.health_status = "FAILED"
            record.stopped_at = datetime.now(timezone.utc)
        await self.repository.session.commit()
        return self._response(record)

    @staticmethod
    async def _wait_for_port(process: asyncio.subprocess.Process, port: int, timeout_seconds: float = 4.0) -> bool:
        deadline = asyncio.get_running_loop().time() + timeout_seconds
        while asyncio.get_running_loop().time() < deadline:
            if process.returncode is not None:
                return False
            try:
                _, writer = await asyncio.open_connection("127.0.0.1", port)
                writer.close()
                await writer.wait_closed()
                return True
            except OSError:
                await asyncio.sleep(0.2)
        return False

    async def _capture_logs(self, process_id: str, process: asyncio.subprocess.Process) -> None:
        output = bytearray()
        try:
            if process.stdout is None:
                self._logs[process_id] = "PROCESS_OUTPUT_CAPTURE_UNAVAILABLE"
                if process.returncode is None:
                    process.terminate()
                    await process.wait()
                return
            while True:
                chunk = await process.stdout.read(4096)
                if not chunk:
                    break
                if len(output) < 100_000:
                    output.extend(chunk[: 100_000 - len(output)])
            await process.wait()
            self._logs[process_id] = output.decode("utf-8", errors="replace")
        finally:
            async with self._lock:
                self._log_tasks.pop(process_id, None)

    @classmethod
    async def shutdown_active(cls) -> int:
        """Terminate every local preview process during API shutdown.

        Database records are deliberately not synthesized here: the next
        inspection can reconcile an interrupted process, while the operating
        system resources are guaranteed not to survive this API process.
        """

        async with cls._lock:
            active = list(cls._active.items())
            log_tasks = list(cls._log_tasks.values())
        for _, process in active:
            if process.returncode is None:
                process.terminate()
        for _, process in active:
            if process.returncode is not None:
                continue
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
        if log_tasks:
            await asyncio.gather(*log_tasks, return_exceptions=True)
        async with cls._lock:
            count = len(cls._active)
            cls._active.clear()
            cls._log_tasks.clear()
            cls._logs.clear()
        return count

    async def stop(self, workspace_id: str, owner_id: str, process_id: str) -> ProcessResponse:
        record = await self.repository.get_process(process_id, workspace_id, owner_id)
        if record is None:
            raise ToolExecutionError("PROCESS_NOT_FOUND")
        async with self._lock:
            process = self._active.get(process_id)
        if process is None or process.returncode is not None:
            record.status = "STOPPED"
        else:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
            record.status = "STOPPED"
        record.health_status = "STOPPED"
        record.log_excerpt = self._logs.get(process_id, record.log_excerpt or "")
        record.stopped_at = datetime.now(timezone.utc)
        await self.repository.session.commit()
        response = self._response(record)
        async with self._lock:
            self._active.pop(process_id, None)
            self._log_tasks.pop(process_id, None)
            self._logs.pop(process_id, None)
        return response

    async def inspect(self, workspace_id: str, owner_id: str, process_id: str) -> ProcessResponse:
        record = await self.repository.get_process(process_id, workspace_id, owner_id)
        if record is None:
            raise ToolExecutionError("PROCESS_NOT_FOUND")
        async with self._lock:
            process = self._active.get(process_id)
        if process is not None and process.returncode is not None and record.status == "RUNNING":
            record.status = "STOPPED" if process.returncode == 0 else "FAILED"
            record.health_status = "STOPPED"
            record.stopped_at = datetime.now(timezone.utc)
            record.log_excerpt = self._logs.get(process_id, "")
            await self.repository.session.commit()
        elif process is not None and record.health_status != "READY":
            if await self._wait_for_port(process, record.permitted_port, timeout_seconds=0.25):
                record.health_status = "READY"
                await self.repository.session.commit()
        return self._response(record)

    @staticmethod
    def _response(record: WorkspaceProcess) -> ProcessResponse:
        return ProcessResponse(
            process_id=record.process_id,
            workspace_id=record.workspace_id,
            execution_id=getattr(record, "execution_id", None),
            status=record.status,
            port=record.permitted_port,
            health_status=record.health_status,
            log_excerpt=record.log_excerpt or "",
            preview_url=getattr(record, "preview_url", None),
            runner=getattr(record, "runner_provider", None) or "local",
        )


class VercelSandboxProcessManager:
    """Reconnectable remote preview manager backed by Vercel Sandbox."""

    def __init__(
        self,
        repository: EngineeringWorkspaceRepository,
        *,
        client: SandboxPreviewClient | None = None,
        settings: VercelSandboxSettings | None = None,
        storage=None,
        readiness_probe=None,
    ):
        self.repository = repository
        self.settings = settings or VercelSandboxSettings.from_environment()
        self.client = client or VercelPythonPreviewClient(self.settings)
        self.storage = storage or build_workspace_storage(repository)
        self.readiness_probe = readiness_probe or self._probe_url
        self._redactor = SecretRedactor()

    @staticmethod
    def _positive_setting(name: str, default: int, minimum: int, maximum: int) -> int:
        try:
            value = int(os.getenv(name, str(default)))
        except ValueError as exc:
            raise ToolExecutionError(f"{name}_INVALID") from exc
        if value < minimum or value > maximum:
            raise ToolExecutionError(f"{name}_INVALID")
        return value

    @staticmethod
    def _validate(request: ProcessStartInput) -> list[str]:
        executable = PureWindowsPath(request.argv[0]).name.casefold()
        args = list(request.argv[1:])
        is_npm = (
            executable in {"npm", "npm.cmd"}
            and len(args) == 2
            and args[0] == "run"
            and args[1]
            in {
                "dev",
                "start",
                "preview",
            }
        )
        is_python = executable in {"python", "python3", "python.exe", "py"} and args == [
            "-m",
            "http.server",
            str(request.port),
        ]
        if not (is_npm or is_python):
            raise ToolExecutionError("PROCESS_COMMAND_REJECTED")
        for item in args:
            posix = PurePosixPath(item.replace("\\", "/"))
            windows = PureWindowsPath(item)
            if windows.is_absolute() or windows.drive or posix.is_absolute() or ".." in posix.parts:
                raise ToolExecutionError("PROCESS_PATH_REJECTED")
        if is_python:
            return ["python3", "-m", "http.server", str(request.port), "--bind", "0.0.0.0"]
        if args[1] in {"dev", "preview"}:
            return ["npm", "run", args[1], "--", "--host", "0.0.0.0", "--port", str(request.port)]
        return ["npm", "run", "start"]

    @staticmethod
    def _validate_cwd(cwd: str) -> str:
        normalized = cwd.replace("\\", "/").strip("/")
        posix = PurePosixPath(normalized or ".")
        windows = PureWindowsPath(cwd)
        if windows.is_absolute() or windows.drive or posix.is_absolute() or ".." in posix.parts:
            raise ToolExecutionError("PROCESS_CWD_ESCAPE_REJECTED")
        return "" if posix == PurePosixPath(".") else posix.as_posix()

    async def _probe_url(self, url: str) -> bool:
        try:
            async with httpx.AsyncClient(follow_redirects=True, timeout=2.0) as client:
                response = await client.get(url)
            return response.status_code < 500
        except httpx.HTTPError:
            return False

    def _request(self, port: int, timeout_seconds: int) -> SandboxCreateRequest:
        return SandboxCreateRequest(
            execution_time_limit_seconds=min(self.settings.sandbox_timeout_seconds, timeout_seconds + 30),
            synchronization_timeout_seconds=self.settings.synchronization_timeout_seconds,
            image=self.settings.image,
            snapshot_id=self.settings.snapshot_id,
            network_allowed_hosts=(),
            max_file_bytes=self.storage.max_file_bytes,
            max_workspace_bytes=self.storage.max_workspace_bytes,
            max_file_count=self.storage.max_file_count,
            vcpus=self.settings.sandbox_vcpus,
            memory_mb=self.settings.sandbox_memory_mb,
            ports=(port,),
        )

    async def _finish_execution(
        self,
        record: WorkspaceProcess,
        *,
        status: str,
        error_type: str | None,
        stdout: str,
        stderr: str,
        changes: tuple[dict[str, object], ...] = (),
    ) -> None:
        if not record.execution_id:
            return
        execution = await self.repository.get_execution(record.execution_id, record.workspace_id, record.owner_id)
        if execution is None or execution.status != "RUNNING":
            return
        maximum = self._positive_setting("NIE_ENGINEERING_MAX_OUTPUT_BYTES", 100_000, 1_000, 1_000_000)
        stdout = self._redactor.redact(
            stdout.encode("utf-8", errors="replace")[:maximum].decode("utf-8", errors="replace")
        )
        stderr = self._redactor.redact(
            stderr.encode("utf-8", errors="replace")[:maximum].decode("utf-8", errors="replace")
        )
        duration_ms = max(0, int((datetime.now(timezone.utc) - execution.started_at).total_seconds() * 1000))
        await self.repository.finish_execution(
            execution,
            status=status,
            exit_code=0 if status == "SUCCEEDED" else None,
            duration_ms=duration_ms,
            stdout=stdout,
            stderr=stderr,
            error_type=error_type,
            changed_files=[str(change["path"]) for change in changes],
            provider_metadata={
                "provider": "vercel_sandbox",
                "process_type": "preview",
                "network_policy": "deny_all",
                "synchronization_status": "SUCCEEDED" if status == "SUCCEEDED" else "FAILED",
            },
        )
        if stdout:
            await self.repository.add_event(record.execution_id, "tool.stdout", {"content": stdout})
        if stderr:
            await self.repository.add_event(record.execution_id, "tool.stderr", {"content": stderr})
        for change in changes:
            event_type = "file.created" if change["operation"] == "CREATED" else "file.updated"
            await self.repository.add_event(
                record.execution_id,
                event_type,
                {"path": change["path"], "operation": change["operation"]},
            )
        await self.repository.add_event(
            record.execution_id,
            "process.stopped",
            {"process_id": record.process_id, "status": record.status},
        )
        await self.repository.add_event(
            record.execution_id,
            "tool.completed" if status == "SUCCEEDED" else "tool.failed",
            {"status": status, "error_type": error_type},
        )

    async def _mark_start_failed(
        self, record: WorkspaceProcess, *, code: str, status: str = "FAILED", log_excerpt: str = ""
    ) -> None:
        record.status = "FAILED"
        record.health_status = "FAILED"
        record.log_excerpt = self._redactor.redact(log_excerpt or code)[:100_000]
        record.stopped_at = datetime.now(timezone.utc)
        record.last_checked_at = record.stopped_at
        await self.repository.update_process(record)
        await self._finish_execution(record, status=status, error_type=code, stdout="", stderr=record.log_excerpt)

    async def _mark_unpersisted_process_failed(
        self, execution: ToolExecution, *, code: str, status: str = "FAILED"
    ) -> None:
        session = getattr(self.repository, "session", None)
        if session is not None:
            await session.rollback()
        if execution.status != "RUNNING":
            return
        duration_ms = max(0, int((datetime.now(timezone.utc) - execution.started_at).total_seconds() * 1000))
        await self.repository.finish_execution(
            execution,
            status=status,
            exit_code=None,
            duration_ms=duration_ms,
            stdout="",
            stderr=code,
            error_type=code,
            changed_files=[],
            provider_metadata={
                "provider": "vercel_sandbox",
                "process_type": "preview",
                "network_policy": "deny_all",
            },
        )
        await self.repository.add_event(
            execution.execution_id,
            "tool.failed",
            {"status": status, "error_type": code},
        )

    async def start(self, workspace: Any, owner_id: str, request: ProcessStartInput) -> ProcessResponse:
        if getattr(workspace, "owner_id", None) != owner_id:
            raise ToolExecutionError("WORKSPACE_OWNERSHIP_REJECTED")
        argv = self._validate(request)
        cwd = self._validate_cwd(request.cwd)
        maximum_processes = self._positive_setting("NIE_ENGINEERING_MAX_PROCESSES", 1, 1, 10)
        if await self.repository.count_active_processes(owner_id) >= maximum_processes:
            raise ToolExecutionError("PROCESS_LIMIT_REACHED")
        preview_timeout = self._positive_setting("NIE_ENGINEERING_PREVIEW_TIMEOUT_SECONDS", 300, 30, 2_700)
        readiness_timeout = self._positive_setting("NIE_ENGINEERING_PREVIEW_READINESS_TIMEOUT_SECONDS", 20, 2, 120)
        execution_id = f"tex_{uuid.uuid4().hex}"
        correlation_id = f"cor_{uuid.uuid4().hex}"
        execution = ToolExecution(
            execution_id=execution_id,
            workspace_id=workspace.workspace_id,
            owner_id=owner_id,
            conversation_id=None,
            tool_name="process.start",
            sanitized_arguments={"argv": argv, "cwd": cwd, "port": request.port},
            approval_status="NOT_REQUIRED",
            status="RUNNING",
            correlation_id=correlation_id,
        )
        await self.repository.begin_execution(execution)
        await self.repository.add_event(
            execution_id,
            "tool.requested",
            {"tool_name": "process.start", "arguments": execution.sanitized_arguments},
        )
        await self.repository.add_event(execution_id, "tool.started", {"tool_name": "process.start"})
        now = datetime.now(timezone.utc)
        record = WorkspaceProcess(
            process_id=f"wpr_{uuid.uuid4().hex}",
            workspace_id=workspace.workspace_id,
            owner_id=owner_id,
            execution_id=execution_id,
            sanitized_command=argv,
            runner_provider="vercel_sandbox",
            permitted_port=request.port,
            status="STARTING",
            health_status="STARTING",
            provider_metadata={"network_policy": "deny_all"},
            expires_at=now + timedelta(seconds=preview_timeout),
        )
        try:
            await self.repository.create_process(record)
        except asyncio.CancelledError:
            await asyncio.shield(
                self._mark_unpersisted_process_failed(execution, code="PROCESS_CANCELLED", status="CANCELLED")
            )
            raise
        except Exception as exc:
            await self._mark_unpersisted_process_failed(execution, code="PROCESS_PERSISTENCE_FAILED")
            raise ToolExecutionError("PROCESS_PERSISTENCE_FAILED") from exc
        handle = None
        try:
            snapshot = await capture_workspace(self.storage, workspace)
            record.workspace_revision = snapshot.revision
            await self.repository.update_process(record)
            handle = await self.client.start(
                self._request(request.port, preview_timeout),
                snapshot,
                argv,
                cwd,
                request.port,
                preview_timeout,
            )
            record.sandbox_identifier = handle.sandbox_identifier
            record.runner_process_id = handle.process_id
            record.preview_url = handle.preview_url
            await self.repository.update_process(record)
            deadline = time.monotonic() + readiness_timeout
            while time.monotonic() < deadline:
                state = await self.client.inspect(handle.sandbox_identifier, handle.process_id, request.port)
                if state.status != "RUNNING":
                    raise ToolExecutionError("PREVIEW_PROCESS_EXITED_BEFORE_READY")
                if await self.readiness_probe(handle.preview_url):
                    mark_vercel_sandbox_capability_verified("preview")
                    record.status = "RUNNING"
                    record.health_status = "READY"
                    record.last_checked_at = datetime.now(timezone.utc)
                    await self.repository.update_process(record)
                    await self.repository.add_event(
                        execution_id,
                        "process.started",
                        {"process_id": record.process_id, "port": request.port, "health_status": "READY"},
                    )
                    return self._response(record)
                await asyncio.sleep(0.25)
            raise ToolExecutionError("PREVIEW_READINESS_TIMEOUT", status="TIMED_OUT")
        except WorkspaceStorageError as exc:
            if handle is not None:
                await asyncio.shield(self.client.destroy(handle.sandbox_identifier))
            await self._mark_start_failed(record, code=exc.code, status="SYNC_FAILED")
            raise ToolExecutionError(exc.code, status="SYNC_FAILED") from exc
        except ToolExecutionError as exc:
            if handle is not None:
                await asyncio.shield(self.client.destroy(handle.sandbox_identifier))
            await self._mark_start_failed(record, code=exc.code, status=exc.status)
            raise
        except asyncio.CancelledError as exc:
            if handle is not None:
                try:
                    await asyncio.shield(self.client.destroy(handle.sandbox_identifier))
                except BaseException:
                    pass
            await asyncio.shield(self._mark_start_failed(record, code="PROCESS_CANCELLED", status="CANCELLED"))
            raise exc
        except BaseException as exc:
            if handle is not None:
                try:
                    await asyncio.shield(self.client.destroy(handle.sandbox_identifier))
                except BaseException:
                    pass
            code = VercelSandboxRunner._provider_error(exc)
            await self._mark_start_failed(record, code=code)
            raise ToolExecutionError(code) from exc

    async def _finalize(self, workspace: Any, record: WorkspaceProcess, state: SandboxPreviewState) -> ProcessResponse:
        if record.status not in {"STARTING", "RUNNING"}:
            return self._response(record)
        try:
            synchronized = await reconcile_workspace(
                self.storage,
                workspace,
                str(record.execution_id),
                str(record.workspace_revision),
                state.files or {},
            )
            record.status = "STOPPED" if state.status != "FAILED" else "FAILED"
            record.health_status = "STOPPED" if record.status == "STOPPED" else "FAILED"
            record.log_excerpt = self._redactor.redact((state.stdout + state.stderr)[:100_000])
            record.stopped_at = datetime.now(timezone.utc)
            record.last_checked_at = record.stopped_at
            await self.repository.update_process(record)
            await self._finish_execution(
                record,
                status="SUCCEEDED" if record.status == "STOPPED" else "FAILED",
                error_type=None if record.status == "STOPPED" else "PREVIEW_PROCESS_FAILED",
                stdout=state.stdout,
                stderr=state.stderr,
                changes=synchronized.changes,
            )
            return self._response(record)
        except WorkspaceStorageError as exc:
            record.status = "FAILED"
            record.health_status = "FAILED"
            record.stopped_at = datetime.now(timezone.utc)
            await self.repository.update_process(record)
            await self._finish_execution(
                record,
                status="SYNC_FAILED",
                error_type=exc.code,
                stdout=state.stdout,
                stderr=state.stderr,
            )
            raise ToolExecutionError(exc.code, status="SYNC_FAILED") from exc

    async def stop(self, workspace_id: str, owner_id: str, process_id: str) -> ProcessResponse:
        record = await self.repository.get_process(process_id, workspace_id, owner_id)
        if record is None:
            raise ToolExecutionError("PROCESS_NOT_FOUND")
        if record.status not in {"STARTING", "RUNNING"}:
            return self._response(record)
        if not record.sandbox_identifier or not record.runner_process_id or not record.permitted_port:
            await self._mark_start_failed(record, code="PREVIEW_PROCESS_HANDLE_INVALID")
            raise ToolExecutionError("PREVIEW_PROCESS_HANDLE_INVALID")
        try:
            state = await self.client.stop_and_collect(
                record.sandbox_identifier,
                record.runner_process_id,
                record.permitted_port,
                self._request(record.permitted_port, self.settings.sandbox_timeout_seconds),
            )
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            code = VercelSandboxRunner._provider_error(exc)
            await self._mark_start_failed(record, code=code)
            raise ToolExecutionError(code) from exc
        workspace = await self.repository.get_workspace(workspace_id, owner_id)
        if workspace is None:
            raise ToolExecutionError("WORKSPACE_NOT_FOUND")
        return await self._finalize(workspace, record, state)

    async def inspect(self, workspace_id: str, owner_id: str, process_id: str) -> ProcessResponse:
        record = await self.repository.get_process(process_id, workspace_id, owner_id)
        if record is None:
            raise ToolExecutionError("PROCESS_NOT_FOUND")
        if record.status not in {"STARTING", "RUNNING"}:
            return self._response(record)
        if not record.sandbox_identifier or not record.runner_process_id or not record.permitted_port:
            await self._mark_start_failed(record, code="PREVIEW_PROCESS_HANDLE_INVALID")
            raise ToolExecutionError("PREVIEW_PROCESS_HANDLE_INVALID")
        try:
            state = await self.client.inspect(
                record.sandbox_identifier, record.runner_process_id, record.permitted_port
            )
        except BaseException as exc:
            code = VercelSandboxRunner._provider_error(exc)
            await self._mark_start_failed(record, code=code)
            raise ToolExecutionError(code) from exc
        record.last_checked_at = datetime.now(timezone.utc)
        if state.status == "RUNNING":
            record.health_status = "READY" if await self.readiness_probe(state.preview_url) else "STARTING"
            await self.repository.update_process(record)
            return self._response(record)
        try:
            collected = await self.client.stop_and_collect(
                record.sandbox_identifier,
                record.runner_process_id,
                record.permitted_port,
                self._request(record.permitted_port, self.settings.sandbox_timeout_seconds),
            )
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            code = VercelSandboxRunner._provider_error(exc)
            await self._mark_start_failed(record, code=code)
            raise ToolExecutionError(code) from exc
        workspace = await self.repository.get_workspace(workspace_id, owner_id)
        if workspace is None:
            raise ToolExecutionError("WORKSPACE_NOT_FOUND")
        return await self._finalize(workspace, record, collected)

    @staticmethod
    def _response(record: WorkspaceProcess) -> ProcessResponse:
        return ProcessResponse(
            process_id=str(record.process_id),
            workspace_id=str(record.workspace_id),
            execution_id=str(record.execution_id) if record.execution_id else None,
            status=str(record.status),
            port=int(record.permitted_port) if record.permitted_port else None,
            health_status=str(record.health_status),
            log_excerpt=str(record.log_excerpt or ""),
            preview_url=str(record.preview_url) if record.preview_url else None,
            runner=str(record.runner_provider or "vercel_sandbox"),
        )


def build_process_manager(repository: EngineeringWorkspaceRepository):
    mode = os.getenv("NIE_ENGINEERING_RUNNER", "local").strip().casefold()
    if mode == "vercel_sandbox":
        return VercelSandboxProcessManager(repository)
    return ProcessManager(repository)
