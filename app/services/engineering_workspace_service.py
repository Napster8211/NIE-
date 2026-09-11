"""Owner-scoped persistent workspace and confined filesystem services."""

import hashlib
import os
import re
import shutil
import tempfile
import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path, PureWindowsPath
from typing import Any

from app.models.engineering_workspace import EngineeringWorkspace
from app.repositories.engineering_workspace_repository import EngineeringWorkspaceRepository
from app.schemas.engineering_workspace import WorkspaceCreate, WorkspaceResponse, WorkspaceUpdate
from app.services.runtime_environment import is_production


class WorkspaceError(Exception):
    def __init__(self, code: str, http_status: int = 400):
        super().__init__(code)
        self.code = code
        self.http_status = http_status


@dataclass
class FileOperationResult:
    data: dict[str, Any]
    changed_files: list[str] = field(default_factory=list)
    change: dict[str, Any] | None = None
    changes: list[dict[str, Any]] = field(default_factory=list)
    provider_metadata: dict[str, Any] = field(default_factory=dict)
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.strip().casefold()).strip("-")
    return (slug or "workspace")[:120]


def workspace_storage_root() -> Path:
    configured = os.getenv("NIE_ENGINEERING_WORKSPACE_ROOT", "").strip()
    if not configured:
        if is_production():
            raise WorkspaceError("WORKSPACE_STORAGE_NOT_CONFIGURED", 503)
        configured = str(Path(__file__).resolve().parents[2] / "storage" / "engineering_workspaces")
    root = Path(configured).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    return root


class WorkspaceManager:
    def __init__(self, repository: EngineeringWorkspaceRepository):
        self.repository = repository

    async def create(self, owner_id: str, request: WorkspaceCreate) -> WorkspaceResponse:
        if request.conversation_id and not await self.repository.conversation_owned_by_user(
            request.conversation_id, owner_id
        ):
            raise WorkspaceError("CONVERSATION_NOT_FOUND", 404)
        root = workspace_storage_root()
        owner_bucket = hashlib.sha256(owner_id.encode("utf-8")).hexdigest()[:20]
        workspace_id = f"ews_{uuid.uuid4().hex}"
        slug = _slugify(request.name)
        configured_runner = (
            os.getenv("NIE_ENGINEERING_RUNNER", "docker" if is_production() else "local").strip().casefold()
        )
        runtime_type = {
            "docker": "DOCKER",
            "vercel_sandbox": "VERCEL_SANDBOX",
        }.get(configured_runner, "LOCAL_DEVELOPMENT")
        workspace_root = (root / owner_bucket / workspace_id).resolve()
        if root not in workspace_root.parents:
            raise WorkspaceError("WORKSPACE_PATH_INVALID")
        workspace_root.mkdir(parents=True, exist_ok=False)
        record = EngineeringWorkspace(
            workspace_id=workspace_id,
            owner_id=owner_id,
            name=request.name.strip(),
            slug=slug,
            root_path=str(workspace_root),
            runtime_type=runtime_type,
            metadata_json=request.metadata,
        )
        try:
            await self.repository.create_workspace(record, request.conversation_id)
        except Exception:
            shutil.rmtree(workspace_root, ignore_errors=True)
            raise
        return await self.response(record)

    async def require(self, workspace_id: str, owner_id: str, *, active: bool = True) -> EngineeringWorkspace:
        workspace = await self.repository.get_workspace(workspace_id, owner_id)
        if workspace is None:
            raise WorkspaceError("WORKSPACE_NOT_FOUND", 404)
        if active and workspace.status != "ACTIVE":
            raise WorkspaceError("WORKSPACE_NOT_ACTIVE", 409)
        root = Path(workspace.root_path).resolve()
        storage_root = workspace_storage_root()
        if storage_root != root and storage_root not in root.parents:
            raise WorkspaceError("WORKSPACE_ROOT_INVALID", 500)
        if not root.is_dir():
            raise WorkspaceError("WORKSPACE_ROOT_UNAVAILABLE", 503)
        return workspace

    async def list(self, owner_id: str, include_archived: bool = False) -> list[WorkspaceResponse]:
        records = await self.repository.list_workspaces(owner_id, include_archived)
        return [await self.response(record) for record in records]

    async def update(self, workspace_id: str, owner_id: str, request: WorkspaceUpdate) -> WorkspaceResponse:
        workspace = await self.require(workspace_id, owner_id, active=False)
        if request.name is not None:
            workspace.name = request.name.strip()
            workspace.slug = _slugify(request.name)
        if request.status is not None:
            workspace.status = request.status.value
            workspace.archived_at = utc_now() if request.status.value == "ARCHIVED" else None
        if request.metadata is not None:
            workspace.metadata_json = request.metadata
        await self.repository.update_workspace(workspace)
        return await self.response(workspace)

    async def attach(self, workspace_id: str, conversation_id: str, owner_id: str) -> WorkspaceResponse:
        workspace = await self.require(workspace_id, owner_id)
        if not await self.repository.conversation_owned_by_user(conversation_id, owner_id):
            raise WorkspaceError("CONVERSATION_NOT_FOUND", 404)
        await self.repository.attach_conversation(workspace_id, conversation_id, owner_id)
        await self.repository.update_workspace(workspace)
        return await self.response(workspace)

    async def response(self, workspace: EngineeringWorkspace) -> WorkspaceResponse:
        return WorkspaceResponse(
            workspace_id=workspace.workspace_id,
            name=workspace.name,
            slug=workspace.slug,
            status=workspace.status,
            runtime_type=workspace.runtime_type,
            created_at=workspace.created_at,
            updated_at=workspace.updated_at,
            last_activity_at=workspace.last_activity_at,
            metadata=workspace.metadata_json or {},
            conversation_ids=await self.repository.conversation_ids(workspace.workspace_id, workspace.owner_id),
        )


