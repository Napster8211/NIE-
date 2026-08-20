"""Fail-closed, atomic persistence for Director and Mission approvals."""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
import tempfile
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.schemas.shared_artifacts import (
    ApprovalRequest,
    ApprovalStatus,
)


APPROVAL_FILE = os.getenv("NIE_APPROVAL_FILE", ".napstertec_approvals.json")
_APPROVAL_MUTEX_TIMEOUT_MS = 30_000


class ApprovalPersistenceError(RuntimeError):
    pass


class ApprovalInvariantError(ValueError):
    pass


@contextmanager
def _cross_process_approval_lock(path: str):
    """Use the same Windows named-mutex discipline as MissionRegistry and ObjectiveRegistry."""
    if os.name != "nt":
        yield
        return

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create_mutex = kernel32.CreateMutexW
    create_mutex.argtypes = (ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p)
    create_mutex.restype = ctypes.c_void_p
    wait_for_single_object = kernel32.WaitForSingleObject
    wait_for_single_object.argtypes = (ctypes.c_void_p, ctypes.c_uint32)
    wait_for_single_object.restype = ctypes.c_uint32
    release_mutex = kernel32.ReleaseMutex
    release_mutex.argtypes = (ctypes.c_void_p,)
    release_mutex.restype = ctypes.c_bool
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = (ctypes.c_void_p,)
    close_handle.restype = ctypes.c_bool

    normalized_path = os.path.normcase(os.path.abspath(path))
    lock_id = hashlib.sha256(normalized_path.encode("utf-8")).hexdigest()
    handle = create_mutex(None, False, f"Local\\NapsterTecApprovalRegistry_{lock_id}")
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    wait_result = wait_for_single_object(handle, _APPROVAL_MUTEX_TIMEOUT_MS)
    if wait_result not in {0x00000000, 0x00000080}:
        close_handle(handle)
        if wait_result == 0x00000102:
            raise TimeoutError("ApprovalRegistryCrossProcessLockTimeout")
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        yield
    finally:
        release_mutex(handle)
        close_handle(handle)


