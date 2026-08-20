"""
NapsterTec AI Operating System - Base Agent Specification
Module: app/agent/base_agent.py
Description: Abstract Base Class defining the unified lifecycle, dependency injection,
             and extension hooks for all NapsterTec intelligent agents.
"""

import time
import logging
from abc import ABC, abstractmethod
from typing import Optional, Dict, Any

from app.agent.agent_models import (
    AgentMetadata,
    AgentContext,
    AgentResult,
    AgentPermission,
)
from app.tools.tool_manager import ToolManager
from app.router.engine import CapabilityRouter
from app.monitoring.performance_profiler import PerformanceProfiler

logger = logging.getLogger(__name__)

class ToolExecutionDenied(Exception):
    """Raised when an agent attempts to execute a tool outside its SDK allowed permissions."""
    pass

class BaseAgent(ABC):
    """
    Abstract Base Class for all NapsterTec AI OS Agents.
    Enforces a strict, standardized execution lifecycle across all future agent modules.
    """

    def __init__(
        self,
        metadata: AgentMetadata,
        tool_manager: Optional[ToolManager] = None,
        capability_router: Optional[CapabilityRouter] = None,
    ):
        self.metadata = metadata
        self.tool_manager = tool_manager
        self.capability_router = capability_router
        logger.info(f"[Agent SDK] Initialized Agent: {self.metadata.name} (v{self.metadata.version})")

    # ============================================================================
    # Dependency Injection Setters
    # ============================================================================

    def inject_dependencies(
        self,
        tool_manager: Optional[ToolManager] = None,
        capability_router: Optional[CapabilityRouter] = None,
    ) -> None:
        """Injects platform core services at runtime without instantiating them inside the agent."""
        if tool_manager:
            self.tool_manager = tool_manager
        if capability_router:
            self.capability_router = capability_router

    # ============================================================================
    # Core Lifecycle Pipeline
    # ============================================================================

    async def run(self, context: AgentContext) -> AgentResult:
        """
        Main entry point executing the standardized agent lifecycle pipeline:
        Initialize -> Validate -> Plan -> Execute -> Validate Result -> Summarize -> Complete
        """
        start_time = time.perf_counter()
        profiler = PerformanceProfiler(request_id=context.session_id)
        profiler.start(f"Agent Execution: {self.metadata.name}")

        result = AgentResult(
            success=False,
            agent_name=self.metadata.name,
            session_id=context.session_id,
        )

        try:
            # 1. Initialize Context & Environment
            await self.initialize(context)

            # 2. Validate Task Feasibility & Permissions
            if not await self.validate_task(context):
                result.errors.append(f"Task validation failed for agent '{self.metadata.name}'.")
                return result

            # 3. Pre-Planning Hook & Planning
            await self.before_plan(context)
            context.execution_plan = await self.plan(context)
            await self.after_plan(context)

            # 4. Pre-Execution Hook & Main Execution
            await self.before_execution(context)
            execution_output = await self.execute(context)
            await self.after_execution(context, execution_output)

            # Merge intermediate result
            result = execution_output
            result.duration_seconds = round(time.perf_counter() - start_time, 3)

            # 5. Validate Output Quality
            if not await self.validate_result(result):
                result.warnings.append("Output quality validation reported minor discrepancies.")

            # 6. Summarize Execution
            result.execution_summary = await self.summarize(result)

            # 7. Complete Session
            await self.complete(context, result)
            # Preserve the agent's grounded execution verdict. The lifecycle
            # must not convert a failed persistence/tool result into success
            # merely because no exception was raised.
            result.success = bool(result.success and not result.errors)

        except Exception as e:
            logger.error(f"[Agent SDK Error] Fatal failure in agent '{self.metadata.name}': {str(e)}", exc_info=True)
            result.success = False
            result.errors.append(f"Unhandled Exception: {str(e)}")
            result.duration_seconds = round(time.perf_counter() - start_time, 3)

        finally:
            profiler.end(f"Agent Execution: {self.metadata.name}")
            profiler.report()

        return result

    # ============================================================================
    # Lifecycle Hooks & Abstract Methods
    # ============================================================================

    async def initialize(self, context: AgentContext) -> None:
        """Lifecycle Step 1: Prepares runtime metadata and checks security permissions."""
        logger.info(f"[{self.metadata.name}] Initializing session {context.session_id}")
        
        # Verify required permissions against granted context permissions
        for req_perm in self.metadata.required_permissions:
            if req_perm not in context.granted_permissions:
                logger.warning(f"[{self.metadata.name}] Missing recommended permission: {req_perm}")

    async def validate_task(self, context: AgentContext) -> bool:
        """Lifecycle Step 2: Validates if the context task is satisfiable by this agent."""
        if not context.task or not context.task.strip():
            logger.error(f"[{self.metadata.name}] Task prompt is empty.")
            return False
        return True

    async def before_plan(self, context: AgentContext) -> None:
        """Hook called immediately prior to task planning."""
        pass

    async def plan(self, context: AgentContext) -> Any:
        """Lifecycle Step 3: Derives an execution strategy or uses existing context plan."""
        return context.execution_plan

    async def after_plan(self, context: AgentContext) -> None:
        """Hook called immediately after task planning succeeds."""
        pass

    async def before_execution(self, context: AgentContext) -> None:
        """Hook called immediately prior to tool execution."""
        pass

    @abstractmethod
    async def execute(self, context: AgentContext) -> AgentResult:
        """
        Lifecycle Step 4: Core reasoning and tool execution loop.
        MUST be implemented by subclasses.
        """
        pass

    async def after_execution(self, context: AgentContext, result: AgentResult) -> None:
        """Hook called immediately after the main execution loop completes."""
        pass

    async def validate_result(self, result: AgentResult) -> bool:
        """Lifecycle Step 5: Evaluates the quality and accuracy of the output."""
        return result.success or len(result.final_output) > 0

    async def summarize(self, result: AgentResult) -> str:
        """Lifecycle Step 6: Generates a human-readable execution summary."""
        tool_count = len(result.tool_calls)
        status_str = "successfully" if result.success else "with issues"
        return f"Agent '{self.metadata.name}' executed {tool_count} tool call(s) {status_str} in {result.duration_seconds}s."

    async def complete(self, context: AgentContext, result: AgentResult) -> None:
        """Lifecycle Step 7: Final cleanup, state synchronization, and metric recording."""
        logger.info(f"[{self.metadata.name}] Session {context.session_id} completed. Success: {result.success}")

    # ============================================================================
    # Tool Execution Helper
    # ============================================================================

    async def invoke_tool(self, tool_name: str, parameters: Dict[str, Any], context: AgentContext) -> Dict[str, Any]:
        """
        Safely invokes a tool via the injected ToolManager.
        Agents never execute tools directly; they request execution via the engine.
        """
        if not self.tool_manager:
            raise RuntimeError(f"[{self.metadata.name}] Cannot invoke tool '{tool_name}': ToolManager is not injected.")

        # SPRINT 2.1: Strict Runtime Permission Enforcement
        if tool_name not in self.metadata.allowed_tools and "*" not in self.metadata.allowed_tools:
            raise ToolExecutionDenied(f"ToolExecutionDenied: Agent '{self.metadata.name}' is unauthorized to execute '{tool_name}'.")

        blocked_tools = set(context.runtime_metadata.get("blocked_tools", []))
        if tool_name in blocked_tools:
            raise ToolExecutionDenied(
                f"ToolExecutionDenied: Tool '{tool_name}' is blocked by the execution authority context."
            )

        tool = self.tool_manager.registry.get_tool(tool_name)
        tool_permissions = {
            str(getattr(permission, "value", permission)).strip().lower()
            for permission in getattr(tool, "permissions", [])
        }
        forbidden_permissions = {
            str(permission).strip().lower()
            for permission in context.runtime_metadata.get("forbidden_tool_permissions", [])
        }
        forbidden = tool_permissions.intersection(forbidden_permissions)
        if forbidden:
            raise ToolExecutionDenied(
                f"ToolExecutionDenied: Tool '{tool_name}' requires prohibited permission(s): {', '.join(sorted(forbidden))}."
            )

        scoped_permissions = {
            AgentPermission.READ_EXTERNAL_DISCOVERY.value: AgentPermission.READ_EXTERNAL_DISCOVERY,
            AgentPermission.WRITE_EXTERNAL.value: AgentPermission.WRITE_EXTERNAL,
            AgentPermission.OUTREACH.value: AgentPermission.OUTREACH,
        }
        missing_scoped = [
            enum_value.value
            for permission, enum_value in scoped_permissions.items()
            if permission in tool_permissions and enum_value not in context.granted_permissions
        ]
        if missing_scoped:
            raise ToolExecutionDenied(
                f"ToolExecutionDenied: Missing scoped permission(s) for '{tool_name}': {', '.join(missing_scoped)}."
            )

        # Check budget
        if context.execution_budget.is_exhausted():
            raise RuntimeError(f"Execution budget exhausted for session {context.session_id}.")

        logger.info(f"[{self.metadata.name}] Requesting Tool Execution: '{tool_name}'")
        
        # Execute tool via central ToolManager
        tool_result = await self.tool_manager.run_step(tool_name=tool_name, parameters=parameters)
        
        # Update budget
        context.execution_budget.current_tool_calls += 1
        
        return {
            "tool_name": tool_name,
            "parameters": parameters,
            "output": tool_result
        }