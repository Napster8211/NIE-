import os
import json
import tempfile
import threading
from typing import Dict, List, Optional
from datetime import datetime, timezone
from app.schemas.mission_portfolio import MissionPortfolio, MissionPortfolioStatus

PORTFOLIO_FILE = os.getenv("NIE_MISSION_PORTFOLIO_FILE", ".napstertec_mission_portfolios.json")

class MissionPortfolioRepository:
    def __init__(self, storage_path: Optional[str] = None):
        self.storage_path = os.path.abspath(storage_path or PORTFOLIO_FILE)
        self._lock = threading.RLock()
        self._portfolios: Dict[str, MissionPortfolio] = {}
        with self._lock:
            self._portfolios = self._read_from_disk()

    def _read_from_disk(self) -> Dict[str, MissionPortfolio]:
        if not os.path.exists(self.storage_path):
            return {}
        try:
            with open(self.storage_path, "r", encoding="utf-8") as f:
                raw = json.load(f)
            return {
                p_id: MissionPortfolio.model_validate(payload)
                for p_id, payload in raw.items()
            }
        except Exception as exc:
            raise RuntimeError(f"MISSION_PORTFOLIO_PERSISTENCE_LOAD_FAILED: {exc}") from exc

    def _persist(self, portfolios: Dict[str, MissionPortfolio]) -> None:
        directory = os.path.dirname(self.storage_path) or "."
        os.makedirs(directory, exist_ok=True)
        temp_path = None
        try:
            payload = {
                p_id: p.model_dump(mode="json")
                for p_id, p in portfolios.items()
            }
            fd, temp_path = tempfile.mkstemp(prefix=".portfolios-", suffix=".tmp", dir=directory)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(payload, handle, separators=(",", ":"))
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_path, self.storage_path)
        except Exception as exc:
            raise RuntimeError(f"MISSION_PORTFOLIO_PERSISTENCE_WRITE_FAILED: {exc}") from exc
        finally:
            if temp_path and os.path.exists(temp_path):
                os.unlink(temp_path)

    def create(self, portfolio: MissionPortfolio) -> MissionPortfolio:
        with self._lock:
            if portfolio.portfolio_id in self._portfolios:
                raise ValueError("MISSION_PORTFOLIO_ALREADY_EXISTS")
            if portfolio.version != 1:
                raise ValueError("MISSION_PORTFOLIO_INITIAL_VERSION_MUST_BE_ONE")
            
            # Supersede unstarted/draft portfolios for the same objective/plan
            for existing in self._portfolios.values():
                if existing.strategic_plan_id == portfolio.strategic_plan_id and existing.status in [MissionPortfolioStatus.DRAFT, MissionPortfolioStatus.READY]:
                    existing.status = MissionPortfolioStatus.SUPERSEDED
                    existing.updated_at = datetime.now(timezone.utc).isoformat()
            
            proposed = dict(self._portfolios)
            proposed[portfolio.portfolio_id] = MissionPortfolio.model_validate(portfolio.model_dump())
            self._persist(proposed)
            self._portfolios = proposed
            return self._portfolios[portfolio.portfolio_id]

    def get(self, portfolio_id: str) -> Optional[MissionPortfolio]:
        with self._lock:
            p = self._portfolios.get(portfolio_id)
            return MissionPortfolio.model_validate(p.model_dump()) if p else None

    def get_latest_for_objective(self, objective_id: str) -> Optional[MissionPortfolio]:
        with self._lock:
            obj_ports = [p for p in self._portfolios.values() if p.objective_id == objective_id]
            if not obj_ports:
                return None
            return sorted(obj_ports, key=lambda p: p.created_at, reverse=True)[0]
            
    def update(self, portfolio: MissionPortfolio) -> MissionPortfolio:
        with self._lock:
            if portfolio.portfolio_id not in self._portfolios:
                raise ValueError("MISSION_PORTFOLIO_NOT_FOUND")
            portfolio.updated_at = datetime.now(timezone.utc).isoformat()
            portfolio.version += 1
            proposed = dict(self._portfolios)
            proposed[portfolio.portfolio_id] = MissionPortfolio.model_validate(portfolio.model_dump())
            self._persist(proposed)
            self._portfolios = proposed
            return self._portfolios[portfolio.portfolio_id]

mission_portfolio_repository = MissionPortfolioRepository()