"""Vercel Sandbox implementation of the Engineering command-runner contract."""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import timedelta
from pathlib import PurePosixPath
from typing import Any, Protocol

from app.schemas.engineering_workspace import CommandInput
from app.services.engineering_execution_service import (
    CommandRunner,
    PolicyDecision,
    SecretRedactor,
    ToolExecutionError,
)
from app.services.engineering_workspace_service import FileOperationResult
from app.services.engineering_workspace_storage import (
    LocalDurableWorkspaceStorage,
    WorkspaceSnapshot,
    WorkspaceStorage,
    WorkspaceStorageError,
)

SANDBOX_ROOT = "/vercel/sandbox/workspace"
SAFE_SANDBOX_ENVIRONMENT = {
    "HOME": "/vercel/sandbox",
    "TMPDIR": "/tmp",
    "PYTHONDONTWRITEBYTECODE": "1",
    "CI": "1",
}


@dataclass(frozen=True)
class VercelSandboxSettings:
    token: str
    team_id: str
    project_id: str
    image: str
    snapshot_id: str | None
    sandbox_timeout_seconds: int
    synchronization_timeout_seconds: int
    cleanup_timeout_seconds: int
    max_concurrent_executions: int
    network_policy: str
    network_allowlist: tuple[str, ...]

    @classmethod
    def from_environment(cls) -> VercelSandboxSettings:
        required = {
            "VERCEL_TOKEN": os.getenv("VERCEL_TOKEN", "").strip(),
            "VERCEL_TEAM_ID": os.getenv("VERCEL_TEAM_ID", "").strip(),
            "VERCEL_PROJECT_ID": os.getenv("VERCEL_PROJECT_ID", "").strip(),
        }
        missing = sorted(name for name, value in required.items() if not value)
        if missing:
            raise ToolExecutionError("VERCEL_SANDBOX_CREDENTIALS_MISSING")
        if any(value.startswith("<") and value.endswith(">") for value in required.values()):
            raise ToolExecutionError("VERCEL_SANDBOX_CREDENTIALS_PLACEHOLDER")
        image = os.getenv("NIE_ENGINEERING_SANDBOX_IMAGE", "vercel/sandbox/universal:latest").strip()
        if not image or not re.fullmatch(r"[A-Za-z0-9._/:@-]{1,240}", image):
            raise ToolExecutionError("VERCEL_SANDBOX_IMAGE_INVALID")
        policy = os.getenv("NIE_ENGINEERING_NETWORK_POLICY", "deny_all").strip().casefold()
        if policy not in {"deny_all", "allowlist"}:
            raise ToolExecutionError("VERCEL_SANDBOX_NETWORK_POLICY_INVALID")
        hosts = tuple(
            item.strip().casefold()
            for item in os.getenv("NIE_ENGINEERING_NETWORK_ALLOWLIST", "").split(",")
            if item.strip()
        )
        for host in hosts:
            cls._validate_host(host)
        snapshot_id = os.getenv("NIE_ENGINEERING_SANDBOX_SNAPSHOT_ID", "").strip() or None
        if snapshot_id and not re.fullmatch(r"[A-Za-z0-9._:@/-]{1,240}", snapshot_id):
            raise ToolExecutionError("VERCEL_SANDBOX_SNAPSHOT_ID_INVALID")
        cls._bounded_int("NIE_ENGINEERING_MAX_OUTPUT_BYTES", 100_000, 1_000, 1_000_000)
        return cls(
            token=required["VERCEL_TOKEN"],
            team_id=required["VERCEL_TEAM_ID"],
            project_id=required["VERCEL_PROJECT_ID"],
            image=image,
            snapshot_id=snapshot_id,
            sandbox_timeout_seconds=cls._bounded_int("NIE_ENGINEERING_SANDBOX_TIMEOUT_SECONDS", 300, 30, 2_700),
            synchronization_timeout_seconds=cls._bounded_int(
                "NIE_ENGINEERING_SYNCHRONIZATION_TIMEOUT_SECONDS", 60, 10, 600
            ),
            cleanup_timeout_seconds=cls._bounded_int("NIE_ENGINEERING_CLEANUP_TIMEOUT_SECONDS", 15, 3, 60),
            max_concurrent_executions=cls._bounded_int("NIE_ENGINEERING_MAX_CONCURRENT_EXECUTIONS", 1, 1, 10),
            network_policy=policy,
            network_allowlist=hosts,
        )

    @staticmethod
    def _bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
        try:
            value = int(os.getenv(name, str(default)))
        except ValueError as exc:
            raise ToolExecutionError(f"{name}_INVALID") from exc
        if value < minimum or value > maximum:
            raise ToolExecutionError(f"{name}_INVALID")
        return value

    @staticmethod
    def _validate_host(host: str) -> None:
        if host == "*" or not re.fullmatch(r"(?:\*\.)?[a-z0-9](?:[a-z0-9.-]{0,251}[a-z0-9])?", host):
            raise ToolExecutionError("VERCEL_SANDBOX_NETWORK_HOST_INVALID")
        without_wildcard = host.removeprefix("*.")
        try:
            address = ipaddress.ip_address(without_wildcard)
        except ValueError:
            address = None
        if address is not None and (address.is_private or address.is_loopback or address.is_link_local):
            raise ToolExecutionError("VERCEL_SANDBOX_NETWORK_HOST_FORBIDDEN")
        if without_wildcard in {"localhost", "metadata.google.internal"} or without_wildcard.endswith(".internal"):
            raise ToolExecutionError("VERCEL_SANDBOX_NETWORK_HOST_FORBIDDEN")


