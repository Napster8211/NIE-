"""Fail-closed, atomic persistence for Director company objectives."""
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

from app.schemas.company_objective import (
    CompanyObjective,
    CompanyObjectiveStatus,
    TERMINAL_OBJECTIVE_STATUSES,
)


OBJECTIVE_FILE = os.getenv("NIE_COMPANY_OBJECTIVE_FILE", ".napstertec_objectives.json")
DEFAULT_MAX_MISSIONS_PER_OBJECTIVE = int(os.getenv("NIE_MAX_MISSIONS_PER_OBJECTIVE", "10"))
DEFAULT_MAX_STRATEGY_CHANGES = int(os.getenv("NIE_MAX_STRATEGY_CHANGES", "3"))
DEFAULT_MAX_ZERO_PROGRESS_CYCLES = int(os.getenv("NIE_MAX_ZERO_PROGRESS_CYCLES", "3"))
_OBJECTIVE_MUTEX_TIMEOUT_MS = 30_000


class ObjectivePersistenceError(RuntimeError):
    pass


class ObjectiveInvariantError(ValueError):
    pass


@contextmanager
def _cross_process_objective_lock(path: str):
    """Use the same Windows named-mutex discipline as MissionRegistry."""
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
    handle = create_mutex(None, False, f"Local\\NapsterTecObjectiveRegistry_{lock_id}")
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    wait_result = wait_for_single_object(handle, _OBJECTIVE_MUTEX_TIMEOUT_MS)
    if wait_result not in {0x00000000, 0x00000080}:
        close_handle(handle)
        if wait_result == 0x00000102:
            raise TimeoutError("CompanyObjectiveCrossProcessLockTimeout")
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        yield
    finally:
        release_mutex(handle)
        close_handle(handle)


