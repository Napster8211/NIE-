from pydantic import BaseModel, Field
from typing import Any, Dict, List, Optional
from enum import Enum

class RetryPolicy(BaseModel):
    """Configures backoff and retry behavior for transient failures."""
    max_retries: int = Field(default=2, ge=0)
    backoff_factor: float = Field(default=1.5, ge=1.0)
    retryable_exceptions: List[str] = Field(
        default_factory=lambda: ["TimeoutError", "ConnectionError", "APIError"]
    )

class ToolResultStatus(str, Enum):
    SUCCESS = "SUCCESS"
    FAILURE = "FAILURE"
    TIMEOUT = "TIMEOUT"

class ToolResult(BaseModel):
    """Standardized output structure for all tool executions."""
    status: ToolResultStatus
    data: Any = None
    error: Optional[str] = None
    execution_time_ms: float = 0.0
    metrics: Dict[str, Any] = Field(default_factory=dict)