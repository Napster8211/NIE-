import os
import json
import tempfile
import threading
from typing import Dict, List, Optional
from datetime import datetime, timezone
from app.schemas.executive_strategy import ExecutiveStrategyEvaluation

EVAL_FILE = os.getenv("NIE_EXECUTIVE_STRATEGY_FILE", ".napstertec_executive_strategies.json")

class ExecutiveStrategyRepository:
    def __init__(self, storage_path: Optional[str] = None):
        self.storage_path = os.path.abspath(storage_path or EVAL_FILE)
        self._lock = threading.RLock()
        self._evals: Dict[str, ExecutiveStrategyEvaluation] = {}
        with self._lock:
            self._evals = self._read_from_disk()

    def _read_from_disk(self) -> Dict[str, ExecutiveStrategyEvaluation]:
        if not os.path.exists(self.storage_path):
            return {}
        try:
            with open(self.storage_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            return {
                eval_id: ExecutiveStrategyEvaluation.model_validate(payload)
                for eval_id, payload in raw.items()
            }
        except Exception as exc:
            raise RuntimeError(f"EXECUTIVE_STRATEGY_PERSISTENCE_LOAD_FAILED: {exc}") from exc

    def _persist(self, evals: Dict[str, ExecutiveStrategyEvaluation]) -> None:
        directory = os.path.dirname(self.storage_path) or "."
        os.makedirs(directory, exist_ok=True)
        temp_path = None
        try:
            payload = {
                e_id: e.model_dump(mode="json")
                for e_id, e in evals.items()
            }
            fd, temp_path = tempfile.mkstemp(prefix=".evals-", suffix=".tmp", dir=directory)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self.storage_path)
        except Exception as exc:
            raise RuntimeError(f"EXECUTIVE_STRATEGY_PERSISTENCE_WRITE_FAILED: {exc}") from exc
        finally:
            if temp_path and os.path.exists(temp_path):
                os.unlink(temp_path)

    def create(self, evaluation: ExecutiveStrategyEvaluation) -> ExecutiveStrategyEvaluation:
        with self._lock:
            if evaluation.evaluation_id in self._evals:
                raise ValueError("EXECUTIVE_EVALUATION_ALREADY_EXISTS")
            
            proposed = dict(self._evals)
            proposed[evaluation.evaluation_id] = ExecutiveStrategyEvaluation.model_validate(evaluation.model_dump())
            self._persist(proposed)
            self._evals = proposed
            return self._evals[evaluation.evaluation_id]

    def get_by_trigger(self, portfolio_id: str, trigger_mission_id: str) -> Optional[ExecutiveStrategyEvaluation]:
        with self._lock:
            evals = [e for e in self._evals.values() if e.portfolio_id == portfolio_id and e.trigger_mission_id == trigger_mission_id]
            if not evals:
                return None
            return sorted(evals, key=lambda e: e.created_at, reverse=True)[0]
            
    def get(self, evaluation_id: str) -> Optional[ExecutiveStrategyEvaluation]:
        with self._lock:
            e = self._evals.get(evaluation_id)
            return ExecutiveStrategyEvaluation.model_validate(e.model_dump()) if e else None

executive_strategy_repository = ExecutiveStrategyRepository()