@dataclass(frozen=True)
class SandboxCreateRequest:
    execution_time_limit_seconds: int
    synchronization_timeout_seconds: int
    image: str
    snapshot_id: str | None
    network_allowed_hosts: tuple[str, ...]
    max_file_bytes: int
    max_workspace_bytes: int
    max_file_count: int


@dataclass(frozen=True)
class SandboxCommandResult:
    stdout: str
    stderr: str
    exit_code: int


class SandboxSession(Protocol):
    @property
    def identifier(self) -> str: ...

    async def upload(self, snapshot: WorkspaceSnapshot) -> None: ...

    async def execute(self, argv: list[str], cwd: str, timeout_seconds: int) -> SandboxCommandResult: ...

    async def download(self) -> Mapping[str, bytes]: ...

    async def cancel(self) -> None: ...

    async def close(self) -> None: ...


class SandboxClient(Protocol):
    async def create(self, request: SandboxCreateRequest) -> SandboxSession: ...


class VercelPythonSandboxSession:
    def __init__(self, box: Any, api_session: Any, request: SandboxCreateRequest):
        self._box = box
        self._api_session = api_session
        self._request = request
        self._closed = False

    @property
    def identifier(self) -> str:
        return str(self._box.name)

    async def upload(self, snapshot: WorkspaceSnapshot) -> None:
        await self._box.fs.mkdir(SANDBOX_ROOT, recursive=True)
        for path in sorted(snapshot.files):
            target = PurePosixPath(SANDBOX_ROOT) / PurePosixPath(path)
            if target.parent != PurePosixPath(SANDBOX_ROOT):
                await self._box.fs.mkdir(target.parent.as_posix(), recursive=True)
            await self._box.fs.write_bytes(target.as_posix(), snapshot.files[path].content)

    async def execute(self, argv: list[str], cwd: str, timeout_seconds: int) -> SandboxCommandResult:
        relative_cwd = PurePosixPath(cwd or ".")
        remote_cwd = (PurePosixPath(SANDBOX_ROOT) / relative_cwd).as_posix()
        completed = await self._box.run_process(
            argv[0],
            argv[1:],
            cwd=remote_cwd,
            env=SAFE_SANDBOX_ENVIRONMENT,
            kill_after=timeout_seconds,
            capture_output=True,
            check=False,
        )
        return SandboxCommandResult(completed.stdout or "", completed.stderr or "", int(completed.returncode))

    async def download(self) -> Mapping[str, bytes]:
        files: dict[str, bytes] = {}
        total_bytes = 0

        async def walk(relative: PurePosixPath) -> None:
            nonlocal total_bytes
            remote = (PurePosixPath(SANDBOX_ROOT) / relative).as_posix()
            for entry in await self._box.fs.listdir(remote):
                if not entry.path or "/" in entry.path or "\\" in entry.path or entry.path in {".", ".."}:
                    raise WorkspaceStorageError("SANDBOX_RETURNED_PATH_INVALID")
                child = relative / entry.path
                if entry.kind == "directory":
                    if entry.path in LocalDurableWorkspaceStorage.IGNORED_DIRECTORY_NAMES:
                        continue
                    await walk(child)
                elif entry.kind == "file":
                    content = await self._box.fs.read_bytes((PurePosixPath(SANDBOX_ROOT) / child).as_posix())
                    if len(content) > self._request.max_file_bytes:
                        raise WorkspaceStorageError("SANDBOX_RETURNED_FILE_TOO_LARGE")
                    total_bytes += len(content)
                    if total_bytes > self._request.max_workspace_bytes:
                        raise WorkspaceStorageError("SANDBOX_RETURNED_WORKSPACE_TOO_LARGE")
                    files[child.as_posix()] = content
                    if len(files) > self._request.max_file_count:
                        raise WorkspaceStorageError("SANDBOX_RETURNED_FILE_COUNT_EXCEEDED")
                elif entry.kind == "symlink":
                    raise WorkspaceStorageError("SANDBOX_RETURNED_SYMLINK")
                else:
                    raise WorkspaceStorageError("SANDBOX_RETURNED_NON_REGULAR_FILE")

        await walk(PurePosixPath("."))
        return files

    async def cancel(self) -> None:
        await self._box.stop()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            try:
                await self._box.stop()
            except BaseException:
                # Destroy remains the authoritative cleanup operation. A sandbox
                # may already be stopped after cancellation or provider timeout.
                pass
            await self._box.destroy()
        finally:
            await self._api_session.__aexit__(None, None, None)