class ApprovalRepository:
    def __init__(self, storage_path: Optional[str] = None):
        self.storage_path = os.path.abspath(storage_path or APPROVAL_FILE)
        self._lock = threading.RLock()
        self._approvals: Dict[str, ApprovalRequest] = {}
        with self.locked(reload=False):
            self._approvals = self._read_from_disk()

    @contextmanager
    def locked(self, reload: bool = True):
        with self._lock:
            with _cross_process_approval_lock(self.storage_path):
                if reload:
                    self._approvals = self._read_from_disk()
                yield

    def _read_from_disk(self) -> Dict[str, ApprovalRequest]:
        path = Path(self.storage_path)
        if not path.exists():
            return {}
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("approval registry root must be an object")
            return {
                approval_id: ApprovalRequest.model_validate(payload)
                for approval_id, payload in raw.items()
            }
        except Exception as exc:
            raise ApprovalPersistenceError(
                f"APPROVAL_PERSISTENCE_LOAD_FAILED: {exc}"
            ) from exc

    def _persist(self, approvals: Dict[str, ApprovalRequest]) -> None:
        directory = os.path.dirname(self.storage_path) or "."
        os.makedirs(directory, exist_ok=True)
        temp_path: Optional[str] = None
        try:
            payload = {
                approval_id: approval.model_dump(mode="json")
                for approval_id, approval in approvals.items()
            }
            fd, temp_path = tempfile.mkstemp(
                prefix=".approvals-", suffix=".tmp", dir=directory
            )
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self.storage_path)
        except Exception as exc:
            raise ApprovalPersistenceError(
                f"APPROVAL_PERSISTENCE_WRITE_FAILED: {exc}"
            ) from exc
        finally:
            if temp_path and os.path.exists(temp_path):
                os.unlink(temp_path)

    @staticmethod
    def _copy(approval: ApprovalRequest) -> ApprovalRequest:
        return ApprovalRequest.model_validate(approval.model_dump(mode="json"))

    def _commit(self, proposed: Dict[str, ApprovalRequest]) -> None:
        self._persist(proposed)
        self._approvals = proposed

    def create(self, approval: ApprovalRequest) -> ApprovalRequest:
        with self.locked():
            if approval.approval_id in self._approvals:
                raise ApprovalInvariantError("APPROVAL_ALREADY_EXISTS")
            if approval.version != 1:
                raise ApprovalInvariantError("APPROVAL_INITIAL_VERSION_MUST_BE_ONE")
            if approval.status != ApprovalStatus.PENDING:
                raise ApprovalInvariantError("NEW_APPROVAL_MUST_BE_PENDING")
            
            proposed = dict(self._approvals)
            proposed[approval.approval_id] = self._copy(approval)
            self._commit(proposed)
            return self._copy(proposed[approval.approval_id])

    def get(self, approval_id: str) -> Optional[ApprovalRequest]:
        with self.locked():
            approval = self._approvals.get(approval_id)
            return self._copy(approval) if approval else None

    def list_by_mission(self, mission_id: str) -> List[ApprovalRequest]:
        with self.locked():
            return [
                self._copy(value)
                for value in self._approvals.values()
                if value.mission_id == mission_id
            ]

    def list_pending(self) -> List[ApprovalRequest]:
        with self.locked():
            return [
                self._copy(value)
                for value in self._approvals.values()
                if value.status == ApprovalStatus.PENDING
            ]

    def resolve_approval(
        self,
        approval_id: str,
        status: ApprovalStatus,
        reason: str,
        *,
        expected_version: Optional[int] = None,
    ) -> ApprovalRequest:
        if status == ApprovalStatus.PENDING:
            raise ApprovalInvariantError("CANNOT_RESOLVE_TO_PENDING")
        if not reason or not str(reason).strip():
            raise ApprovalInvariantError("RESOLUTION_REASON_REQUIRED")

        with self.locked():
            current = self._approvals.get(approval_id)
            if not current:
                raise ApprovalInvariantError("APPROVAL_NOT_FOUND")
            if expected_version is not None and current.version != expected_version:
                raise ApprovalInvariantError("APPROVAL_VERSION_CONFLICT")
            if current.status != ApprovalStatus.PENDING:
                raise ApprovalInvariantError("APPROVAL_ALREADY_RESOLVED")

            payload = current.model_dump(mode="json")
            payload["status"] = status.value
            payload["resolution_reason"] = str(reason).strip()
            payload["resolved_at"] = datetime.now(timezone.utc).isoformat()
            payload["version"] = current.version + 1

            try:
                candidate = ApprovalRequest.model_validate(payload)
            except ValueError as exc:
                raise ApprovalInvariantError(str(exc)) from exc

            proposed = dict(self._approvals)
            proposed[approval_id] = candidate
            self._commit(proposed)
            return self._copy(candidate)

    def revoke_approval(
        self,
        approval_id: str,
        reason: str,
        *,
        expected_version: Optional[int] = None,
    ) -> ApprovalRequest:
        if not reason or not str(reason).strip():
            raise ApprovalInvariantError("RESOLUTION_REASON_REQUIRED")

        with self.locked():
            current = self._approvals.get(approval_id)
            if not current:
                raise ApprovalInvariantError("APPROVAL_NOT_FOUND")
            if expected_version is not None and current.version != expected_version:
                raise ApprovalInvariantError("APPROVAL_VERSION_CONFLICT")
            if current.status != ApprovalStatus.APPROVED:
                raise ApprovalInvariantError("ONLY_APPROVED_CAN_BE_REVOKED")

            payload = current.model_dump(mode="json")
            payload["status"] = ApprovalStatus.CANCELLED.value
            payload["resolution_reason"] = f"REVOKED: {str(reason).strip()}"
            payload["resolved_at"] = datetime.now(timezone.utc).isoformat()
            payload["version"] = current.version + 1

            try:
                candidate = ApprovalRequest.model_validate(payload)
            except ValueError as exc:
                raise ApprovalInvariantError(str(exc)) from exc

            proposed = dict(self._approvals)
            proposed[approval_id] = candidate
            self._commit(proposed)
            return self._copy(candidate)

    def snapshot(self) -> Dict[str, ApprovalRequest]:
        with self.locked():
            return {
                approval_id: self._copy(approval)
                for approval_id, approval in self._approvals.items()
            }

    def persisted_digest(self) -> str:
        with self.locked(reload=False):
            path = Path(self.storage_path)
            payload = path.read_bytes() if path.exists() else b""
            return hashlib.sha256(payload).hexdigest()


approval_repository = ApprovalRepository()