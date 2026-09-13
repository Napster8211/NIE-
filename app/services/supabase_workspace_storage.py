"""Private Supabase Storage adapter with PostgreSQL-authoritative manifests."""

from __future__ import annotations

import asyncio
import hashlib
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import quote, urlparse

import httpx

from app.repositories.engineering_workspace_repository import EngineeringWorkspaceRepository
from app.services.engineering_workspace_storage import (
    LocalDurableWorkspaceStorage,
    WorkspaceSnapshot,
    WorkspaceStorageError,
    WorkspaceStoredFile,
    WorkspaceSyncResult,
)
from app.services.runtime_environment import runtime_environment

EMPTY_MANIFEST_SHA256 = hashlib.sha256(b"").hexdigest()


@dataclass(frozen=True)
class SupabaseStorageSettings:
    url: str
    service_role_key: str
    bucket: str
    timeout_seconds: int

    @classmethod
    def from_environment(cls) -> SupabaseStorageSettings:
        url = os.getenv("SUPABASE_URL", "").strip().rstrip("/")
        key = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "").strip()
        bucket = os.getenv("NIE_ENGINEERING_STORAGE_BUCKET", "engineering-workspaces").strip()
        missing = [name for name, value in (("SUPABASE_URL", url), ("SUPABASE_SERVICE_ROLE_KEY", key)) if not value]
        if missing:
            raise WorkspaceStorageError("SUPABASE_STORAGE_CREDENTIALS_MISSING")
        parsed = urlparse(url)
        deployed = runtime_environment() in {"production", "prod", "staging", "stage"}
        if parsed.scheme not in ({"https"} if deployed else {"http", "https"}) or not parsed.netloc:
            raise WorkspaceStorageError("SUPABASE_URL_INVALID")
        if any(value.startswith("<") and value.endswith(">") for value in (url, key)):
            raise WorkspaceStorageError("SUPABASE_STORAGE_CREDENTIALS_PLACEHOLDER")
        if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,62}[a-z0-9]", bucket):
            raise WorkspaceStorageError("SUPABASE_STORAGE_BUCKET_INVALID")
        try:
            timeout = int(os.getenv("NIE_ENGINEERING_STORAGE_TIMEOUT_SECONDS", "30"))
        except ValueError as exc:
            raise WorkspaceStorageError("NIE_ENGINEERING_STORAGE_TIMEOUT_SECONDS_INVALID") from exc
        if timeout < 5 or timeout > 120:
            raise WorkspaceStorageError("NIE_ENGINEERING_STORAGE_TIMEOUT_SECONDS_INVALID")
        return cls(url=url, service_role_key=key, bucket=bucket, timeout_seconds=timeout)


class SupabaseObjectClient(Protocol):
    async def upload(self, object_key: str, content: bytes) -> None: ...

    async def download(self, object_key: str) -> bytes: ...

    async def delete(self, object_keys: list[str]) -> None: ...

    async def list(self, prefix: str) -> list[str]: ...

    async def check_bucket(self) -> bool: ...


