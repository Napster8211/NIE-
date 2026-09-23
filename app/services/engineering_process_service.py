"""Managed Engineering preview processes for local development and Vercel Sandbox."""

from __future__ import annotations

import asyncio
import hashlib
import math
import os
import secrets
import shutil
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

import httpx

from app.models.engineering_workspace import ToolExecution, WorkspaceProcess
from app.repositories.engineering_workspace_repository import (
    ACTIVE_PROCESS_STATUSES,
    TERMINAL_PROCESS_STATUSES,
    EngineeringWorkspaceRepository,
)
from app.schemas.engineering_workspace import PreviewSessionResponse, ProcessResponse, ProcessStartInput
from app.services.engineering_authorization import authorize_engineering_identity, engineering_mode_enabled
from app.services.engineering_execution_service import SecretRedactor, ToolExecutionError
from app.services.engineering_preview_gateway import preview_gateway_path
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
    VercelPythonPreviewClient,
    VercelSandboxRunner,
    VercelSandboxSettings,
    mark_vercel_sandbox_capability_verified,
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo is not None else value.replace(tzinfo=timezone.utc)


def _expired(record: WorkspaceProcess, now: datetime | None = None) -> bool:
    expires_at = _aware(record.expires_at)
    return bool(expires_at and expires_at <= (now or _utc_now()))


def _process_response(record: WorkspaceProcess) -> ProcessResponse:
    public_preview = None
    if record.runner_provider == "vercel_sandbox" and record.status == "READY":
        public_preview = preview_gateway_path(str(record.workspace_id), str(record.process_id))
    return ProcessResponse(
        process_id=str(record.process_id),
        workspace_id=str(record.workspace_id),
        execution_id=str(record.execution_id) if record.execution_id else None,
        status=str(record.status),
        port=int(record.permitted_port) if record.permitted_port else None,
        health_status=str(record.health_status),
        log_excerpt=str(record.log_excerpt or ""),
        preview_url=public_preview,
        runner=str(record.runner_provider or "local"),
        expires_at=_aware(record.expires_at),
        cancellation_requested_at=_aware(record.cancellation_requested_at),
    )


