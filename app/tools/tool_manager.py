import logging
from typing import List, Dict, Any
from app.tools.tool_registry import ToolRegistry
from app.tools.tool_executor import ToolExecutor
from app.tools.tool_models import ToolResult

logger = logging.getLogger(__name__)

class ToolManager:
    """
    Facade for the Enterprise Tool architecture. 
    Routes instructions from the Planner into the Execution layer.
    """
    def __init__(self, registry: ToolRegistry, executor: ToolExecutor):
        self.registry = registry
        self.executor = executor

    async def run_step(self, tool_name: str, parameters: Dict[str, Any]) -> ToolResult:
        # Prevent forgery of authority metrics by agent-generated parameters
        if "context" in parameters and isinstance(parameters["context"], dict) and "granted_permissions" in parameters["context"]:
             # If an agent tries to inject a raw dict with fake permissions, pop it.
             # We rely on the caller (BaseAgent/ToolExecutor) passing the genuine AgentContext object.
             if not hasattr(parameters["context"], "model_dump"):
                 parameters.pop("context")

        """Executes a single planned tool step."""
        logger.info(f"[ToolManager] Dispatching step to: {tool_name}")
        try:
            tool = self.registry.get_tool(tool_name)
            return await self.executor.execute_tool(tool, parameters)
        except KeyError as e:
            logger.error(f"[ToolManager] Dispatch failed. {str(e)}")
            return ToolResult(
                status="FAILURE",
                error=f"Unregistered tool requested: {tool_name}"
            )

    async def run_parallel_steps(self, steps: List[Dict[str, Any]]) -> List[ToolResult]:
        """
        Executes a batch of planned steps concurrently.
        Format expected: [{"tool_name": "...", "parameters": {...}}, ...]
        """
        logger.info(f"[ToolManager] Dispatching {len(steps)} steps for parallel execution.")
        execution_batch = []
        
        for step in steps:
            try:
                tool_name = step.get("tool_name")
                tool = self.registry.get_tool(tool_name)
                execution_batch.append((tool, step.get("parameters", {})))
            except KeyError:
                logger.error(f"[ToolManager] Skipping unregistered tool in parallel batch: {tool_name}")
                
        return await self.executor.execute_parallel(execution_batch)