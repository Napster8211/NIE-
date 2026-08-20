from abc import ABC, abstractmethod
from typing import Any, Dict, List, Type, AsyncGenerator
from pydantic import BaseModel
from app.tools.tool_models import RetryPolicy

class BaseTool(ABC):
    """
    Enterprise tool contract. Every dynamically registered tool must inherit from this.
    """
    
    @property
    @abstractmethod
    def name(self) -> str:
        """Unique identifier for the tool."""
        pass

    @property
    @abstractmethod
    def description(self) -> str:
        """LLM-friendly description of what the tool does."""
        pass

    @property
    @abstractmethod
    def capabilities(self) -> List[str]:
        """System tags (e.g., ['network', 'read-only', 'compute'])."""
        pass

    @property
    @abstractmethod
    def permissions(self) -> List[str]:
        """Required RBAC or security permissions (e.g., ['internet_access'])."""
        pass

    @property
    @abstractmethod
    def input_schema(self) -> Type[BaseModel]:
        """Pydantic model defining strict input requirements."""
        pass

    @property
    @abstractmethod
    def output_schema(self) -> Type[BaseModel]:
        """Pydantic model defining the guaranteed output structure."""
        pass

    @property
    def timeout(self) -> float:
        """Default execution timeout in seconds."""
        return 15.0

    @property
    def retry_policy(self) -> RetryPolicy:
        """Default retry configuration."""
        return RetryPolicy()
        
    @property
    def approval_required(self) -> bool:
        """Whether this tool requires an explicit human ApprovalRequest prior to execution."""
        return False
        
    @property
    def operation_type(self) -> str:
        """Deterministic action footprint identity (e.g., 'OUTREACH', 'CRM_MUTATION')."""
        return "GENERAL"

    @abstractmethod
    async def execute(self, **kwargs) -> Any:
        """Core execution logic. Receives validated input schema fields."""
        pass
        
    async def execute_stream(self, **kwargs) -> AsyncGenerator[Any, None]:
        """Optional streaming execution logic."""
        raise NotImplementedError(f"Streaming is not supported by {self.name}.")