class WorkspacePathResolver:
    BLOCKED_NAMES = {
        ".git",
        ".nie-trash",
        ".nie-tmp",
        ".env",
        ".env.local",
        ".env.production",
        "id_rsa",
        "id_ed25519",
        "credentials",
        "credentials.json",
        ".npmrc",
        ".pypirc",
    }

    @classmethod
    def _is_secret_name(cls, value: str) -> bool:
        name = value.casefold()
        return name in cls.BLOCKED_NAMES or name.startswith(".env.") or name.endswith((".pem", ".key", ".p12", ".pfx"))

    def __init__(self, root_path: str):
        self.root = Path(root_path).resolve()

    def resolve(self, relative_path: str, *, allow_root: bool = False, secret_access: bool = False) -> tuple[Path, str]:
        value = relative_path or ""
        if "\x00" in value:
            raise WorkspaceError("PATH_NULL_BYTE")
        if Path(value).is_absolute() or PureWindowsPath(value).is_absolute() or PureWindowsPath(value).drive:
            raise WorkspaceError("ABSOLUTE_PATH_REJECTED")
        normalized = value.replace("\\", "/").strip("/")
        if any(part == ".." for part in normalized.split("/")):
            raise WorkspaceError("PATH_TRAVERSAL_REJECTED")
        candidate = (self.root / normalized).resolve(strict=False)
        if candidate != self.root and self.root not in candidate.parents:
            raise WorkspaceError("PATH_ESCAPE_REJECTED")
        if candidate == self.root and not allow_root:
            raise WorkspaceError("WORKSPACE_ROOT_OPERATION_REJECTED")
        if not secret_access and any(self._is_secret_name(part) for part in candidate.relative_to(self.root).parts):
            raise WorkspaceError("SECRET_FILE_ACCESS_REJECTED", 403)
        current = candidate
        while current != self.root:
            if current.exists() and current.is_symlink():
                resolved = current.resolve()
                if self.root not in resolved.parents and resolved != self.root:
                    raise WorkspaceError("SYMLINK_ESCAPE_REJECTED")
            current = current.parent
        return candidate, candidate.relative_to(self.root).as_posix() if candidate != self.root else ""


