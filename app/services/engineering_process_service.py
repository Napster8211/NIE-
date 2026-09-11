"""Bounded local-development process manager behind the engineering runner contract."""

import asyncio
import os
import shutil
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.models.engineering_workspace import WorkspaceProcess
from app.repositories.engineering_workspace_repository import EngineeringWorkspaceRepository
from app.schemas.engineering_workspace import ProcessResponse, ProcessStartInput
from app.services.engineering_execution_service import ToolExecutionError
from app.services.runtime_environment import runtime_environment


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
            status=record.status,
            port=record.permitted_port,
            health_status=record.health_status,
            log_excerpt=record.log_excerpt or "",
        )
