import logging
import json
from typing import Any, Protocol, Dict, Optional
from app.planner.planner_models import ExecutionPlan
from app.planner.planner_registry import PlannerRegistry

logger = logging.getLogger(__name__)

class StructuredLLMProvider(Protocol):
    """
    Dependency Injection Interface for the LLM. 
    Any provider passed to the TaskPlanner must implement this method.
    """
    async def generate_structured(self, system_prompt: str, user_prompt: str, schema_class: Any) -> Any:
        ...

class TaskPlanner:
    """
    Intelligent engine that translates user intent into a multi-step execution plan.
    """
    
    def __init__(self, registry: PlannerRegistry, llm_provider: StructuredLLMProvider):
        self.registry = registry
        self.llm_provider = llm_provider

    def _build_system_prompt(self, intent_metadata: Optional[Dict[str, Any]] = None) -> str:
        """Constructs the system prompt dynamically based on registered tools."""
        available_tools = self.registry.get_available_tools_schema()
        
        prompt = (
            "You are the NapsterTec Task Planner. Your job is to break down the user's request into a logical "
            "sequence of execution steps. You must output valid JSON matching the requested schema.\n\n"
            "AVAILABLE TOOLS:\n"
            f"{json.dumps(available_tools, indent=2)}\n\n"
            "RULES:\n"
            "- Determine if simple chat is sufficient (use 'llm' tool).\n"
            "- If real-time data is needed, use 'web_search'.\n"
            "- If a URL is provided, use 'url_reader'.\n"
            "- If complex math or code execution is needed, use 'python_executor'.\n"
            "- If images are attached, use 'image_analyzer'.\n"
        )
        
        if intent_metadata:
            prompt += f"\nINTENT METADATA:\n{json.dumps(intent_metadata)}\n"
            
        return prompt

    async def generate_plan(self, prompt: str, intent_metadata: Optional[Dict[str, Any]] = None) -> ExecutionPlan:
        """
        Analyzes the prompt and returns a validated ExecutionPlan.
        """
        logger.info("[TaskPlanner] Generating execution plan...")
        
        system_prompt = self._build_system_prompt(intent_metadata)
        
        try:
            # The injected LLM provider handles enforcing the Pydantic schema
            plan: ExecutionPlan = await self.llm_provider.generate_structured(
                system_prompt=system_prompt,
                user_prompt=prompt,
                schema_class=ExecutionPlan
            )
            
            logger.info(f"[TaskPlanner] Plan generated successfully with {plan.estimated_steps} steps.")
            return plan
            
        except Exception as e:
            logger.error(f"[TaskPlanner] Failed to generate plan: {str(e)}", exc_info=True)
            # Enterprise Error Handling: Fallback to a safe, direct LLM route
            return self._generate_fallback_plan(prompt)

    def _generate_fallback_plan(self, prompt: str) -> ExecutionPlan:
        """Failsafe plan if the LLM fails to generate structured output."""
        logger.warning("[TaskPlanner] Initiating fallback chat plan.")
        return ExecutionPlan(
            goal="Direct Chat Fallback",
            steps=[
                {
                    "tool": "llm",
                    "reason": "Fallback execution due to planner failure.",
                    "parameters": {}
                }
            ],
            requires_tools=False,
            requires_research=False,
            estimated_steps=1
        )