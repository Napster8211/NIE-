import time
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

class ToolExecution(BaseModel):
    tool_name: str
    arguments: Dict[str, Any]

class ExecutionTrace(BaseModel):
    step_number: int
    thought: str = Field(..., description="The agent's internal reasoning for this step.")
    action: Optional[ToolExecution] = Field(default=None, description="The tool selected for execution.")
    observation: Optional[str] = Field(default=None, description="The raw output or error returned by the tool.")
    duration_seconds: float = Field(default=0.0, description="Time taken to execute the step.")
    confidence: float = Field(default=1.0, description="Agent's confidence in the result (0.0 to 1.0).")
    reflection: Optional[str] = Field(default=None, description="Self-correction or analysis of the observation.")
    is_error: bool = Field(default=False, description="Flag indicating if the action resulted in a failure.")

class AgentState(BaseModel):
    session_id: str
    goal: str
    status: str = Field(default="running", description="running, completed, or failed")
    current_plan: List[str] = Field(default_factory=list, description="The dynamic sequence of pending tasks.")
    traces: List[ExecutionTrace] = Field(default_factory=list, description="Full execution history.")
    final_result: Optional[str] = Field(default=None, description="The finalized output when the goal is met.")
    iteration_count: int = Field(default=0)