class ProcessManager:
    """Development-only local process manager; never selected in production."""

    _active: dict[str, asyncio.subprocess.Process] = {}
    _lock = asyncio.Lock()

    def __init__(self, repository: EngineeringWorkspaceRepository):
        self.repository = repository

    @staticmethod
    def _validate(request: ProcessStartInput) -> list[str]:
        if runtime_environment() not in {"development", "test"}:
            raise ToolExecutionError("PROCESS_RUNNER_REQUIRES_ISOLATED_REMOTE_IMPLEMENTATION")
        if os.getenv("NIE_ENGINEERING_RUNNER", "local").strip().casefold() != "local":
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
        timeout = int(os.getenv("NIE_ENGINEERING_PREVIEW_TIMEOUT_SECONDS", "300"))
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
                "CI": "1",
            },
        )
        process_id = f"wpr_{uuid.uuid4().hex}"
        async with self._lock:
            self._active[process_id] = process
        record = WorkspaceProcess(
            process_id=process_id,
            workspace_id=workspace.workspace_id,
            owner_id=owner_id,
            sanitized_command=request.argv,
            runner_process_id=str(process.pid),
            permitted_port=request.port,
            status="RUNNING",
            health_status="RUNNING",
            expires_at=_utc_now() + timedelta(seconds=timeout),
        )
        await self.repository.create_process(record)
        return _process_response(record)

    async def inspect(self, workspace_id: str, owner_id: str, process_id: str) -> ProcessResponse:
        record = await self.repository.get_process(process_id, workspace_id, owner_id)
        if record is None:
            raise ToolExecutionError("PROCESS_NOT_FOUND")
        if _expired(record) and record.status in ACTIVE_PROCESS_STATUSES:
            return await self._terminate(record, "EXPIRED")
        async with self._lock:
            process = self._active.get(process_id)
        if process is not None and process.returncode is not None and record.status in ACTIVE_PROCESS_STATUSES:
            record.status = "STOPPED" if process.returncode == 0 else "FAILED"
            record.health_status = record.status
            record.stopped_at = _utc_now()
            await self.repository.update_process(record)
        return _process_response(record)

    async def _terminate(self, record: WorkspaceProcess, target: str) -> ProcessResponse:
        async with self._lock:
            process = self._active.pop(record.process_id, None)
        if process is not None and process.returncode is None:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except asyncio.TimeoutError:
                process.kill()
                await process.wait()
        record.status = target
        record.health_status = target
        record.stopped_at = _utc_now()
        await self.repository.update_process(record)
        return _process_response(record)

    async def stop(self, workspace_id: str, owner_id: str, process_id: str) -> ProcessResponse:
        record = await self.repository.get_process(process_id, workspace_id, owner_id)
        if record is None:
            raise ToolExecutionError("PROCESS_NOT_FOUND")
        if record.status in TERMINAL_PROCESS_STATUSES:
            return _process_response(record)
        return await self._terminate(record, "CANCELLED" if record.status == "STARTING" else "STOPPED")

    async def list_active(self, workspace_id: str, owner_id: str) -> list[ProcessResponse]:
        records = await self.repository.list_active_processes(workspace_id, owner_id)
        results = []
        for record in records:
            inspected = await self.inspect(workspace_id, owner_id, record.process_id)
            if inspected.status in ACTIVE_PROCESS_STATUSES:
                results.append(inspected)
        return results

    async def issue_preview_session(
        self,
        workspace_id: str,
        owner_id: str,
        process_id: str,
    ) -> tuple[PreviewSessionResponse, str]:
        raise ToolExecutionError("PREVIEW_GATEWAY_REQUIRES_ISOLATED_REMOTE_RUNNER")

    async def authorize_preview(
        self,
        workspace_id: str,
        owner_id: str,
        process_id: str,
        token: str,
    ) -> WorkspaceProcess:
        raise ToolExecutionError("PROCESS_NOT_FOUND")

    @classmethod
    async def shutdown_active(cls) -> int:
        async with cls._lock:
            active = list(cls._active.values())
            count = len(active)
            cls._active.clear()
            log_tasks = list(getattr(cls, "_log_tasks", {}).values())
            getattr(cls, "_log_tasks", {}).clear()
            getattr(cls, "_logs", {}).clear()
        for process in active:
            if process.returncode is None:
                process.terminate()
        for task in log_tasks:
            task.cancel()
        await asyncio.gather(*(process.wait() for process in active), return_exceptions=True)
        await asyncio.gather(*log_tasks, return_exceptions=True)
        return count


