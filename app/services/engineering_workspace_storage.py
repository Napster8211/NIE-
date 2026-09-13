"""Versioned durable storage used to synchronize isolated engineering runners."""

from __future__ import annotations

import asyncio
import hashlib
import os
import shutil
import tempfile
import threading
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Protocol, runtime_checkable

from app.services.engineering_workspace_service import WorkspacePathResolver, configured_workspace_storage_backend
from app.services.runtime_environment import runtime_environment


def _deployed_environment() -> bool:
    return runtime_environment() in {"production", "prod", "staging", "stage"}


class WorkspaceStorageError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class WorkspaceStoredFile:
    path: str
    content: bytes
    sha256: str

    @property
    def size(self) -> int:
        return len(self.content)


@dataclass(frozen=True)
class WorkspaceSnapshot:
    revision: str
    files: Mapping[str, WorkspaceStoredFile]
    total_bytes: int


@dataclass(frozen=True)
class WorkspaceSyncResult:
    revision_before: str
    revision_after: str
    changes: tuple[dict[str, object], ...]


class WorkspaceStorage:
    """Storage contract independent of any one sandbox lifecycle."""

    max_file_bytes: int
    max_workspace_bytes: int
    max_file_count: int

    def capture(self, root_path: str) -> WorkspaceSnapshot:
        raise NotImplementedError

    def reconcile(
        self,
        root_path: str,
        base_revision: str,
        returned_files: Mapping[str, bytes],
    ) -> WorkspaceSyncResult:
        raise NotImplementedError


@runtime_checkable
class AsyncWorkspaceStorage(Protocol):
    """Database-backed storage contract used by ephemeral remote runners."""

    is_async: bool
    max_file_bytes: int
    max_workspace_bytes: int
    max_file_count: int

    async def capture(self, target: Any) -> WorkspaceSnapshot: ...

    async def reconcile(
        self,
        target: Any,
        execution_id: str,
        base_revision: str,
        returned_files: Mapping[str, bytes],
    ) -> WorkspaceSyncResult: ...


WorkspaceStorageBackend = WorkspaceStorage | AsyncWorkspaceStorage


def build_workspace_storage(repository=None) -> WorkspaceStorageBackend:
    backend = configured_workspace_storage_backend()
    if backend == "local":
        if _deployed_environment():
            raise WorkspaceStorageError("EPHEMERAL_LOCAL_STORAGE_FORBIDDEN")
        return LocalDurableWorkspaceStorage()
    if backend == "supabase":
        if repository is None:
            raise WorkspaceStorageError("SUPABASE_STORAGE_REPOSITORY_REQUIRED")
        from app.services.supabase_workspace_storage import SupabaseWorkspaceStorage

        return SupabaseWorkspaceStorage(repository)
    raise WorkspaceStorageError("WORKSPACE_STORAGE_BACKEND_INVALID")


def workspace_storage_readiness() -> dict[str, object]:
    backend = configured_workspace_storage_backend()
    enabled = os.getenv("NIE_ENGINEERING_MODE_ENABLED", "false").strip().casefold() in {"1", "true", "yes", "on"}
    runner = os.getenv("NIE_ENGINEERING_RUNNER", "docker" if _deployed_environment() else "local").strip().casefold()
    if backend == "supabase":
        from app.services.supabase_workspace_storage import supabase_storage_readiness

        result = supabase_storage_readiness()
        compatible = runner == "vercel_sandbox"
        result["enabled"] = enabled
        result["compatible_runner"] = compatible
        result["safe_for_production"] = bool(result.get("configured") and compatible)
        if not compatible:
            result["reason"] = "ENGINEERING_STORAGE_RUNNER_INCOMPATIBLE"
        return result
    if backend == "local":
        safe = not _deployed_environment()
        return {
            "backend": "local",
            "configured": safe or bool(os.getenv("NIE_ENGINEERING_WORKSPACE_ROOT", "").strip()),
            "reachable": None,
            "enabled": enabled,
            "safe_for_production": safe,
            "reason": None if safe else "EPHEMERAL_LOCAL_STORAGE_FORBIDDEN",
        }
    return {
        "backend": backend,
        "configured": False,
        "reachable": False,
        "enabled": enabled,
        "safe_for_production": False,
        "reason": "WORKSPACE_STORAGE_BACKEND_INVALID",
    }


