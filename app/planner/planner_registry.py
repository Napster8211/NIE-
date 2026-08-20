import logging
from typing import Dict, List, Optional
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)

class ToolDefinition(BaseModel):
    """Defines an available capability that the planner can route to."""
    name: str = Field(..., description="Identifier for the tool (e.g., 'python_executor').")
    description: str = Field(..., description="Explanation of when the planner should use this tool.")
    required_parameters: List[str] = Field(default_factory=list, description="Keys expected in the step parameters.")

class PlannerRegistry:
    """Central registry for all tools available to the Planner Engine."""
    
    def __init__(self):
        self._tools: Dict[str, ToolDefinition] = {}

    def register_tool(self, tool: ToolDefinition) -> None:
        """Registers a new capability dynamically."""
        if tool.name in self._tools:
            logger.warning(f"[PlannerRegistry] Overwriting existing tool definition: {tool.name}")
        self._tools[tool.name] = tool
        logger.info(f"[PlannerRegistry] Registered capability: {tool.name}")

    def get_tool(self, name: str) -> Optional[ToolDefinition]:
        return self._tools.get(name)

    def get_available_tools_schema(self) -> List[dict]:
        """Returns the registry state formatted for LLM system prompts."""
        return [
            {
                "name": tool.name, 
                "description": tool.description,
                "parameters": tool.required_parameters
            }
            for tool in self._tools.values()
        ]