class CompanyObjectiveRepository:
    def __init__(self, storage_path: Optional[str] = None):
        self.storage_path = os.path.abspath(storage_path or OBJECTIVE_FILE)
        self._lock = threading.RLock()
        self._objectives: Dict[str, CompanyObjective] = {}
        with self.locked(reload=False):
            self._objectives = self._read_from_disk()

    @contextmanager
    def locked(self, reload: bool = True):
        with self._lock:
            with _cross_process_objective_lock(self.storage_path):
                if reload:
                    self._objectives = self._read_from_disk()
                yield

    def _read_from_disk(self) -> Dict[str, CompanyObjective]:
        path = Path(self.storage_path)
        if not path.exists():
            return {}
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("objective registry root must be an object")
            return {
                objective_id: CompanyObjective.model_validate(payload)
                for objective_id, payload in raw.items()
            }
        except Exception as exc:
            raise ObjectivePersistenceError(
                f"COMPANY_OBJECTIVE_PERSISTENCE_LOAD_FAILED: {exc}"
            ) from exc

    def _persist(self, objectives: Dict[str, CompanyObjective]) -> None:
        directory = os.path.dirname(self.storage_path) or "."
        os.makedirs(directory, exist_ok=True)
        temp_path: Optional[str] = None
        try:
            payload = {
                objective_id: objective.model_dump(mode="json")
                for objective_id, objective in objectives.items()
            }
            fd, temp_path = tempfile.mkstemp(
                prefix=".objectives-", suffix=".tmp", dir=directory
            )
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self.storage_path)
        except Exception as exc:
            raise ObjectivePersistenceError(
                f"COMPANY_OBJECTIVE_PERSISTENCE_WRITE_FAILED: {exc}"
            ) from exc
        finally:
            if temp_path and os.path.exists(temp_path):
                os.unlink(temp_path)

    @staticmethod
    def _copy(objective: CompanyObjective) -> CompanyObjective:
        return CompanyObjective.model_validate(objective.model_dump(mode="json"))

    def _commit(self, proposed: Dict[str, CompanyObjective]) -> None:
        self._persist(proposed)
        self._objectives = proposed

    def create(self, objective: CompanyObjective) -> CompanyObjective:
        with self.locked():
            if objective.objective_id in self._objectives:
                raise ObjectiveInvariantError("OBJECTIVE_ALREADY_EXISTS")
            if objective.version != 1:
                raise ObjectiveInvariantError("OBJECTIVE_INITIAL_VERSION_MUST_BE_ONE")
            proposed = dict(self._objectives)
            proposed[objective.objective_id] = self._copy(objective)
            self._commit(proposed)
            return self._copy(proposed[objective.objective_id])

    def get(self, objective_id: str) -> Optional[CompanyObjective]:
        with self.locked():
            objective = self._objectives.get(objective_id)
            return self._copy(objective) if objective else None

    def list(self) -> List[CompanyObjective]:
        with self.locked():
            return [self._copy(value) for value in self._objectives.values()]

    def update(
        self,
        objective_id: str,
        changes: Dict[str, Any],
        *,
        expected_version: Optional[int] = None,
    ) -> CompanyObjective:
        forbidden = {"objective_id", "created_at", "version"}.intersection(changes)
        if forbidden:
            raise ObjectiveInvariantError(
                f"OBJECTIVE_IMMUTABLE_FIELDS: {', '.join(sorted(forbidden))}"
            )
        with self.locked():
            current = self._objectives.get(objective_id)
            if not current:
                raise ObjectiveInvariantError("OBJECTIVE_NOT_FOUND")
            if expected_version is not None and current.version != expected_version:
                raise ObjectiveInvariantError("OBJECTIVE_VERSION_CONFLICT")
            payload = current.model_dump(mode="json")
            payload.update(changes)
            payload["version"] = current.version + 1
            payload["updated_at"] = datetime.now(timezone.utc).isoformat()
            try:
                candidate = CompanyObjective.model_validate(payload)
            except ValueError as exc:
                raise ObjectiveInvariantError(str(exc)) from exc
            proposed = dict(self._objectives)
            proposed[objective_id] = candidate
            self._commit(proposed)
            return self._copy(candidate)

    def link_mission(self, objective_id: str, mission_id: str) -> CompanyObjective:
        with self.locked():
            current = self._objectives.get(objective_id)
            if not current:
                raise ObjectiveInvariantError("OBJECTIVE_NOT_FOUND")
            if current.is_terminal:
                raise ObjectiveInvariantError("TERMINAL_OBJECTIVE_CANNOT_CREATE_WORK")
            if mission_id in current.linked_mission_ids:
                raise ObjectiveInvariantError("OBJECTIVE_DUPLICATE_MISSION_LINK")
            if len(current.linked_mission_ids) >= current.max_missions:
                raise ObjectiveInvariantError("OBJECTIVE_MAX_MISSIONS_REACHED")
            links = [*current.linked_mission_ids, mission_id]
            return self.update(
                objective_id, {"linked_mission_ids": links}, expected_version=current.version
            )

    def update_status(
        self,
        objective_id: str,
        status: CompanyObjectiveStatus,
        *,
        terminal_reason: Optional[str] = None,
    ) -> CompanyObjective:
        target = CompanyObjectiveStatus(status)
        current = self.get(objective_id)
        if not current:
            raise ObjectiveInvariantError("OBJECTIVE_NOT_FOUND")
        if current.is_terminal and current.status != target:
            raise ObjectiveInvariantError("TERMINAL_OBJECTIVE_TRANSITION_REJECTED")
        if current.status == target and current.terminal_reason == terminal_reason:
            return current
        changes: Dict[str, Any] = {"status": target.value}
        if terminal_reason is not None:
            changes["terminal_reason"] = terminal_reason
        return self.update(objective_id, changes, expected_version=current.version)

    def terminal_transition(
        self,
        objective_id: str,
        status: CompanyObjectiveStatus,
        terminal_reason: str,
    ) -> CompanyObjective:
        target = CompanyObjectiveStatus(status)
        if target not in TERMINAL_OBJECTIVE_STATUSES:
            raise ObjectiveInvariantError("OBJECTIVE_TERMINAL_STATUS_REQUIRED")
        if not terminal_reason.strip():
            raise ObjectiveInvariantError("OBJECTIVE_TERMINAL_REASON_REQUIRED")
        return self.update_status(
            objective_id, target, terminal_reason=terminal_reason.strip()
        )

    def set_verified_success_count(
        self, objective_id: str, verified_success_count: int
    ) -> CompanyObjective:
        if verified_success_count < 0:
            raise ObjectiveInvariantError("OBJECTIVE_VERIFIED_COUNT_NEGATIVE")
        current = self.get(objective_id)
        if not current:
            raise ObjectiveInvariantError("OBJECTIVE_NOT_FOUND")
        return self.update(
            objective_id,
            {"verified_success_count": verified_success_count},
            expected_version=current.version,
        )

    def record_strategy_change(self, objective_id: str) -> CompanyObjective:
        current = self.get(objective_id)
        if not current:
            raise ObjectiveInvariantError("OBJECTIVE_NOT_FOUND")
        if current.is_terminal:
            raise ObjectiveInvariantError("TERMINAL_OBJECTIVE_CANNOT_CREATE_WORK")
        if current.strategy_change_count >= current.max_strategy_changes:
            raise ObjectiveInvariantError("OBJECTIVE_MAX_STRATEGY_CHANGES_REACHED")
        return self.update(
            objective_id,
            {
                "strategy_change_count": current.strategy_change_count + 1,
                "current_strategy_version": current.current_strategy_version + 1,
            },
            expected_version=current.version,
        )

    def record_zero_progress_cycle(self, objective_id: str) -> CompanyObjective:
        current = self.get(objective_id)
        if not current:
            raise ObjectiveInvariantError("OBJECTIVE_NOT_FOUND")
        if current.is_terminal:
            raise ObjectiveInvariantError("TERMINAL_OBJECTIVE_CANNOT_CREATE_WORK")
        if current.zero_progress_cycles >= current.max_zero_progress_cycles:
            raise ObjectiveInvariantError("OBJECTIVE_MAX_ZERO_PROGRESS_CYCLES_REACHED")
        return self.update(
            objective_id,
            {"zero_progress_cycles": current.zero_progress_cycles + 1},
            expected_version=current.version,
        )

    def snapshot(self) -> Dict[str, CompanyObjective]:
        with self.locked():
            return {
                objective_id: self._copy(objective)
                for objective_id, objective in self._objectives.items()
            }

    def persisted_digest(self) -> str:
        with self.locked(reload=False):
            path = Path(self.storage_path)
            payload = path.read_bytes() if path.exists() else b""
            return hashlib.sha256(payload).hexdigest()


company_objective_repository = CompanyObjectiveRepository()