class FileSystemTool:
    def __init__(self, root_path: str):
        self.resolver = WorkspacePathResolver(root_path)
        self.root = self.resolver.root
        self.max_file_bytes = int(os.getenv("NIE_ENGINEERING_MAX_FILE_BYTES", "1000000"))
        self.max_workspace_bytes = int(os.getenv("NIE_ENGINEERING_MAX_WORKSPACE_BYTES", "100000000"))

    def _workspace_size(self) -> int:
        total = 0
        for entry in self.root.rglob("*"):
            if entry.is_file() and not entry.is_symlink():
                total += entry.stat().st_size
                if total > self.max_workspace_bytes:
                    break
        return total

    def _validate_content(self, content: str, existing_bytes: int = 0) -> bytes:
        encoded = content.encode("utf-8")
        if len(encoded) > self.max_file_bytes:
            raise WorkspaceError("FILE_SIZE_LIMIT_EXCEEDED", 413)
        projected = self._workspace_size() - existing_bytes + len(encoded)
        if projected > self.max_workspace_bytes:
            raise WorkspaceError("WORKSPACE_SIZE_LIMIT_EXCEEDED", 413)
        return encoded

    @staticmethod
    def _atomic_write(path: Path, content: bytes) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temp_name = tempfile.mkstemp(prefix=".nie_workspace_", suffix=".tmp", dir=str(path.parent))
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

    def list_files(self, path: str = "") -> FileOperationResult:
        target, relative = self.resolver.resolve(path, allow_root=True)
        if not target.is_dir():
            raise WorkspaceError("DIRECTORY_NOT_FOUND", 404)
        entries = []
        for child in sorted(target.iterdir(), key=lambda item: (not item.is_dir(), item.name.casefold()))[:500]:
            if child.name in {".nie-trash", ".nie-tmp"}:
                continue
            if child.is_symlink():
                kind = "symlink"
            elif child.is_dir():
                kind = "directory"
            else:
                kind = "file"
            entries.append(
                {
                    "name": child.name,
                    "path": child.relative_to(self.root).as_posix(),
                    "type": kind,
                    "size": child.stat().st_size if kind == "file" else None,
                }
            )
        return FileOperationResult({"path": relative, "entries": entries})

    def read_file(self, path: str) -> FileOperationResult:
        target, relative = self.resolver.resolve(path)
        if not target.is_file() or target.is_symlink():
            raise WorkspaceError("FILE_NOT_FOUND", 404)
        size = target.stat().st_size
        if size > self.max_file_bytes:
            raise WorkspaceError("FILE_SIZE_LIMIT_EXCEEDED", 413)
        content = target.read_text(encoding="utf-8", errors="replace")
        return FileOperationResult({"path": relative, "content": content, "bytes": size})

    def metadata(self, path: str) -> FileOperationResult:
        target, relative = self.resolver.resolve(path, allow_root=True)
        if not target.exists():
            raise WorkspaceError("PATH_NOT_FOUND", 404)
        stat = target.stat()
        return FileOperationResult(
            {
                "path": relative,
                "type": "directory" if target.is_dir() else "file",
                "size": stat.st_size,
                "modified_at": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).isoformat(),
                "symlink": target.is_symlink(),
            }
        )

    def create_file(self, path: str, content: str, overwrite: bool = False) -> FileOperationResult:
        target, relative = self.resolver.resolve(path)
        if target.exists() and (not overwrite or not target.is_file() or target.is_symlink()):
            raise WorkspaceError("FILE_ALREADY_EXISTS", 409)
        before = target.stat().st_size if target.exists() else 0
        encoded = self._validate_content(content, before)
        self._atomic_write(target, encoded)
        digest = hashlib.sha256(encoded).hexdigest()
        return FileOperationResult(
            {"path": relative, "bytes_changed": abs(len(encoded) - before), "sha256": digest},
            [relative],
            {
                "path": relative,
                "operation": "UPDATED" if before else "CREATED",
                "bytes_before": before,
                "bytes_after": len(encoded),
                "sha256": digest,
            },
        )

    def apply_patch(self, path: str, patches: Iterable[Any]) -> FileOperationResult:
        target, relative = self.resolver.resolve(path)
        if not target.is_file() or target.is_symlink():
            raise WorkspaceError("FILE_NOT_FOUND", 404)
        before_content = target.read_text(encoding="utf-8")
        content = before_content
        for patch in patches:
            old = patch.old if hasattr(patch, "old") else patch["old"]
            new = patch.new if hasattr(patch, "new") else patch["new"]
            expected = (
                patch.expected_occurrences
                if hasattr(patch, "expected_occurrences")
                else patch.get("expected_occurrences", 1)
            )
            actual = content.count(old)
            if actual != expected:
                raise WorkspaceError(f"PATCH_CONTEXT_MISMATCH: expected {expected}, found {actual}", 409)
            content = content.replace(old, new)
        before = len(before_content.encode("utf-8"))
        encoded = self._validate_content(content, before)
        self._atomic_write(target, encoded)
        digest = hashlib.sha256(encoded).hexdigest()
        return FileOperationResult(
            {"path": relative, "bytes_changed": abs(len(encoded) - before), "sha256": digest},
            [relative],
            {
                "path": relative,
                "operation": "UPDATED",
                "bytes_before": before,
                "bytes_after": len(encoded),
                "sha256": digest,
            },
        )

    def create_directory(self, path: str) -> FileOperationResult:
        target, relative = self.resolver.resolve(path)
        target.mkdir(parents=True, exist_ok=False)
        return FileOperationResult(
            {"path": relative},
            [relative],
            {"path": relative, "operation": "DIRECTORY_CREATED", "bytes_before": 0, "bytes_after": 0, "sha256": None},
        )

    def rename(self, source: str, destination: str, overwrite: bool = False) -> FileOperationResult:
        source_path, source_relative = self.resolver.resolve(source)
        destination_path, destination_relative = self.resolver.resolve(destination)
        if not source_path.exists() or source_path.is_symlink():
            raise WorkspaceError("SOURCE_NOT_FOUND", 404)
        if destination_path.exists() and not overwrite:
            raise WorkspaceError("DESTINATION_EXISTS", 409)
        destination_path.parent.mkdir(parents=True, exist_ok=True)
        os.replace(source_path, destination_path)
        return FileOperationResult(
            {"source": source_relative, "destination": destination_relative}, [source_relative, destination_relative]
        )

    def delete_file(self, path: str) -> FileOperationResult:
        target, relative = self.resolver.resolve(path)
        if not target.is_file() or target.is_symlink():
            raise WorkspaceError("FILE_NOT_FOUND", 404)
        trash = self.root / ".nie-trash" / uuid.uuid4().hex
        trash.mkdir(parents=True, exist_ok=False)
        destination = trash / target.name
        before = target.stat().st_size
        os.replace(target, destination)
        return FileOperationResult(
            {"path": relative, "recoverable": True},
            [relative],
            {"path": relative, "operation": "SOFT_DELETED", "bytes_before": before, "bytes_after": 0, "sha256": None},
        )


