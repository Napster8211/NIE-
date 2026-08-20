import logging
from typing import Dict, Any, Tuple, Optional
from enum import Enum
from app.planner.planner_models import ExecutionPlan, TaskStep

logger = logging.getLogger(__name__)

class StepStatus(str, Enum):
    PENDING = "PENDING"
    IN_PROGRESS = "IN_PROGRESS"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"

class PlanExecutorState:
    """Manages the state and results of a generated ExecutionPlan."""
    
    def __init__(self, plan: ExecutionPlan):
        self.plan = plan
        self.step_statuses: Dict[int, StepStatus] = {i: StepStatus.PENDING for i in range(len(plan.steps))}
        self.step_results: Dict[int, Any] = {}
        self.is_aborted: bool = False

    def get_next_pending_step(self) -> Tuple[Optional[int], Optional[TaskStep]]:
        """Retrieves the next step in the sequence that needs execution."""
        if self.is_aborted:
            return None, None
            
        for i, step in enumerate(self.plan.steps):
            if self.step_statuses[i] == StepStatus.PENDING:
                return i, step
        return None, None

    def mark_step_in_progress(self, step_index: int) -> None:
        if step_index in self.step_statuses:
            self.step_statuses[step_index] = StepStatus.IN_PROGRESS
            logger.debug(f"[ExecutionPlan] Step {step_index} ({self.plan.steps[step_index].tool}) marked IN_PROGRESS.")

    def mark_step_completed(self, step_index: int, result: Any) -> None:
        if step_index in self.step_statuses:
            self.step_statuses[step_index] = StepStatus.COMPLETED
            self.step_results[step_index] = result
            logger.info(f"[ExecutionPlan] Step {step_index} completed successfully.")

    def mark_step_failed(self, step_index: int, error: str) -> None:
        if step_index in self.step_statuses:
            self.step_statuses[step_index] = StepStatus.FAILED
            self.step_results[step_index] = {"error": error}
            self.is_aborted = True
            logger.error(f"[ExecutionPlan] Step {step_index} FAILED. Plan aborted. Error: {error}")

    def is_complete(self) -> bool:
        """Returns True if all steps are completed successfully."""
        return all(status == StepStatus.COMPLETED for status in self.step_statuses.values()) and not self.is_aborted