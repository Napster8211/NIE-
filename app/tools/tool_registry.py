import logging
from typing import Dict, List, Type, Any
from app.tools.base_tool import BaseTool

logger = logging.getLogger(__name__)

class ToolRegistry:
    """Manages dynamic discovery and registration of system tools."""
    
    def __init__(self):
        self._tools: Dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        """Registers a tool instance dynamically."""
        if tool.name in self._tools:
            logger.warning(f"[ToolRegistry] Overwriting existing tool: {tool.name}")
        self._tools[tool.name] = tool
        logger.info(f"[ToolRegistry] Successfully registered: {tool.name}")

    def get_tool(self, name: str) -> BaseTool:
        """Retrieves a tool by name."""
        if name not in self._tools:
            logger.error(f"[ToolRegistry] Requested tool not found: {name}")
            raise KeyError(f"Tool '{name}' is not registered.")
        return self._tools[name]

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        """Exports all registered tools as JSON schema for LLM ingestion."""
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema.model_json_schema(),
            }
            for tool in self._tools.values()
        ]

# Create a global singleton instance for the rest of the app to import
tool_registry = ToolRegistry()

# --- REGISTER ACTIVE TOOLS HERE ---
try:
    from app.tools.url_reader import UrlReaderTool
    tool_registry.register(UrlReaderTool())
except ImportError as e:
    logger.error(f"[ToolRegistry] Failed to import UrlReaderTool: {e}")