class VercelSandboxProcessManager:
    """Durable, poll-driven state machine for reconnectable remote previews."""

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
            and args[1] in {"dev", "start", "preview"}
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
            async with httpx.AsyncClient(follow_redirects=False, timeout=2.0, trust_env=False) as client:
                response = await client.get(url, headers={"accept-encoding": "identity"})
            return response.status_code < 500 and not (300 <= response.status_code < 400)
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
        stdout: str = "",
        stderr: str = "",
        changes: tuple[dict[str, object], ...] = (),
    ) -> None:
        if not record.execution_id:
            return
        execution = await self.repository.get_execution(record.execution_id, record.workspace_id, record.owner_id)
        if execution is None or execution.status != "RUNNING":
            return
        maximum = self._positive_setting("NIE_ENGINEERING_MAX_OUTPUT_BYTES", 100_000, 1_000, 1_000_000)
        stdout = self._redactor.redact(stdout.encode()[:maximum].decode("utf-8", errors="replace"))
        stderr = self._redactor.redact(stderr.encode()[:maximum].decode("utf-8", errors="replace"))
        started_at = _aware(execution.started_at) or _utc_now()
        duration_ms = max(0, int((_utc_now() - started_at).total_seconds() * 1000))
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
                "synchronization_status": "SUCCEEDED" if status == "SUCCEEDED" else status,
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
            {"process_id": record.process_id, "status": record.status, "reason": record.terminal_reason},
        )
        await self.repository.add_event(
            record.execution_id,
            "tool.completed" if status == "SUCCEEDED" else "tool.failed",
            {"status": status, "error_type": error_type},
        )

    async def _finish_unpersisted_execution(self, execution: ToolExecution, code: str) -> None:
        session = getattr(self.repository, "session", None)
        if session is not None:
            await session.rollback()
        if execution.status != "RUNNING":
            return
        started_at = _aware(execution.started_at) or _utc_now()
        duration_ms = max(0, int((_utc_now() - started_at).total_seconds() * 1000))
        await self.repository.finish_execution(
            execution,
            status="FAILED",
            exit_code=None,
            duration_ms=duration_ms,
            stdout="",
            stderr=code,
            error_type=code,
            changed_files=[],
            provider_metadata={"provider": "vercel_sandbox", "process_type": "preview"},
        )
        await self.repository.add_event(
            execution.execution_id,
            "tool.failed",
            {"status": "FAILED", "error_type": code},
        )

    async def start(self, workspace: Any, owner_id: str, request: ProcessStartInput) -> ProcessResponse:
        if getattr(workspace, "owner_id", None) != owner_id:
            raise ToolExecutionError("WORKSPACE_OWNERSHIP_REJECTED")
        argv = self._validate(request)
        cwd = self._validate_cwd(request.cwd)
        maximum = self._positive_setting("NIE_ENGINEERING_MAX_PROCESSES", 1, 1, 10)
        if await self.repository.count_active_processes(owner_id) >= maximum:
            raise ToolExecutionError("PROCESS_LIMIT_REACHED")
        timeout = self._positive_setting("NIE_ENGINEERING_PREVIEW_TIMEOUT_SECONDS", 300, 30, 2_700)
        execution = ToolExecution(
            execution_id=f"tex_{uuid.uuid4().hex}",
            workspace_id=workspace.workspace_id,
            owner_id=owner_id,
            conversation_id=None,
            tool_name="process.start",
            sanitized_arguments={"argv": argv, "cwd": cwd, "port": request.port},
            approval_status="NOT_REQUIRED",
            status="RUNNING",
            correlation_id=f"cor_{uuid.uuid4().hex}",
        )
        await self.repository.begin_execution(execution)
        await self.repository.add_event(
            execution.execution_id,
            "tool.requested",
            {"tool_name": "process.start", "arguments": execution.sanitized_arguments},
        )
        await self.repository.add_event(execution.execution_id, "tool.started", {"tool_name": "process.start"})
        now = _utc_now()
        record = WorkspaceProcess(
            process_id=f"wpr_{uuid.uuid4().hex}",
            workspace_id=workspace.workspace_id,
            owner_id=owner_id,
            execution_id=execution.execution_id,
            sanitized_command=argv,
            runner_provider="vercel_sandbox",
            permitted_port=request.port,
            status="STARTING",
            health_status="STARTING",
            provider_metadata={"network_policy": "deny_all", "cwd": cwd},
            expires_at=now + timedelta(seconds=timeout),
        )
        try:
            await self.repository.create_process(record)
        except BaseException as exc:
            await self._finish_unpersisted_execution(execution, "PROCESS_PERSISTENCE_FAILED")
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise ToolExecutionError("PROCESS_PERSISTENCE_FAILED") from exc
        return _process_response(record)

    async def _terminal(
        self,
        record: WorkspaceProcess,
        *,
        target: str,
        reason: str,
        execution_status: str,
        error_type: str | None,
        stdout: str = "",
        stderr: str = "",
        changes: tuple[dict[str, object], ...] = (),
    ) -> ProcessResponse:
        values = {
            "stopped_at": _utc_now(),
            "terminal_reason": reason,
            "preview_session_token_hash": None,
            "preview_session_expires_at": None,
            "startup_claim_id": None,
            "startup_claimed_at": None,
            "cleanup_claim_id": None,
            "cleanup_claimed_at": None,
        }
        current, changed = await self.repository.transition_process(
            process_id=record.process_id,
            workspace_id=record.workspace_id,
            owner_id=record.owner_id,
            expected_statuses=ACTIVE_PROCESS_STATUSES,
            status=target,
            health_status=target,
            values=values,
        )
        if current is None:
            raise ToolExecutionError("PROCESS_NOT_FOUND")
        if changed:
            current.terminal_reason = reason
            await self._finish_execution(
                current,
                status=execution_status,
                error_type=error_type,
                stdout=stdout,
                stderr=stderr,
                changes=changes,
            )
        return _process_response(current)

    async def _fail(self, record: WorkspaceProcess, code: str, *, status: str = "FAILED") -> ProcessResponse:
        safe = self._redactor.redact(code)[:100_000]
        record.log_excerpt = safe
        await self.repository.update_process(record)
        return await self._terminal(
            record,
            target="FAILED",
            reason=code,
            execution_status=status,
            error_type=code,
            stderr=safe,
        )

    @staticmethod
    def _provider_not_found(error: BaseException) -> bool:
        response = getattr(error, "response", None)
        status_code = getattr(error, "status_code", None) or getattr(response, "status_code", None)
        return status_code == 404 or "notfound" in error.__class__.__name__.casefold()

    async def _start_pending(self, record: WorkspaceProcess) -> ProcessResponse:
        if _expired(record):
            return await self.expire(record)
        claim_id = f"claim_{uuid.uuid4().hex}"
        claimed = await self.repository.claim_process_start(
            record.process_id,
            record.workspace_id,
            record.owner_id,
            claim_id,
            stale_after_seconds=self._positive_setting("NIE_ENGINEERING_STARTUP_LEASE_SECONDS", 60, 10, 300),
        )
        refreshed = await self.repository.get_process(record.process_id, record.workspace_id, record.owner_id)
        if refreshed is None:
            raise ToolExecutionError("PROCESS_NOT_FOUND")
        record = refreshed
        if not claimed or record.status != "STARTING":
            return _process_response(record)
        workspace = await self.repository.get_workspace(record.workspace_id, record.owner_id)
        if workspace is None:
            return await self._fail(record, "WORKSPACE_NOT_FOUND")
        handle = None
        try:
            if record.cancellation_requested_at:
                return await self._begin_cleanup(record, reason="CANCEL")
            snapshot = await capture_workspace(self.storage, workspace)
            record.workspace_revision = snapshot.revision
            await self.repository.update_process(record)
            expires_at = _aware(record.expires_at) or (_utc_now() + timedelta(seconds=30))
            timeout = max(30, math.ceil((expires_at - _utc_now()).total_seconds()))
            cwd = str((record.provider_metadata or {}).get("cwd", ""))
            handle = await self.client.start(
                self._request(int(record.permitted_port), timeout),
                snapshot,
                list(record.sanitized_command),
                cwd,
                int(record.permitted_port),
                timeout,
            )
            latest = await self.repository.get_process(record.process_id, record.workspace_id, record.owner_id)
            if latest is None:
                await asyncio.shield(self.client.destroy(handle.sandbox_identifier))
                raise ToolExecutionError("PROCESS_NOT_FOUND")
            if latest.status in TERMINAL_PROCESS_STATUSES or latest.cancellation_requested_at or _expired(latest):
                await asyncio.shield(self.client.destroy(handle.sandbox_identifier))
                if latest.status in TERMINAL_PROCESS_STATUSES:
                    return _process_response(latest)
                reason = "EXPIRE" if _expired(latest) else "CANCEL"
                return await self._begin_cleanup(latest, reason=reason)
            transitioned, changed = await self.repository.transition_process(
                process_id=latest.process_id,
                workspace_id=latest.workspace_id,
                owner_id=latest.owner_id,
                expected_statuses=("STARTING",),
                status="RUNNING",
                health_status="RUNNING",
                values={
                    "sandbox_identifier": handle.sandbox_identifier,
                    "runner_process_id": handle.process_id,
                    "provider_preview_url": handle.preview_url,
                    "workspace_revision": snapshot.revision,
                    "startup_claim_id": None,
                    "startup_claimed_at": None,
                },
            )
            if transitioned is None:
                raise ToolExecutionError("PROCESS_NOT_FOUND")
            record = transitioned
            if not changed:
                await asyncio.shield(self.client.destroy(handle.sandbox_identifier))
                return _process_response(record)
            readiness_timeout = self._positive_setting("NIE_ENGINEERING_PREVIEW_READINESS_TIMEOUT_SECONDS", 20, 2, 120)
            deadline = time.monotonic() + readiness_timeout
            while time.monotonic() < deadline:
                latest = await self.repository.get_process(record.process_id, record.workspace_id, record.owner_id)
                if latest is None:
                    raise ToolExecutionError("PROCESS_NOT_FOUND")
                if latest.cancellation_requested_at or latest.status == "STOPPING":
                    return await self._begin_cleanup(latest, reason=latest.stop_reason or "CANCEL")
                if _expired(latest):
                    return await self.expire(latest)
                state = await self.client.inspect(
                    handle.sandbox_identifier,
                    handle.process_id,
                    int(record.permitted_port),
                )
                if state.status != "RUNNING":
                    raise ToolExecutionError("PREVIEW_PROCESS_EXITED_BEFORE_READY")
                if await self.readiness_probe(handle.preview_url):
                    current, won = await self.repository.transition_process(
                        process_id=record.process_id,
                        workspace_id=record.workspace_id,
                        owner_id=record.owner_id,
                        expected_statuses=("RUNNING",),
                        status="READY",
                        health_status="READY",
                    )
                    if current is None:
                        raise ToolExecutionError("PROCESS_NOT_FOUND")
                    if won:
                        mark_vercel_sandbox_capability_verified("preview")
                        await self.repository.add_event(
                            record.execution_id,
                            "process.started",
                            {
                                "process_id": record.process_id,
                                "port": record.permitted_port,
                                "status": "READY",
                            },
                        )
                    return _process_response(current)
                await asyncio.sleep(0.25)
            raise ToolExecutionError("PREVIEW_READINESS_TIMEOUT", status="TIMED_OUT")
        except WorkspaceStorageError as exc:
            if handle is not None:
                await asyncio.shield(self.client.destroy(handle.sandbox_identifier))
            await self._fail(record, exc.code, status="SYNC_FAILED")
            raise ToolExecutionError(exc.code, status="SYNC_FAILED") from exc
        except asyncio.CancelledError:
            if handle is not None:
                try:
                    await asyncio.shield(self.client.destroy(handle.sandbox_identifier))
                except BaseException:
                    pass
            latest = await self.repository.get_process(record.process_id, record.workspace_id, record.owner_id)
            if latest and latest.status in ACTIVE_PROCESS_STATUSES:
                await asyncio.shield(self._begin_cleanup(latest, reason="CANCEL"))
            raise
        except ToolExecutionError as exc:
            if handle is not None:
                try:
                    await asyncio.shield(self.client.destroy(handle.sandbox_identifier))
                except BaseException:
                    pass
            latest = await self.repository.get_process(record.process_id, record.workspace_id, record.owner_id)
            if latest and latest.status in ACTIVE_PROCESS_STATUSES:
                await self._fail(latest, exc.code, status=exc.status)
            raise
        except BaseException as exc:
            if handle is not None:
                try:
                    await asyncio.shield(self.client.destroy(handle.sandbox_identifier))
                except BaseException:
                    pass
            code = VercelSandboxRunner._provider_error(exc)
            latest = await self.repository.get_process(record.process_id, record.workspace_id, record.owner_id)
            if latest and latest.status in ACTIVE_PROCESS_STATUSES:
                await self._fail(latest, code)
            raise ToolExecutionError(code) from exc

    async def _collect_and_reconcile(
        self,
        record: WorkspaceProcess,
        *,
        target: str,
        reason: str,
    ) -> ProcessResponse:
        if not record.sandbox_identifier or not record.runner_process_id or not record.permitted_port:
            if reason in {"CANCEL", "EXPIRE"}:
                target_status = "CANCELLED" if reason == "CANCEL" else "EXPIRED"
                error = "PROCESS_CANCELLED" if reason == "CANCEL" else "PROCESS_EXPIRED"
                execution_status = "CANCELLED" if reason == "CANCEL" else "TIMED_OUT"
                return await self._terminal(
                    record,
                    target=target_status,
                    reason=error,
                    execution_status=execution_status,
                    error_type=error,
                )
            return await self._fail(record, "PREVIEW_PROCESS_HANDLE_INVALID")
        try:
            if reason == "CANCEL":
                await self.client.destroy(record.sandbox_identifier)
                return await self._terminal(
                    record,
                    target="CANCELLED",
                    reason="PROCESS_CANCELLED",
                    execution_status="CANCELLED",
                    error_type="PROCESS_CANCELLED",
                )
            state = await self.client.stop_and_collect(
                record.sandbox_identifier,
                record.runner_process_id,
                int(record.permitted_port),
                self._request(int(record.permitted_port), self.settings.sandbox_timeout_seconds),
            )
            workspace = await self.repository.get_workspace(record.workspace_id, record.owner_id)
            if workspace is None:
                return await self._fail(record, "WORKSPACE_NOT_FOUND")
            synchronized = await reconcile_workspace(
                self.storage,
                workspace,
                str(record.execution_id),
                str(record.workspace_revision),
                state.files or {},
            )
            if reason == "EXPIRE":
                return await self._terminal(
                    record,
                    target="EXPIRED",
                    reason="PROCESS_EXPIRED",
                    execution_status="TIMED_OUT",
                    error_type="PROCESS_EXPIRED",
                    stdout=state.stdout,
                    stderr=state.stderr,
                    changes=synchronized.changes,
                )
            failed = state.status == "FAILED"
            return await self._terminal(
                record,
                target="FAILED" if failed else target,
                reason="PREVIEW_PROCESS_FAILED" if failed else "PROCESS_STOPPED",
                execution_status="FAILED" if failed else "SUCCEEDED",
                error_type="PREVIEW_PROCESS_FAILED" if failed else None,
                stdout=state.stdout,
                stderr=state.stderr,
                changes=synchronized.changes,
            )
        except WorkspaceStorageError as exc:
            await self._fail(record, exc.code, status="SYNC_FAILED")
            raise ToolExecutionError(exc.code, status="SYNC_FAILED") from exc
        except BaseException as exc:
            if reason == "EXPIRE":
                not_found = self._provider_not_found(exc)
                failure = None if not_found else VercelSandboxRunner._provider_error(exc)
                suffix = "PROVIDER_ALREADY_MISSING" if not_found else "CLEANUP_FAILED"
                return await self._terminal(
                    record,
                    target="EXPIRED",
                    reason=f"PROCESS_EXPIRED_{suffix}",
                    execution_status="TIMED_OUT",
                    error_type=failure or "PROCESS_EXPIRED",
                    stderr="" if not_found else "PROCESS_EXPIRY_CLEANUP_FAILED",
                )
            if isinstance(exc, asyncio.CancelledError):
                raise
            code = VercelSandboxRunner._provider_error(exc)
            await self._fail(record, code)
            raise ToolExecutionError(code) from exc

    async def _begin_cleanup(self, record: WorkspaceProcess, *, reason: str) -> ProcessResponse:
        if record.status in TERMINAL_PROCESS_STATUSES:
            return _process_response(record)
        target_reason = reason if reason in {"STOP", "CANCEL", "EXPIRE"} else "STOP"
        if record.status != "STOPPING":
            values: dict[str, Any] = {"stop_reason": target_reason}
            if target_reason == "CANCEL":
                values["cancellation_requested_at"] = record.cancellation_requested_at or _utc_now()
            current, _ = await self.repository.transition_process(
                process_id=record.process_id,
                workspace_id=record.workspace_id,
                owner_id=record.owner_id,
                expected_statuses=("STARTING", "RUNNING", "READY"),
                status="STOPPING",
                health_status="STOPPING",
                values=values,
            )
            if current is None:
                raise ToolExecutionError("PROCESS_NOT_FOUND")
            record = current
        else:
            target_reason = record.stop_reason or target_reason
        claim_id = f"cleanup_{uuid.uuid4().hex}"
        claimed = await self.repository.claim_process_cleanup(
            record.process_id,
            record.workspace_id,
            record.owner_id,
            claim_id,
            stale_after_seconds=self._positive_setting(
                "NIE_ENGINEERING_CLEANUP_LEASE_SECONDS",
                60,
                10,
                300,
            ),
        )
        refreshed = await self.repository.get_process(record.process_id, record.workspace_id, record.owner_id)
        if refreshed is None:
            raise ToolExecutionError("PROCESS_NOT_FOUND")
        record = refreshed
        if not claimed:
            return _process_response(record)
        return await self._collect_and_reconcile(record, target="STOPPED", reason=target_reason)

    async def expire(self, record: WorkspaceProcess) -> ProcessResponse:
        if record.status in TERMINAL_PROCESS_STATUSES:
            return _process_response(record)
        return await self._begin_cleanup(record, reason="EXPIRE")

    async def inspect(self, workspace_id: str, owner_id: str, process_id: str) -> ProcessResponse:
        record = await self.repository.get_process(process_id, workspace_id, owner_id)
        if record is None:
            raise ToolExecutionError("PROCESS_NOT_FOUND")
        if record.status in TERMINAL_PROCESS_STATUSES:
            return _process_response(record)
        if _expired(record):
            return await self.expire(record)
        if record.status == "STOPPING":
            return await self._begin_cleanup(record, reason=record.stop_reason or "STOP")
        if record.cancellation_requested_at:
            return await self._begin_cleanup(record, reason="CANCEL")
        if record.status == "STARTING":
            return await self._start_pending(record)
        if not record.sandbox_identifier or not record.runner_process_id or not record.permitted_port:
            return await self._fail(record, "PREVIEW_PROCESS_HANDLE_INVALID")
        try:
            state = await self.client.inspect(
                record.sandbox_identifier,
                record.runner_process_id,
                int(record.permitted_port),
            )
        except BaseException as exc:
            latest = await self.repository.get_process(process_id, workspace_id, owner_id)
            if latest and latest.status in TERMINAL_PROCESS_STATUSES:
                return _process_response(latest)
            code = VercelSandboxRunner._provider_error(exc)
            await self._fail(record, code)
            raise ToolExecutionError(code) from exc
        if state.status != "RUNNING":
            return await self._begin_cleanup(record, reason="STOP")
        ready = await self.readiness_probe(str(record.provider_preview_url or state.preview_url))
        next_status = "READY" if ready else "RUNNING"
        current, _ = await self.repository.transition_process(
            process_id=record.process_id,
            workspace_id=record.workspace_id,
            owner_id=record.owner_id,
            expected_statuses=("RUNNING", "READY"),
            status=next_status,
            health_status=next_status,
        )
        return _process_response(current or record)

    async def stop(self, workspace_id: str, owner_id: str, process_id: str) -> ProcessResponse:
        record = await self.repository.get_process(process_id, workspace_id, owner_id)
        if record is None:
            raise ToolExecutionError("PROCESS_NOT_FOUND")
        if record.status in TERMINAL_PROCESS_STATUSES:
            return _process_response(record)
        if _expired(record):
            return await self.expire(record)
        reason = "CANCEL" if record.status == "STARTING" else "STOP"
        return await self._begin_cleanup(record, reason=reason)

    async def list_active(self, workspace_id: str, owner_id: str) -> list[ProcessResponse]:
        if await self.repository.get_workspace(workspace_id, owner_id) is None:
            raise ToolExecutionError("WORKSPACE_NOT_FOUND")
        records = await self.repository.list_active_processes(workspace_id, owner_id)
        active: list[ProcessResponse] = []
        for record in records:
            if _expired(record):
                await self.expire(record)
                continue
            active.append(_process_response(record))
        return active

    async def issue_preview_session(
        self,
        workspace_id: str,
        owner_id: str,
        process_id: str,
    ) -> tuple[PreviewSessionResponse, str]:
        record = await self.repository.get_process(process_id, workspace_id, owner_id)
        if record is None:
            raise ToolExecutionError("PROCESS_NOT_FOUND")
        if _expired(record):
            await self.expire(record)
            raise ToolExecutionError("PROCESS_EXPIRED", status="TIMED_OUT")
        if record.status != "READY" or not record.provider_preview_url:
            raise ToolExecutionError("PREVIEW_NOT_READY")
        workspace = await self.repository.get_workspace(workspace_id, owner_id)
        if workspace is None:
            raise ToolExecutionError("WORKSPACE_NOT_FOUND")
        if str(getattr(workspace, "storage_backend", "")).upper() == "SUPABASE":
            current_revision = await self.repository.workspace_revision_token(workspace_id, owner_id)
            if current_revision != record.workspace_revision:
                raise ToolExecutionError("PREVIEW_WORKSPACE_REVISION_STALE")
        lifetime = self._positive_setting("NIE_ENGINEERING_PREVIEW_SESSION_TTL_SECONDS", 60, 10, 300)
        process_expires = _aware(record.expires_at) or (_utc_now() + timedelta(seconds=lifetime))
        expires_at = min(process_expires, _utc_now() + timedelta(seconds=lifetime))
        token = secrets.token_urlsafe(32)
        record.preview_session_token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        record.preview_session_expires_at = expires_at
        await self.repository.update_process(record)
        return (
            PreviewSessionResponse(
                process_id=record.process_id,
                preview_url=preview_gateway_path(workspace_id, process_id),
                expires_at=expires_at,
            ),
            token,
        )

    async def authorize_preview(
        self,
        workspace_id: str,
        owner_id: str,
        process_id: str,
        token: str,
    ) -> WorkspaceProcess:
        if not engineering_mode_enabled() or not token:
            raise ToolExecutionError("PROCESS_NOT_FOUND")
        token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
        record = await self.repository.get_process_for_preview(
            process_id=process_id,
            workspace_id=workspace_id,
            owner_id=owner_id,
            token_hash=token_hash,
        )
        if record is None or record.owner_id != owner_id or not str(owner_id).startswith("firebase:"):
            raise ToolExecutionError("PROCESS_NOT_FOUND")
        uid = str(owner_id).removeprefix("firebase:")
        try:
            authorize_engineering_identity(uid, None, "preview_session")
        except Exception as exc:
            raise ToolExecutionError("PROCESS_NOT_FOUND") from exc
        if _expired(record):
            await self.expire(record)
            raise ToolExecutionError("PROCESS_EXPIRED", status="TIMED_OUT")
        if record.status != "READY" or not record.provider_preview_url:
            raise ToolExecutionError("PROCESS_NOT_FOUND")
        workspace = await self.repository.get_workspace(workspace_id, owner_id)
        if workspace is None:
            raise ToolExecutionError("PROCESS_NOT_FOUND")
        if str(getattr(workspace, "storage_backend", "")).upper() == "SUPABASE":
            current_revision = await self.repository.workspace_revision_token(workspace_id, owner_id)
            if current_revision != record.workspace_revision:
                raise ToolExecutionError("PROCESS_NOT_FOUND")
        return record

    async def cleanup_expired(self, *, limit: int, minimum_age_seconds: int) -> dict[str, int]:
        records = await self.repository.list_expired_processes(
            limit=max(1, min(limit, 100)),
            minimum_age_seconds=max(0, minimum_age_seconds),
        )
        cleaned = 0
        failed = 0
        for record in records:
            try:
                response = await self.expire(record)
                cleaned += int(response.status == "EXPIRED")
            except BaseException:
                failed += 1
        return {"selected": len(records), "expired": cleaned, "failed": failed}


def build_process_manager(repository: EngineeringWorkspaceRepository):
    mode = os.getenv("NIE_ENGINEERING_RUNNER", "local").strip().casefold()
    if mode == "vercel_sandbox":
        return VercelSandboxProcessManager(repository)
    if runtime_environment() not in {"development", "test"}:
        raise ToolExecutionError("PROCESS_RUNNER_REQUIRES_ISOLATED_REMOTE_IMPLEMENTATION")
    return ProcessManager(repository)