class SupabaseStorageHttpClient:
    """Minimal server-side client for the documented Supabase Storage API."""

    def __init__(self, settings: SupabaseStorageSettings, *, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings
        self._transport = transport
        self._headers = {
            "apikey": settings.service_role_key,
            "Authorization": f"Bearer {settings.service_role_key}",
        }

    def _object_url(self, object_key: str, *, authenticated: bool = False) -> str:
        safe_key = quote(object_key, safe="/")
        route = "object/authenticated" if authenticated else "object"
        return f"{self.settings.url}/storage/v1/{route}/{quote(self.settings.bucket, safe='')}/{safe_key}"

    async def upload(self, object_key: str, content: bytes) -> None:
        headers = {**self._headers, "Content-Type": "application/octet-stream", "x-upsert": "false"}
        try:
            async with httpx.AsyncClient(timeout=self.settings.timeout_seconds, transport=self._transport) as client:
                response = await client.post(self._object_url(object_key), headers=headers, content=content)
        except httpx.HTTPError:
            raise WorkspaceStorageError("SUPABASE_STORAGE_UPLOAD_FAILED") from None
        if response.status_code in {200, 201}:
            return
        if response.status_code in {400, 409}:
            try:
                existing = await self.download(object_key)
            except WorkspaceStorageError:
                existing = None
            if existing is not None and hashlib.sha256(existing).digest() == hashlib.sha256(content).digest():
                return
        raise WorkspaceStorageError("SUPABASE_STORAGE_UPLOAD_FAILED")

    async def download(self, object_key: str) -> bytes:
        try:
            async with httpx.AsyncClient(timeout=self.settings.timeout_seconds, transport=self._transport) as client:
                response = await client.get(self._object_url(object_key, authenticated=True), headers=self._headers)
        except httpx.HTTPError:
            raise WorkspaceStorageError("SUPABASE_STORAGE_DOWNLOAD_FAILED") from None
        if response.status_code != 200:
            raise WorkspaceStorageError("SUPABASE_STORAGE_DOWNLOAD_FAILED")
        return response.content

    async def delete(self, object_keys: list[str]) -> None:
        if not object_keys:
            return
        url = f"{self.settings.url}/storage/v1/object/{quote(self.settings.bucket, safe='')}"
        try:
            async with httpx.AsyncClient(timeout=self.settings.timeout_seconds, transport=self._transport) as client:
                response = await client.request("DELETE", url, headers=self._headers, json={"prefixes": object_keys})
        except httpx.HTTPError:
            raise WorkspaceStorageError("SUPABASE_STORAGE_DELETE_FAILED") from None
        if response.status_code not in {200, 204}:
            raise WorkspaceStorageError("SUPABASE_STORAGE_DELETE_FAILED")

    async def list(self, prefix: str) -> list[str]:
        url = f"{self.settings.url}/storage/v1/object/list/{quote(self.settings.bucket, safe='')}"
        payload = {"prefix": prefix, "limit": 1000, "offset": 0, "sortBy": {"column": "name", "order": "asc"}}
        try:
            async with httpx.AsyncClient(timeout=self.settings.timeout_seconds, transport=self._transport) as client:
                response = await client.post(url, headers=self._headers, json=payload)
        except httpx.HTTPError:
            raise WorkspaceStorageError("SUPABASE_STORAGE_LIST_FAILED") from None
        if response.status_code != 200:
            raise WorkspaceStorageError("SUPABASE_STORAGE_LIST_FAILED")
        try:
            body = response.json()
        except ValueError:
            raise WorkspaceStorageError("SUPABASE_STORAGE_RESPONSE_INVALID") from None
        if not isinstance(body, list):
            raise WorkspaceStorageError("SUPABASE_STORAGE_RESPONSE_INVALID")
        return [f"{prefix.rstrip('/')}/{item['name']}" for item in body if isinstance(item, dict) and item.get("name")]

    async def check_bucket(self) -> bool:
        url = f"{self.settings.url}/storage/v1/bucket/{quote(self.settings.bucket, safe='')}"
        try:
            async with httpx.AsyncClient(
                timeout=min(self.settings.timeout_seconds, 10), transport=self._transport
            ) as client:
                response = await client.get(url, headers=self._headers)
        except httpx.HTTPError:
            return False
        if response.status_code != 200:
            return False
        try:
            body = response.json()
        except ValueError:
            return False
        return isinstance(body, dict) and body.get("public") is False


class SupabaseWorkspaceStorage:
    """Durable object storage whose manifest is committed transactionally in PostgreSQL."""

    is_async = True

    def __init__(
        self,
        repository: EngineeringWorkspaceRepository,
        *,
        client: SupabaseObjectClient | None = None,
        settings: SupabaseStorageSettings | None = None,
    ):
        self.repository = repository
        self.settings = settings or SupabaseStorageSettings.from_environment()
        self.client = client or SupabaseStorageHttpClient(self.settings)
        validator = LocalDurableWorkspaceStorage()
        self.max_file_bytes = validator.max_file_bytes
        self.max_workspace_bytes = validator.max_workspace_bytes
        self.max_file_count = validator.max_file_count
        self._validator = validator

    @staticmethod
    def owner_prefix(owner_id: str) -> str:
        return hashlib.sha256(owner_id.encode("utf-8")).hexdigest()[:40]

    @classmethod
    def object_key(cls, owner_id: str, workspace_id: str, execution_id: str, content_sha256: str) -> str:
        safe_workspace = re.sub(r"[^A-Za-z0-9_-]", "", workspace_id)[:160]
        safe_execution = re.sub(r"[^A-Za-z0-9_-]", "", execution_id)[:160]
        if not safe_workspace or not safe_execution or not re.fullmatch(r"[0-9a-f]{64}", content_sha256):
            raise WorkspaceStorageError("SUPABASE_STORAGE_OBJECT_KEY_INVALID")
        return (
            f"users/{cls.owner_prefix(owner_id)}/workspaces/{safe_workspace}/"
            f"executions/{safe_execution}/staged/{content_sha256}"
        )

    @staticmethod
    def _target(target: Any) -> tuple[str, str]:
        workspace_id = str(getattr(target, "workspace_id", ""))
        owner_id = str(getattr(target, "owner_id", ""))
        if not workspace_id or not owner_id:
            raise WorkspaceStorageError("SUPABASE_WORKSPACE_CONTEXT_REQUIRED")
        if str(getattr(target, "storage_backend", "")).upper() != "SUPABASE":
            raise WorkspaceStorageError("WORKSPACE_STORAGE_BACKEND_MISMATCH")
        return workspace_id, owner_id

    @staticmethod
    def _revision_token(revision: int, manifest_sha256: str) -> str:
        return f"{revision}:{manifest_sha256 or EMPTY_MANIFEST_SHA256}"

    @staticmethod
    def _parse_revision(token: str) -> tuple[int, str]:
        try:
            raw_revision, manifest_sha256 = token.split(":", 1)
            revision = int(raw_revision)
        except (TypeError, ValueError) as exc:
            raise WorkspaceStorageError("WORKSPACE_REVISION_INVALID") from exc
        if revision < 0 or not re.fullmatch(r"[0-9a-f]{64}", manifest_sha256):
            raise WorkspaceStorageError("WORKSPACE_REVISION_INVALID")
        return revision, manifest_sha256

    @staticmethod
    def _manifest_hash(files: Mapping[str, tuple[str, int]]) -> str:
        digest = hashlib.sha256()
        for path in sorted(files):
            content_sha256, size = files[path]
            digest.update(path.encode("utf-8"))
            digest.update(b"\0")
            digest.update(str(size).encode("ascii"))
            digest.update(b"\0")
            digest.update(content_sha256.encode("ascii"))
            digest.update(b"\n")
        return digest.hexdigest()

    async def capture(self, target: Any) -> WorkspaceSnapshot:
        workspace_id, owner_id = self._target(target)
        revision, stored_manifest_hash, records = await self.repository.workspace_storage_state(workspace_id, owner_id)
        if len(records) > self.max_file_count:
            raise WorkspaceStorageError("WORKSPACE_FILE_COUNT_EXCEEDED")
        files: dict[str, WorkspaceStoredFile] = {}
        total = 0
        metadata: dict[str, tuple[str, int]] = {}
        for record in records:
            path = self._validator._validate_relative_path(str(record.logical_path))
            size = int(record.size_bytes)
            if size < 0 or size > self.max_file_bytes:
                raise WorkspaceStorageError("WORKSPACE_FILE_TOO_LARGE")
            total += size
            if total > self.max_workspace_bytes:
                raise WorkspaceStorageError("WORKSPACE_SIZE_LIMIT_EXCEEDED")
            content = await self.client.download(str(record.storage_object_key))
            digest = hashlib.sha256(content).hexdigest()
            if len(content) != size or digest != record.content_sha256:
                raise WorkspaceStorageError("SUPABASE_STORAGE_CONTENT_INTEGRITY_FAILED")
            files[path] = WorkspaceStoredFile(path=path, content=content, sha256=digest)
            metadata[path] = (digest, size)
        actual_hash = self._manifest_hash(metadata)
        expected_hash = stored_manifest_hash or EMPTY_MANIFEST_SHA256
        if actual_hash != expected_hash:
            raise WorkspaceStorageError("WORKSPACE_MANIFEST_INTEGRITY_FAILED")
        return WorkspaceSnapshot(self._revision_token(revision, actual_hash), files, total)

    async def reconcile(
        self,
        target: Any,
        execution_id: str,
        base_revision: str,
        returned_files: Mapping[str, bytes],
    ) -> WorkspaceSyncResult:
        workspace_id, owner_id = self._target(target)
        expected_revision, expected_hash = self._parse_revision(base_revision)
        validated = self._validator._validate_returned_files(returned_files)
        current_revision, current_hash, records = await self.repository.workspace_storage_state(workspace_id, owner_id)
        normalized_current_hash = current_hash or EMPTY_MANIFEST_SHA256
        if current_revision != expected_revision or normalized_current_hash != expected_hash:
            raise WorkspaceStorageError("WORKSPACE_SYNC_CONFLICT")
        current = {str(item.logical_path): item for item in records}
        returned_hashes = {path: hashlib.sha256(content).hexdigest() for path, content in validated.items()}
        created = sorted(set(validated) - set(current))
        deleted = sorted(set(current) - set(validated))
        updated = sorted(
            path for path in set(validated) & set(current) if returned_hashes[path] != str(current[path].content_sha256)
        )
        if not created and not deleted and not updated:
            return WorkspaceSyncResult(base_revision, base_revision, ())

        changes: list[dict[str, object]] = []
        for path in created:
            changes.append(
                {
                    "path": path,
                    "operation": "CREATED",
                    "bytes_before": 0,
                    "bytes_after": len(validated[path]),
                    "sha256": returned_hashes[path],
                }
            )
        for path in updated:
            changes.append(
                {
                    "path": path,
                    "operation": "UPDATED",
                    "bytes_before": int(current[path].size_bytes),
                    "bytes_after": len(validated[path]),
                    "sha256": returned_hashes[path],
                }
            )
        for path in deleted:
            changes.append(
                {
                    "path": path,
                    "operation": "DELETED",
                    "bytes_before": int(current[path].size_bytes),
                    "bytes_after": 0,
                    "sha256": None,
                }
            )

        planned: dict[str, dict[str, Any]] = {}
        for path in created + updated:
            digest = returned_hashes[path]
            object_key = self.object_key(owner_id, workspace_id, execution_id, digest)
            planned.setdefault(
                object_key,
                {
                    "object_key": object_key,
                    "sha256": digest,
                    "size": len(validated[path]),
                },
            )
        try:
            await self.repository.record_pending_staged_objects(
                workspace_id=workspace_id,
                owner_id=owner_id,
                execution_id=execution_id,
                objects=list(planned.values()),
            )
        except Exception:
            raise WorkspaceStorageError("WORKSPACE_STAGING_RECORD_FAILED") from None

        uploaded_keys: set[str] = set()
        try:
            for path in created + updated:
                digest = returned_hashes[path]
                object_key = self.object_key(owner_id, workspace_id, execution_id, digest)
                if object_key not in uploaded_keys:
                    # Track before awaiting: cancellation can occur after the
                    # provider accepts bytes but before its response arrives.
                    await self.client.upload(object_key, validated[path])
                    uploaded_keys.add(object_key)

            manifest: list[dict[str, Any]] = []
            metadata: dict[str, tuple[str, int]] = {}
            for path in sorted(validated):
                digest = returned_hashes[path]
                size = len(validated[path])
                if path in current and path not in updated:
                    object_key = str(current[path].storage_object_key)
                    responsible_execution_id = getattr(current[path], "execution_id", None)
                else:
                    object_key = self.object_key(owner_id, workspace_id, execution_id, digest)
                    responsible_execution_id = execution_id
                manifest.append(
                    {
                        "path": path,
                        "object_key": object_key,
                        "sha256": digest,
                        "size": size,
                        "execution_id": responsible_execution_id,
                    }
                )
                metadata[path] = (digest, size)
            manifest_hash = self._manifest_hash(metadata)
            try:
                new_revision = await self.repository.commit_workspace_manifest(
                    workspace_id=workspace_id,
                    owner_id=owner_id,
                    execution_id=execution_id,
                    expected_revision=expected_revision,
                    expected_manifest_sha256=expected_hash,
                    manifest_sha256=manifest_hash,
                    files=manifest,
                    changes=changes,
                    staged_objects=list(planned.values()),
                )
            except ValueError as exc:
                if str(exc) == "WORKSPACE_SYNC_CONFLICT":
                    raise WorkspaceStorageError("WORKSPACE_SYNC_CONFLICT") from exc
                raise
            except Exception:
                # A lost database acknowledgement can occur after PostgreSQL
                # committed. Re-read the authoritative revision before deciding
                # whether uploaded objects are safe to remove.
                try:
                    observed_revision, observed_hash, _ = await self.repository.workspace_storage_state(
                        workspace_id, owner_id
                    )
                except Exception:
                    raise WorkspaceStorageError("WORKSPACE_SYNC_COMMIT_STATUS_UNKNOWN") from None
                observed_hash = observed_hash or EMPTY_MANIFEST_SHA256
                if observed_revision == expected_revision + 1 and observed_hash == manifest_hash:
                    new_revision = observed_revision
                elif observed_revision == expected_revision and observed_hash == expected_hash:
                    raise WorkspaceStorageError("WORKSPACE_SYNC_COMMIT_FAILED") from None
                else:
                    raise WorkspaceStorageError("WORKSPACE_SYNC_CONFLICT") from None
        except BaseException as exc:
            cleanup_failed = False
            cleanup_objects = list(planned)
            commit_unknown = (
                isinstance(exc, WorkspaceStorageError) and exc.code == "WORKSPACE_SYNC_COMMIT_STATUS_UNKNOWN"
            )
            if cleanup_objects and not commit_unknown:
                try:
                    await asyncio.shield(self.client.delete(cleanup_objects))
                    await asyncio.shield(self.repository.mark_staged_objects_cleaned(cleanup_objects))
                except BaseException:
                    cleanup_failed = True
                    try:
                        await asyncio.shield(self.repository.mark_staged_objects_cleanup_failed(cleanup_objects))
                    except BaseException:
                        pass
            if isinstance(exc, asyncio.CancelledError):
                raise
            if cleanup_failed:
                raise WorkspaceStorageError("SUPABASE_STORAGE_CLEANUP_FAILED") from exc
            if isinstance(exc, WorkspaceStorageError):
                raise
            raise WorkspaceStorageError("WORKSPACE_SYNC_COMMIT_FAILED") from exc

        revision_after = self._revision_token(new_revision, manifest_hash)
        return WorkspaceSyncResult(base_revision, revision_after, tuple(changes))

    async def list_objects(self, owner_id: str, workspace_id: str) -> list[str]:
        prefix = f"users/{self.owner_prefix(owner_id)}/workspaces/{workspace_id}/"
        return await self.client.list(prefix)

    async def cleanup_abandoned(self, limit: int = 100, min_age_seconds: int = 3600) -> int:
        records = await self.repository.abandoned_staged_objects(limit, min_age_seconds)
        if not records:
            return 0
        keys = [str(record.storage_object_key) for record in records]
        await self.client.delete(keys)
        await self.repository.mark_staged_objects_cleaned(keys)
        return len(keys)


def supabase_storage_readiness() -> dict[str, object]:
    try:
        settings = SupabaseStorageSettings.from_environment()
    except WorkspaceStorageError as exc:
        return {"backend": "supabase", "configured": False, "reachable": False, "ready": False, "reason": exc.code}
    return {
        "backend": "supabase",
        "configured": True,
        "reachable": None,
        "ready": None,
        "bucket": settings.bucket,
    }


async def probe_supabase_storage() -> dict[str, object]:
    readiness = supabase_storage_readiness()
    if not readiness["configured"]:
        return readiness
    try:
        settings = SupabaseStorageSettings.from_environment()
        readiness["reachable"] = await SupabaseStorageHttpClient(settings).check_bucket()
    except Exception:
        readiness["reachable"] = False
    readiness["ready"] = bool(readiness["configured"] and readiness["reachable"])
    return readiness
