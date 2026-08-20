"""Immutable, fail-closed persistence for Director executive decisions."""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, List, Optional

from app.schemas.shared_artifacts import ExecutiveDecisionRecord


DECISION_FILE = os.getenv(
    "NIE_EXECUTIVE_DECISION_FILE", ".napstertec_executive_decisions.json"
)
_DECISION_MUTEX_TIMEOUT_MS = 30_000


class ExecutiveDecisionPersistenceError(RuntimeError):
    pass


class ExecutiveDecisionInvariantError(ValueError):
    pass


class DuplicateExecutiveDecisionError(ExecutiveDecisionInvariantError):
    def __init__(self, existing_decision_id: str):
        self.existing_decision_id = existing_decision_id
        super().__init__(f"EXECUTIVE_DECISION_DUPLICATE:{existing_decision_id}")


@contextmanager
def _cross_process_decision_lock(path: str):
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
    handle = create_mutex(None, False, f"Local\\NapsterTecExecutiveDecision_{lock_id}")
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    wait_result = wait_for_single_object(handle, _DECISION_MUTEX_TIMEOUT_MS)
    if wait_result not in {0x00000000, 0x00000080}:
        close_handle(handle)
        if wait_result == 0x00000102:
            raise TimeoutError("ExecutiveDecisionCrossProcessLockTimeout")
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        yield
    finally:
        release_mutex(handle)
        close_handle(handle)


class ExecutiveDecisionRepository:
    def __init__(self, storage_path: Optional[str] = None):
        self.storage_path = os.path.abspath(storage_path or DECISION_FILE)
        self._lock = threading.RLock()
        self._decisions: Dict[str, ExecutiveDecisionRecord] = {}
        with self.locked(reload=False):
            self._decisions = self._read_from_disk()

    @contextmanager
    def locked(self, reload: bool = True):
        with self._lock:
            with _cross_process_decision_lock(self.storage_path):
                if reload:
                    self._decisions = self._read_from_disk()
                yield

    def _read_from_disk(self) -> Dict[str, ExecutiveDecisionRecord]:
        path = Path(self.storage_path)
        if not path.exists():
            return {}
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("decision registry root must be an object")
            return {
                decision_id: ExecutiveDecisionRecord.model_validate(payload)
                for decision_id, payload in raw.items()
            }
        except Exception as exc:
            raise ExecutiveDecisionPersistenceError(
                f"EXECUTIVE_DECISION_PERSISTENCE_LOAD_FAILED: {exc}"
            ) from exc

    def _persist(self, decisions: Dict[str, ExecutiveDecisionRecord]) -> None:
        directory = os.path.dirname(self.storage_path) or "."
        os.makedirs(directory, exist_ok=True)
        temp_path: Optional[str] = None
        try:
            payload = {
                decision_id: decision.model_dump(mode="json")
                for decision_id, decision in decisions.items()
            }
            fd, temp_path = tempfile.mkstemp(
                prefix=".executive-decisions-", suffix=".tmp", dir=directory
            )
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self.storage_path)
        except Exception as exc:
            raise ExecutiveDecisionPersistenceError(
                f"EXECUTIVE_DECISION_PERSISTENCE_WRITE_FAILED: {exc}"
            ) from exc
        finally:
            if temp_path and os.path.exists(temp_path):
                os.unlink(temp_path)

    @staticmethod
    def _copy(decision: ExecutiveDecisionRecord) -> ExecutiveDecisionRecord:
        return ExecutiveDecisionRecord.model_validate(decision.model_dump(mode="json"))

    def create(self, decision: ExecutiveDecisionRecord) -> ExecutiveDecisionRecord:
        with self.locked():
            if decision.decision_id in self._decisions:
                raise ExecutiveDecisionInvariantError("EXECUTIVE_DECISION_ALREADY_EXISTS")
            duplicate = next(
                (
                    existing
                    for existing in self._decisions.values()
                    if existing.objective_id == decision.objective_id
                    and (
                        existing.mission_terminal_event_id
                        == decision.mission_terminal_event_id
                        or (
                            existing.mission_id == decision.mission_id
                            and existing.mission_terminal_state
                            == decision.mission_terminal_state
                        )
                    )
                ),
                None,
            )
            if duplicate:
                raise DuplicateExecutiveDecisionError(duplicate.decision_id)
            proposed = dict(self._decisions)
            proposed[decision.decision_id] = self._copy(decision)
            self._persist(proposed)
            self._decisions = proposed
            return self._copy(decision)

    def get(self, decision_id: str) -> Optional[ExecutiveDecisionRecord]:
        with self.locked():
            decision = self._decisions.get(decision_id)
            return self._copy(decision) if decision else None

    def get_by_terminal_event(
        self, objective_id: str, mission_terminal_event_id: str
    ) -> Optional[ExecutiveDecisionRecord]:
        with self.locked():
            decision = next(
                (
                    item
                    for item in self._decisions.values()
                    if item.objective_id == objective_id
                    and item.mission_terminal_event_id == mission_terminal_event_id
                ),
                None,
            )
            return self._copy(decision) if decision else None

    def list_by_objective(self, objective_id: str) -> List[ExecutiveDecisionRecord]:
        with self.locked():
            return [
                self._copy(item)
                for item in self._decisions.values()
                if item.objective_id == objective_id
            ]

    def get_latest_by_objective(
        self, objective_id: str
    ) -> Optional[ExecutiveDecisionRecord]:
        decisions = self.list_by_objective(objective_id)
        return max(decisions, key=lambda item: item.created_at) if decisions else None

    def list_by_mission(self, mission_id: str) -> List[ExecutiveDecisionRecord]:
        with self.locked():
            return [
                self._copy(item)
                for item in self._decisions.values()
                if item.mission_id == mission_id
            ]

    def _rollback_create(self, decision_id: str) -> None:
        """Internal compensation used only when the paired objective write fails."""
        with self.locked():
            if decision_id not in self._decisions:
                return
            proposed = dict(self._decisions)
            proposed.pop(decision_id)
            self._persist(proposed)
            self._decisions = proposed

    def snapshot(self) -> Dict[str, ExecutiveDecisionRecord]:
        with self.locked():
            return {
                decision_id: self._copy(decision)
                for decision_id, decision in self._decisions.items()
            }

    def persisted_digest(self) -> str:
        with self.locked(reload=False):
            path = Path(self.storage_path)
            payload = path.read_bytes() if path.exists() else b""
            return hashlib.sha256(payload).hexdigest()


executive_decision_repository = ExecutiveDecisionRepository()
