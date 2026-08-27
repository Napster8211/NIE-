import os
import json
import tempfile
import threading
from typing import Dict, List, Optional
from datetime import datetime, timezone
from app.schemas.strategic_plan import StrategicPlan, StrategicPlanStatus

PLAN_FILE = os.getenv("NIE_STRATEGIC_PLAN_FILE", ".napstertec_strategic_plans.json")

class StrategicPlanRepository:
    def __init__(self, storage_path: Optional[str] = None):
        self.storage_path = os.path.abspath(storage_path or PLAN_FILE)
        self._lock = threading.RLock()
        self._plans: Dict[str, StrategicPlan] = {}
        with self._lock:
            self._plans = self._read_from_disk()

    def _read_from_disk(self) -> Dict[str, StrategicPlan]:
        if not os.path.exists(self.storage_path):
            return {}
        try:
            with open(self.storage_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            return {
                plan_id: StrategicPlan.model_validate(payload)
                for plan_id, payload in raw.items()
            }
        except Exception as exc:
            raise RuntimeError(f"STRATEGIC_PLAN_PERSISTENCE_LOAD_FAILED: {exc}") from exc

    def _persist(self, plans: Dict[str, StrategicPlan]) -> None:
        directory = os.path.dirname(self.storage_path) or "."
        os.makedirs(directory, exist_ok=True)
        temp_path = None
        try:
            payload = {
                plan_id: plan.model_dump(mode="json")
                for plan_id, plan in plans.items()
            }
            fd, temp_path = tempfile.mkstemp(prefix=".plans-", suffix=".tmp", dir=directory)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self.storage_path)
        except Exception as exc:
            raise RuntimeError(f"STRATEGIC_PLAN_PERSISTENCE_WRITE_FAILED: {exc}") from exc
        finally:
            if temp_path and os.path.exists(temp_path):
                os.unlink(temp_path)

    def create(self, plan: StrategicPlan) -> StrategicPlan:
        with self._lock:
            if plan.strategic_plan_id in self._plans:
                raise ValueError("STRATEGIC_PLAN_ALREADY_EXISTS")
            if plan.version != 1:
                raise ValueError("STRATEGIC_PLAN_INITIAL_VERSION_MUST_BE_ONE")
            
            # Supersede any existing READY/DRAFT plans for this objective
            for existing in self._plans.values():
                if existing.objective_id == plan.objective_id and existing.status in [StrategicPlanStatus.DRAFT, StrategicPlanStatus.READY, StrategicPlanStatus.NEEDS_CLARIFICATION]:
                    existing.status = StrategicPlanStatus.SUPERSEDED
                    existing.updated_at = datetime.now(timezone.utc).isoformat()
            
            proposed = dict(self._plans)
            proposed[plan.strategic_plan_id] = StrategicPlan.model_validate(plan.model_dump())
            self._persist(proposed)
            self._plans = proposed
            return self._plans[plan.strategic_plan_id]

    def get(self, plan_id: str) -> Optional[StrategicPlan]:
        with self._lock:
            plan = self._plans.get(plan_id)
            return StrategicPlan.model_validate(plan.model_dump()) if plan else None

    def get_latest_for_objective(self, objective_id: str) -> Optional[StrategicPlan]:
        with self._lock:
            obj_plans = [p for p in self._plans.values() if p.objective_id == objective_id]
            if not obj_plans:
                return None
            return sorted(obj_plans, key=lambda p: p.created_at, reverse=True)[0]
            
    def list(self) -> List[StrategicPlan]:
        with self._lock:
            return [StrategicPlan.model_validate(p.model_dump()) for p in self._plans.values()]

strategic_plan_repository = StrategicPlanRepository()