class VercelPythonSandboxClient:
    """Thin adapter over the official `vercel` Python SDK (Option A)."""

    def __init__(self, settings: VercelSandboxSettings):
        self.settings = settings

    async def create(self, request: SandboxCreateRequest) -> SandboxSession:
        from vercel import sandbox
        from vercel.api import session
        from vercel.sandbox import (
            NetworkPolicy,
            SandboxCredentials,
            SandboxServiceOptions,
            SnapshotSource,
        )

        async def resolve_credentials() -> SandboxCredentials:
            return SandboxCredentials(
                token=self.settings.token,
                team_id=self.settings.team_id,
                project_id=self.settings.project_id,
            )

        service_options = SandboxServiceOptions(
            credentials_factory=resolve_credentials,
            file_transfer_timeout=timedelta(seconds=request.synchronization_timeout_seconds),
        )
        api_session = session(service_options=[service_options])
        await api_session.__aenter__()
        kwargs: dict[str, Any] = {
            "project_id": self.settings.project_id,
            "persistent": False,
            "execution_time_limit": timedelta(seconds=request.execution_time_limit_seconds),
            "network_policy": (
                NetworkPolicy.custom(allow={host: () for host in request.network_allowed_hosts})
                if request.network_allowed_hosts
                else NetworkPolicy.deny_all()
            ),
            "env": SAFE_SANDBOX_ENVIRONMENT,
            "tags": {"service": "nie-engineering", "environment": os.getenv("NIE_ENV", "unknown")[:64]},
        }
        if request.snapshot_id:
            kwargs["source"] = SnapshotSource(snapshot_id=request.snapshot_id)
        else:
            kwargs["image"] = request.image
        try:
            box = await sandbox.create_sandbox(**kwargs)
        except BaseException:
            await api_session.__aexit__(None, None, None)
            raise
        return VercelPythonSandboxSession(box, api_session, request)


