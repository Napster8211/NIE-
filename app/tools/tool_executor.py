import asyncio
import time
import logging
from typing import Dict, Any, List, Tuple
from app.tools.base_tool import BaseTool
from app.tools.tool_models import ToolResult, ToolResultStatus
from app.services.authorization import AuthorizationGate
from app.agent.agent_models import AgentContext

logger = logging.getLogger(__name__)

class ToolExecutor:
    """Executes tools with enforced enterprise guardrails: timeout, retry, metrics, and Protected Execution Gates."""

    async def execute_tool(self, tool: BaseTool, parameters: Dict[str, Any]) -> ToolResult:
        start_time = time.time()
        retries = 0
        policy = tool.retry_policy
        
        raw_context = parameters.get("context")
        agent_context = None
        
        # SPRINT 3V SECURITY: Never instantiate AgentContext from raw dictionary parameters.
        # Direct dictionary injection is an authority forgery attack vector.
        if isinstance(raw_context, AgentContext):
            agent_context = raw_context
            
        # Identify protected tool status centrally
        is_protected = getattr(tool, "approval_required", False)
        
        # 1. ENFORCE TRUSTED CONTEXT FOR PROTECTED OPERATIONS
        if is_protected and not agent_context:
             return ToolResult(
                status=ToolResultStatus.FAILURE,
                error="AUTHORITY_MISSING: Cannot execute protected operation without trusted AgentContext.",
                execution_time_ms=(time.time() - start_time) * 1000
             )
        
        while retries <= policy.max_retries:
            try:
                # 2. Enforce strict input boundary
                validated_input = tool.input_schema(**parameters)
                
                # SPRINT 3E: PROTECTED EXECUTION GATE
                if is_protected and agent_context:
                    # Extract the exact string permissions required by the tool directly.
                    # Do NOT intersect them with granted permissions here, otherwise the gate is bypassed!
                    raw_required_perms = getattr(tool, "permissions", [])
                                
                    auth_result = AuthorizationGate.evaluate_execution(
                        agent_context=agent_context,
                        tool_name=tool.name,
                        required_permissions=raw_required_perms,
                        approval_required=is_protected,
                        operation_type=getattr(tool, "operation_type", "GENERAL"),
                        parameters=validated_input.model_dump()
                    )
                    
                    if auth_result.status != "AUTHORIZED":
                        logger.error(f"[ToolExecutor] Authorization Denied for {tool.name}: {auth_result.status} - {auth_result.error}")
                        return ToolResult(
                            status=ToolResultStatus.FAILURE,
                            error=f"{auth_result.status}: {auth_result.error}",
                            execution_time_ms=(time.time() - start_time) * 1000
                        )

                # 3. Execute with bounded timeout
                execute_args = validated_input.model_dump()
                # Ensure context bypasses Pydantic stripping and reaches the tool execute method
                if "context" in parameters:
                    execute_args["context"] = parameters["context"]
                
                raw_result = await asyncio.wait_for(
                    tool.execute(**execute_args), 
                    timeout=tool.timeout
                )
                
                # 4. Enforce strict output boundary
                validated_output = tool.output_schema.model_validate(raw_result)
                
                # SPRINT 3E: APPROVAL CONSUMPTION
                if is_protected and agent_context:
                    try:
                        AuthorizationGate.consume_approval(agent_context)
                        logger.info(f"[ToolExecutor] Approval successfully CONSUMED for {tool.name}")
                    except Exception as e:
                        logger.error(f"[ToolExecutor] UNCERTAINTY STATE: {tool.name} succeeded but consumption failed: {e}")
                        return ToolResult(
                            status=ToolResultStatus.FAILURE,
                            error="CRITICAL_UNCERTAINTY: External operation succeeded but local approval consumption failed. Automatic retries blocked to prevent duplicate side effects.",
                            execution_time_ms=(time.time() - start_time) * 1000
                        )

                execution_time = (time.time() - start_time) * 1000
                logger.info(f"[ToolExecutor] {tool.name} executed successfully in {execution_time:.2f}ms")
                
                return ToolResult(
                    status=ToolResultStatus.SUCCESS,
                    data=validated_output.model_dump(),
                    execution_time_ms=execution_time,
                    metrics={"retries": retries}
                )
                
            except asyncio.TimeoutError:
                logger.warning(f"[ToolExecutor] {tool.name} timed out after {tool.timeout}s.")
                if retries == policy.max_retries:
                    return ToolResult(
                        status=ToolResultStatus.TIMEOUT,
                        error=f"Execution exceeded {tool.timeout} seconds.",
                        execution_time_ms=(time.time() - start_time) * 1000
                    )
            except Exception as e:
                error_name = type(e).__name__
                logger.warning(f"[ToolExecutor] {tool.name} failed: {str(e)} (Type: {error_name})")
                
                if retries == policy.max_retries or error_name not in policy.retryable_exceptions:
                    return ToolResult(
                        status=ToolResultStatus.FAILURE,
                        error=str(e),
                        execution_time_ms=(time.time() - start_time) * 1000
                    )
            
            # 5. Trigger Backoff
            retries += 1
            backoff_time = policy.backoff_factor ** retries
            logger.info(f"[ToolExecutor] Retrying {tool.name} in {backoff_time}s...")
            await asyncio.sleep(backoff_time)

    async def execute_parallel(self, execution_batch: List[Tuple[BaseTool, Dict[str, Any]]]) -> List[ToolResult]:
        """Fires multiple tools simultaneously using asyncio.gather."""
        tasks = [
            self.execute_tool(tool, params) 
            for tool, params in execution_batch
        ]
        return await asyncio.gather(*tasks)