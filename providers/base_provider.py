from abc import ABC, abstractmethod
from typing import List, Dict, Any, AsyncGenerator

class BaseProvider(ABC):
    @abstractmethod
    async def initialize(self) -> None:
        pass

    @abstractmethod
    async def chat(self, messages: List[Dict[str, str]], model: str, **kwargs) -> Dict[str, Any]:
        pass

    @abstractmethod
    async def stream_chat(self, messages: List[Dict[str, str]], model: str, **kwargs) -> AsyncGenerator[str, None]:
        pass

    @abstractmethod
    async def list_models(self) -> List[Dict[str, Any]]:
        pass

    @abstractmethod
    async def health_check(self) -> Dict[str, Any]:
        pass