class SearchTool:
    SKIP_PARTS = {".git", ".nie-trash", "node_modules", ".venv", "venv", "__pycache__"}

    def __init__(self, root_path: str):
        self.resolver = WorkspacePathResolver(root_path)
        self.root = self.resolver.root

    def _files(self, path: str) -> Iterable[Path]:
        target, _ = self.resolver.resolve(path, allow_root=True)
        if not target.is_dir():
            raise WorkspaceError("DIRECTORY_NOT_FOUND", 404)
        for item in target.rglob("*"):
            relative = item.relative_to(self.root)
            if any(part in self.SKIP_PARTS for part in relative.parts):
                continue
            if item.is_file() and not item.is_symlink():
                yield item

    def filenames(self, query: str, path: str = "", max_results: int = 100) -> FileOperationResult:
        needle = query.casefold()
        matches = [
            item.relative_to(self.root).as_posix() for item in self._files(path) if needle in item.name.casefold()
        ]
        return FileOperationResult(
            {"query": query, "matches": matches[:max_results], "truncated": len(matches) > max_results}
        )

    def contents(
        self, query: str, path: str = "", max_results: int = 100, case_sensitive: bool = False
    ) -> FileOperationResult:
        needle = query if case_sensitive else query.casefold()
        matches = []
        for item in self._files(path):
            if item.stat().st_size > 1_000_000:
                continue
            try:
                for number, line in enumerate(item.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
                    candidate = line if case_sensitive else line.casefold()
                    if needle in candidate:
                        matches.append(
                            {"path": item.relative_to(self.root).as_posix(), "line": number, "preview": line[:500]}
                        )
                        if len(matches) >= max_results:
                            return FileOperationResult({"query": query, "matches": matches, "truncated": True})
            except OSError:
                continue
        return FileOperationResult({"query": query, "matches": matches, "truncated": False})
