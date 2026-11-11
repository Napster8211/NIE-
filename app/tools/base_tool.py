import logging
from typing import List, Type, Any
from abc import ABC, abstractmethod
from pydantic import BaseModel

from app.tools.tool_models import RetryPolicy

logger = logging.getLogger(__name__)


class BaseTool(ABC):
    """Base contract for tools, with optional streaming support."""

    name: str = ""
    description: str = ""
    capabilities: List[str] = []
    permissions: List[str] = []
    input_schema: Type[BaseModel] = BaseModel
    output_schema: Type[BaseModel] = BaseModel
    timeout: float = 30.0
    retry_policy: RetryPolicy = RetryPolicy()
    approval_required: bool = False
    operation_type: str = "read"

    @abstractmethod
    async def execute(self, **kwargs) -> Any:
        """Execute the tool and return its result."""
        raise NotImplementedError

    async def execute_stream(self, **kwargs):
        """Optional streaming interface; non-streaming tools need not override it."""
        raise NotImplementedError(f"Streaming is not supported by {self.name}.")