class VercelSandboxRunner(CommandRunner):
    """Synchronizes a durable workspace through one ephemeral microVM execution."""

    def __init__(
        self,
        *,
        client: SandboxClient | None = None,
        storage: WorkspaceStorage | None = None,
        settings: VercelSandboxSettings | None = None,
    ):
        self.settings = settings or VercelSandboxSettings.from_environment()
        self.client = client or VercelPythonSandboxClient(self.settings)
        try:
            self.storage = storage or LocalDurableWorkspaceStorage()
        except WorkspaceStorageError as exc:
            raise ToolExecutionError(exc.code) from exc
        self._active: dict[str, SandboxSession] = {}
        self._cancelled: set[str] = set()
        self._lock = asyncio.Lock()
        self._slots = asyncio.Semaphore(self.settings.max_concurrent_executions)
        self._redactor = SecretRedactor()

    def _network_hosts(self, policy: PolicyDecision) -> tuple[str, ...]:
        if not policy.network_allowed:
            return ()
        if self.settings.network_policy != "allowlist" or not self.settings.network_allowlist:
            raise ToolExecutionError("VERCEL_SANDBOX_NETWORK_NOT_APPROVED")
        return self.settings.network_allowlist

    @staticmethod
    def _provider_error(error: BaseException) -> str:
        name = type(error).__name__
        status_code = getattr(error, "status_code", None)
        provider_code = str(getattr(error, "code", "")).casefold()
        if status_code == 429 or "quota" in provider_code or "limit" in provider_code:
            return "VERCEL_SANDBOX_QUOTA_EXCEEDED"
        if status_code in {401, 403}:
            return "VERCEL_SANDBOX_AUTHENTICATION_FAILED"
        if name in {"SandboxTimeoutError", "TimeoutError"} or isinstance(error, asyncio.TimeoutError):
            return "VERCEL_SANDBOX_TIMEOUT"
        if name in {"SandboxTerminalStateError", "SandboxTerminalStateException"}:
            return "VERCEL_SANDBOX_TERMINATED"
        if name == "SandboxCredentialsError":
            return "VERCEL_SANDBOX_CREDENTIALS_INVALID"
        return "VERCEL_SANDBOX_PROVIDER_ERROR"

    def _bounded_output(self, content: str, maximum: int) -> tuple[str, bool]:
        encoded = content.encode("utf-8", errors="replace")
        truncated = len(encoded) > maximum
        selected = encoded[:maximum].decode("utf-8", errors="replace")
        selected = self._redactor.redact(selected)
        if truncated:
            selected += "\n...[OUTPUT TRUNCATED]"
        return selected, truncated

    async def run(
        self, execution_id: str, root_path: str, command: CommandInput, policy: PolicyDecision
    ) -> FileOperationResult:
        async with self._slots:
            return await self._run_one(execution_id, root_path, command, policy)

    async def _run_one(
        self, execution_id: str, root_path: str, command: CommandInput, policy: PolicyDecision
    ) -> FileOperationResult:
        snapshot = await asyncio.wait_for(
            asyncio.to_thread(self.storage.capture, root_path),
            timeout=self.settings.synchronization_timeout_seconds,
        )
        request = SandboxCreateRequest(
            execution_time_limit_seconds=min(
                self.settings.sandbox_timeout_seconds,
                max(command.timeout_seconds + 30, 60),
            ),
            synchronization_timeout_seconds=self.settings.synchronization_timeout_seconds,
            image=self.settings.image,
            snapshot_id=self.settings.snapshot_id,
            network_allowed_hosts=self._network_hosts(policy),
            max_file_bytes=self.storage.max_file_bytes,
            max_workspace_bytes=self.storage.max_workspace_bytes,
            max_file_count=self.storage.max_file_count,
        )
        session: SandboxSession | None = None
        stage = "create"
        try:
            session = await asyncio.wait_for(
                self.client.create(request), timeout=self.settings.synchronization_timeout_seconds
            )
            async with self._lock:
                self._active[execution_id] = session
                cancelled = execution_id in self._cancelled
            if cancelled:
                await session.cancel()
                raise ToolExecutionError("COMMAND_CANCELLED", status="CANCELLED")
            stage = "upload"
            await asyncio.wait_for(session.upload(snapshot), timeout=self.settings.synchronization_timeout_seconds)
            stage = "command"
            command_result = await asyncio.wait_for(
                session.execute(list(command.argv), command.cwd, command.timeout_seconds),
                timeout=command.timeout_seconds + 5,
            )
            async with self._lock:
                if execution_id in self._cancelled:
                    raise ToolExecutionError("COMMAND_CANCELLED", status="CANCELLED")
            stage = "download"
            returned = await asyncio.wait_for(session.download(), timeout=self.settings.synchronization_timeout_seconds)
            stage = "synchronize"
            synchronized = await asyncio.wait_for(
                asyncio.to_thread(self.storage.reconcile, root_path, snapshot.revision, returned),
                timeout=self.settings.synchronization_timeout_seconds,
            )
            stdout, stdout_truncated = self._bounded_output(command_result.stdout, command.max_output_bytes)
            stderr, stderr_truncated = self._bounded_output(command_result.stderr, command.max_output_bytes)
            reference = hashlib.sha256(session.identifier.encode("utf-8")).hexdigest()[:20]
            provider_metadata = {
                "provider": "vercel_sandbox",
                "sandbox_name": session.identifier,
                "sandbox_reference": reference,
                "network_enabled": bool(request.network_allowed_hosts),
                "network_policy": "allowlist" if request.network_allowed_hosts else "deny_all",
                "workspace_revision_before": synchronized.revision_before,
                "workspace_revision_after": synchronized.revision_after,
                "synchronization_status": "SUCCEEDED",
            }
            result = FileOperationResult(
                {
                    "classification": policy.classification,
                    "runner": "vercel_sandbox",
                    "sandbox_reference": reference,
                    "network_enabled": bool(request.network_allowed_hosts),
                    "output_truncated": stdout_truncated or stderr_truncated,
                    "synchronization_status": "SUCCEEDED",
                    "workspace_revision": synchronized.revision_after,
                },
                changed_files=[str(change["path"]) for change in synchronized.changes],
                changes=list(synchronized.changes),
                provider_metadata=provider_metadata,
                stdout=stdout,
                stderr=stderr,
                exit_code=command_result.exit_code,
            )
            if command_result.exit_code != 0:
                raise ToolExecutionError(
                    "COMMAND_EXIT_NONZERO",
                    exit_code=command_result.exit_code,
                    stdout=stdout,
                    stderr=stderr,
                    result=result,
                )
        except WorkspaceStorageError as exc:
            raise ToolExecutionError(exc.code, status="SYNC_FAILED") from exc
        except asyncio.TimeoutError as exc:
            if stage == "command":
                raise ToolExecutionError("COMMAND_TIMEOUT", status="TIMED_OUT") from exc
            status = "SYNC_FAILED" if stage in {"upload", "download", "synchronize"} else "FAILED"
            raise ToolExecutionError("VERCEL_SANDBOX_TIMEOUT", status=status) from exc
        except ToolExecutionError:
            raise
        except asyncio.CancelledError:
            raise
        except BaseException as exc:
            async with self._lock:
                was_cancelled = execution_id in self._cancelled
            if was_cancelled:
                raise ToolExecutionError("COMMAND_CANCELLED", status="CANCELLED") from exc
            raise ToolExecutionError(self._provider_error(exc)) from exc
        else:
            try:
                await asyncio.wait_for(session.close(), timeout=self.settings.cleanup_timeout_seconds)
            except BaseException as exc:
                raise ToolExecutionError("VERCEL_SANDBOX_CLEANUP_FAILED") from exc
            session = None
            return result
        finally:
            async with self._lock:
                self._active.pop(execution_id, None)
                self._cancelled.discard(execution_id)
            if session is not None:
                try:
                    await asyncio.wait_for(session.close(), timeout=self.settings.cleanup_timeout_seconds)
                except BaseException:
                    pass

    async def cancel(self, execution_id: str) -> bool:
        async with self._lock:
            self._cancelled.add(execution_id)
            session = self._active.get(execution_id)
        if session is None:
            return True
        try:
            await asyncio.wait_for(session.cancel(), timeout=self.settings.cleanup_timeout_seconds)
        except BaseException:
            return False
        return True


def vercel_sandbox_readiness() -> dict[str, object]:
    try:
        settings = VercelSandboxSettings.from_environment()
    except ToolExecutionError as exc:
        return {"provider": "vercel_sandbox", "configured": False, "reason": exc.code}
    try:
        import vercel  # noqa: F401
    except ImportError:
        return {"provider": "vercel_sandbox", "configured": False, "reason": "VERCEL_SDK_NOT_INSTALLED"}
    return {
        "provider": "vercel_sandbox",
        "configured": True,
        "image": "snapshot" if settings.snapshot_id else settings.image,
        "network_policy": settings.network_policy,
        "sandbox_timeout_seconds": settings.sandbox_timeout_seconds,
        "max_concurrent_executions": settings.max_concurrent_executions,
    }
