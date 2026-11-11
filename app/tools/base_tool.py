import logging
from typing import Dict, List, Type, Any
from abc import ABC, abstractmethod
from pydantic import BaseModel

from app.tools.tool_models import RetryPolicy

logger = logging.getLogger(__name__)


class BaseTool(ABC):
    """Abstract base class for all tools. Any new tool must implement the required properties."""

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
        """Execute the tool with keyword arguments and return a result object or dict."""
        raise NotImplementedError

    @abstractmethod
    async def execute_stream(self, **kwargs):
        """Optional streaming implementation for tools that stream incremental output."""
        raise NotImplementedError