def validate_workspace_storage_configuration() -> None:
    readiness = workspace_storage_readiness()
    if readiness.get("enabled") and (not readiness.get("configured") or not readiness.get("safe_for_production")):
        raise RuntimeError(str(readiness.get("reason") or "ENGINEERING_STORAGE_NOT_READY"))


async def probe_workspace_storage() -> dict[str, object]:
    readiness = workspace_storage_readiness()
    if readiness.get("backend") != "supabase" or not readiness.get("configured"):
        return readiness
    from app.services.supabase_workspace_storage import probe_supabase_storage

    probed = await probe_supabase_storage()
    probed["enabled"] = readiness.get("enabled", False)
    compatible = bool(readiness.get("compatible_runner"))
    probed["compatible_runner"] = compatible
    probed["safe_for_production"] = bool(probed.get("configured") and probed.get("reachable") and compatible)
    if not compatible:
        probed["reason"] = "ENGINEERING_STORAGE_RUNNER_INCOMPATIBLE"
    elif probed.get("configured") and not probed.get("reachable"):
        probed["reason"] = "SUPABASE_STORAGE_PRIVATE_BUCKET_UNAVAILABLE"
    return probed


async def capture_workspace(storage: WorkspaceStorageBackend, target) -> WorkspaceSnapshot:
    if isinstance(storage, AsyncWorkspaceStorage):
        return await storage.capture(target)
    root_path = str(getattr(target, "root_path", target))
    return await asyncio.to_thread(storage.capture, root_path)


async def reconcile_workspace(
    storage: WorkspaceStorageBackend,
    target,
    execution_id: str,
    base_revision: str,
    returned_files: Mapping[str, bytes],
) -> WorkspaceSyncResult:
    if isinstance(storage, AsyncWorkspaceStorage):
        return await storage.reconcile(target, execution_id, base_revision, returned_files)
    root_path = str(getattr(target, "root_path", target))
    return await asyncio.to_thread(storage.reconcile, root_path, base_revision, returned_files)


