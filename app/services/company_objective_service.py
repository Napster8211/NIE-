"""Director-facing intake and read-only inspection for company objectives."""
from __future__ import annotations

import re
import uuid
from typing import Any, Dict, Optional

from app.repositories.company_objective_repository import (
    CompanyObjectiveRepository,
    DEFAULT_MAX_MISSIONS_PER_OBJECTIVE,
    DEFAULT_MAX_STRATEGY_CHANGES,
    DEFAULT_MAX_ZERO_PROGRESS_CYCLES,
    company_objective_repository,
)
from app.schemas.company_objective import (
    CompanyObjective,
    CompanyObjectiveStatus,
    CompanyObjectiveSuccessCriteria,
)


class CompanyObjectiveService:
    def __init__(self, repository: Optional[CompanyObjectiveRepository] = None):
        self.repository = repository or company_objective_repository

    @staticmethod
    def _normalize_request(query: str) -> str:
        normalized = re.sub(r"\s+", " ", query).strip(" .")
        normalized = re.sub(
            r"^(?:director|ceo)\s*[,\-:]?\s*", "", normalized, flags=re.IGNORECASE
        )
        normalized = re.sub(
            r"^(?:create|set|establish|define)\s+(?:a\s+)?(?:company\s+|business\s+)?objective\s+(?:to\s+|for\s+)?",
            "",
            normalized,
            flags=re.IGNORECASE,
        )
        return normalized.strip(" .")

    @staticmethod
    def _derive_success_criteria(objective: str) -> CompanyObjectiveSuccessCriteria:
        target = re.search(r"\b(\d+)\b", objective)
        required = int(target.group(1)) if target else 1
        lower = objective.lower()
        if "qualified" in lower and re.search(r"\b(?:prospects?|leads?)\b", lower):
            criterion = "verified_qualified_prospects"
            unit = "qualified_prospects"
            evidence = ["verified_business_entity", "verified_mission_artifact"]
        else:
            criterion = "verified_objective_outcomes"
            unit = "verified_outcomes"
            evidence = ["verified_mission_artifact"]
        return CompanyObjectiveSuccessCriteria(
            criterion=criterion,
            required=required,
            unit=unit,
            evidence_requirements=evidence,
        )

    def create_from_request(
        self,
        query: str,
        *,
        max_missions: int = DEFAULT_MAX_MISSIONS_PER_OBJECTIVE,
        max_strategy_changes: int = DEFAULT_MAX_STRATEGY_CHANGES,
        max_zero_progress_cycles: int = DEFAULT_MAX_ZERO_PROGRESS_CYCLES,
    ) -> CompanyObjective:
        objective_text = self._normalize_request(query)
        if not objective_text:
            raise ValueError("COMPANY_OBJECTIVE_TEXT_REQUIRED")
        objective = CompanyObjective(
            objective_id=f"obj_{uuid.uuid4().hex[:12]}",
            title=objective_text[:200],
            objective=f"{objective_text[:1].upper()}{objective_text[1:]}",
            status=CompanyObjectiveStatus.ACTIVE,
            priority="NORMAL",
            autonomy_level="SUPERVISED",
            success_criteria=self._derive_success_criteria(objective_text),
            max_missions=max_missions,
            max_strategy_changes=max_strategy_changes,
            max_zero_progress_cycles=max_zero_progress_cycles,
            metadata={"source": "director_intake"},
        )
        return self.repository.create(objective)

    def inspect(self, objective_id: str) -> Dict[str, Any]:
        objective = self.repository.get(objective_id)
        if not objective:
            raise ValueError("COMPANY_OBJECTIVE_NOT_FOUND")
        return objective.model_dump(mode="json")

    def list(self) -> list[Dict[str, Any]]:
        return [item.model_dump(mode="json") for item in self.repository.list()]
