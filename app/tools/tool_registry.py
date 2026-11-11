import logging
from typing import Dict, List, Any
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

    def register_alias(self, alias: str, tool_name: str) -> None:
        """Register a compatibility lookup name for an existing tool instance."""
        if tool_name not in self._tools:
            raise KeyError(f"Cannot alias unregistered tool '{tool_name}'.")
        if alias in self._tools:
            logger.warning("[ToolRegistry] Overwriting existing tool alias: %s", alias)
        self._tools[alias] = self._tools[tool_name]
        logger.info("[ToolRegistry] Registered alias '%s' for '%s'.", alias, tool_name)

    def get_tool(self, name: str) -> BaseTool:
        """Retrieves a tool by name or alias."""
        if name not in self._tools:
            logger.error(f"[ToolRegistry] Requested tool not found: {name}")
            raise KeyError(f"Tool '{name}' is not registered.")
        return self._tools[name]

    def get_tool_schemas(self) -> List[Dict[str, Any]]:
        """Exports each registered tool once as JSON schema for LLM ingestion."""
        unique_tools = {id(tool): tool for tool in self._tools.values()}
        return [
            {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema.model_json_schema(),
            }
            for tool in unique_tools.values()
        ]

# Create a global singleton instance for the rest of the app to import
tool_registry = ToolRegistry()

# --- REGISTER ACTIVE TOOLS HERE ---
try:
    from app.tools.url_reader import UrlReaderTool
    from app.tools.plugins.search_engine import WebIntelligenceTool

    tool_registry.register(UrlReaderTool())
    tool_registry.register(WebIntelligenceTool())
    # Maintain compatibility with any agent or planner still using the old name.
    tool_registry.register_alias("web_intelligence", "web_search")
except ImportError as e:
    logger.exception("[ToolRegistry] Failed to import tools: %s", e)