class LocalDurableWorkspaceStorage(WorkspaceStorage):
    """Durable-volume implementation with content revisions and atomic writes.

    The lock is process-local. Staging is deliberately configured with one API
    worker; a future multi-worker deployment must replace this lock with a
    shared lock while retaining this optimistic content revision check.
    """

    IGNORED_DIRECTORY_NAMES = {
        ".nie-tmp",
        ".nie-trash",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "__pycache__",
        "node_modules",
        ".venv",
        "venv",
    }
    _locks_guard = threading.Lock()
    _locks: dict[str, threading.RLock] = {}

    def __init__(self) -> None:
        self.max_file_bytes = self._positive_setting("NIE_ENGINEERING_MAX_FILE_BYTES", 1_000_000)
        self.max_workspace_bytes = self._positive_setting("NIE_ENGINEERING_MAX_WORKSPACE_BYTES", 100_000_000)
        self.max_file_count = self._positive_setting("NIE_ENGINEERING_MAX_FILE_COUNT", 2_000)

    @staticmethod
    def _positive_setting(name: str, default: int) -> int:
        try:
            value = int(os.getenv(name, str(default)))
        except ValueError as exc:
            raise WorkspaceStorageError(f"{name}_INVALID") from exc
        if value <= 0:
            raise WorkspaceStorageError(f"{name}_INVALID")
        return value

    @classmethod
    def _lock_for(cls, root: Path) -> threading.RLock:
        key = str(root)
        with cls._locks_guard:
            return cls._locks.setdefault(key, threading.RLock())

    @staticmethod
    def _validate_relative_path(value: str) -> str:
        if not value or "\x00" in value:
            raise WorkspaceStorageError("SANDBOX_RETURNED_PATH_INVALID")
        normalized = value.replace("\\", "/").strip("/")
        posix = PurePosixPath(normalized)
        windows = PureWindowsPath(value)
        if posix.is_absolute() or windows.is_absolute() or windows.drive or ".." in posix.parts:
            raise WorkspaceStorageError("SANDBOX_RETURNED_PATH_ESCAPE")
        if any(part in {"", "."} for part in posix.parts):
            raise WorkspaceStorageError("SANDBOX_RETURNED_PATH_INVALID")
        # Git metadata is required for the bounded local-only Git tool. User
        # secret filenames remain forbidden and remote Git operations remain
        # blocked by ToolPolicyEngine.
        if any(part != ".git" and WorkspacePathResolver._is_secret_name(part) for part in posix.parts):
            raise WorkspaceStorageError("SANDBOX_RETURNED_SECRET_FILE")
        return posix.as_posix()

    def _validate_returned_files(self, returned_files: Mapping[str, bytes]) -> dict[str, bytes]:
        if len(returned_files) > self.max_file_count:
            raise WorkspaceStorageError("SANDBOX_RETURNED_FILE_COUNT_EXCEEDED")
        validated: dict[str, bytes] = {}
        total = 0
        for raw_path, raw_content in returned_files.items():
            path = self._validate_relative_path(raw_path)
            if path in validated:
                raise WorkspaceStorageError("SANDBOX_RETURNED_DUPLICATE_PATH")
            if not isinstance(raw_content, bytes):
                raise WorkspaceStorageError("SANDBOX_RETURNED_FILE_INVALID")
            if len(raw_content) > self.max_file_bytes:
                raise WorkspaceStorageError("SANDBOX_RETURNED_FILE_TOO_LARGE")
            total += len(raw_content)
            if total > self.max_workspace_bytes:
                raise WorkspaceStorageError("SANDBOX_RETURNED_WORKSPACE_TOO_LARGE")
            validated[path] = raw_content
        return validated

    @staticmethod
    def _revision(files: Mapping[str, WorkspaceStoredFile]) -> str:
        digest = hashlib.sha256()
        for path in sorted(files):
            item = files[path]
            digest.update(path.encode("utf-8"))
            digest.update(b"\0")
            digest.update(str(item.size).encode("ascii"))
            digest.update(b"\0")
            digest.update(item.sha256.encode("ascii"))
            digest.update(b"\n")
        return digest.hexdigest()

    def capture(self, root_path: str) -> WorkspaceSnapshot:
        root = Path(root_path).resolve()
        if not root.is_dir():
            raise WorkspaceStorageError("WORKSPACE_ROOT_UNAVAILABLE")
        files: dict[str, WorkspaceStoredFile] = {}
        total = 0
        for current, directories, names in os.walk(root, topdown=True, followlinks=False):
            current_path = Path(current)
            kept_directories: list[str] = []
            for directory in directories:
                candidate = current_path / directory
                if directory in self.IGNORED_DIRECTORY_NAMES:
                    continue
                if candidate.is_symlink():
                    raise WorkspaceStorageError("WORKSPACE_SYMLINK_REJECTED")
                kept_directories.append(directory)
            directories[:] = kept_directories
            for name in names:
                candidate = current_path / name
                if candidate.is_symlink():
                    raise WorkspaceStorageError("WORKSPACE_SYMLINK_REJECTED")
                if not candidate.is_file():
                    raise WorkspaceStorageError("WORKSPACE_NON_REGULAR_FILE_REJECTED")
                relative = self._validate_relative_path(candidate.relative_to(root).as_posix())
                content = candidate.read_bytes()
                if len(content) > self.max_file_bytes:
                    raise WorkspaceStorageError("WORKSPACE_FILE_TOO_LARGE")
                total += len(content)
                if total > self.max_workspace_bytes:
                    raise WorkspaceStorageError("WORKSPACE_SIZE_LIMIT_EXCEEDED")
                files[relative] = WorkspaceStoredFile(
                    path=relative,
                    content=content,
                    sha256=hashlib.sha256(content).hexdigest(),
                )
                if len(files) > self.max_file_count:
                    raise WorkspaceStorageError("WORKSPACE_FILE_COUNT_EXCEEDED")
        return WorkspaceSnapshot(self._revision(files), files, total)

    @staticmethod
    def _atomic_write(path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temp_name = tempfile.mkstemp(prefix=".nie_sync_", suffix=".tmp", dir=str(path.parent))
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(content)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, path)
        except Exception:
            try:
                os.unlink(temp_name)
            except OSError:
                pass
            raise

    def reconcile(
        self,
        root_path: str,
        base_revision: str,
        returned_files: Mapping[str, bytes],
    ) -> WorkspaceSyncResult:
        validated = self._validate_returned_files(returned_files)
        root = Path(root_path).resolve()
        with self._lock_for(root):
            current = self.capture(str(root))
            if current.revision != base_revision:
                raise WorkspaceStorageError("WORKSPACE_SYNC_CONFLICT")

            returned_hashes = {path: hashlib.sha256(content).hexdigest() for path, content in validated.items()}
            created = sorted(set(validated) - set(current.files))
            deleted = sorted(set(current.files) - set(validated))
            updated = sorted(
                path
                for path in set(validated) & set(current.files)
                if returned_hashes[path] != current.files[path].sha256
            )
            if not created and not deleted and not updated:
                return WorkspaceSyncResult(current.revision, current.revision, ())

            transaction_root = root / ".nie-tmp" / f"sync-{uuid.uuid4().hex}"
            backups = transaction_root / "backups"
            staged = transaction_root / "staged"
            backups.mkdir(parents=True, exist_ok=False)
            staged.mkdir(parents=True, exist_ok=False)
            for path in created + updated:
                staged_path = staged / Path(path)
                self._atomic_write(staged_path, validated[path])

            applied: list[tuple[str, str]] = []
            changes: list[dict[str, object]] = []
            try:
                for path in updated:
                    target = root / Path(path)
                    backup = backups / Path(path)
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(target, backup)
                    self._atomic_write(target, (staged / Path(path)).read_bytes())
                    applied.append(("UPDATED", path))
                    changes.append(
                        {
                            "path": path,
                            "operation": "UPDATED",
                            "bytes_before": current.files[path].size,
                            "bytes_after": len(validated[path]),
                            "sha256": returned_hashes[path],
                        }
                    )
                for path in created:
                    target = root / Path(path)
                    self._atomic_write(target, (staged / Path(path)).read_bytes())
                    applied.append(("CREATED", path))
                    changes.append(
                        {
                            "path": path,
                            "operation": "CREATED",
                            "bytes_before": 0,
                            "bytes_after": len(validated[path]),
                            "sha256": returned_hashes[path],
                        }
                    )
                for path in deleted:
                    target = root / Path(path)
                    backup = backups / Path(path)
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    os.replace(target, backup)
                    applied.append(("DELETED", path))
                    changes.append(
                        {
                            "path": path,
                            "operation": "DELETED",
                            "bytes_before": current.files[path].size,
                            "bytes_after": 0,
                            "sha256": None,
                        }
                    )
                after = self.capture(str(root))
            except Exception as exc:
                for operation, path in reversed(applied):
                    target = root / Path(path)
                    backup = backups / Path(path)
                    try:
                        if operation == "CREATED":
                            target.unlink(missing_ok=True)
                        elif backup.exists():
                            target.parent.mkdir(parents=True, exist_ok=True)
                            os.replace(backup, target)
                    except OSError:
                        pass
                raise WorkspaceStorageError("WORKSPACE_SYNC_APPLY_FAILED") from exc
            finally:
                shutil.rmtree(transaction_root, ignore_errors=True)

            return WorkspaceSyncResult(current.revision, after.revision, tuple(changes))
