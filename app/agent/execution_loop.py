"""
NapsterTec AI - Autonomous Agent Execution Loop
Module: app/agent/execution_loop.py
"""
import asyncio
import hashlib
import json
import logging
import time
from typing import AsyncGenerator, Dict, Any, Optional, List, Set

from pydantic import BaseModel, Field, ValidationError

from app.agent.state_models import AgentState, ExecutionTrace, ToolExecution
from app.tools.tool_manager import ToolManager

logger = logging.getLogger(__name__)


class CognitiveAction(BaseModel):
    tool_name: str
    arguments: Dict[str, Any] = Field(default_factory=dict)


class CognitiveResponse(BaseModel):
    thought: str = ""
    updated_plan: List[str] = Field(default_factory=list)
    action: Optional[CognitiveAction] = None
    is_finished: bool = False
    final_answer: Optional[str] = None


class ReflectionResponse(BaseModel):
    reflection: str = ""
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    correction_task: Optional[str] = None


class AutonomousAgentLoop:
    """
    NapsterTec AI Enterprise Execution Loop.

    Robust Think -> Plan -> Act -> Observe -> Reflect -> Finish cycle.
    """

    READ_ONLY_DEDUP_TOOLS: Set[str] = {
        "workspace_reader",
        "url_reader",
        "web_search",
        "document_retrieval",
    }

    WORKSPACE_NAVIGATION_ACTIONS: Set[str] = {
        "read_file",
        "find_text",
        "search_codebase",
        "list_directory",
    }

    def __init__(
        self,
        primary_llm,
        fallback_llm,
        tool_manager: ToolManager,
        command_context: Optional[Dict[str, Any]] = None,
    ):
        self.primary_llm = primary_llm
        self.fallback_llm = fallback_llm
        self.tool_manager = tool_manager

        self.max_iterations = 15
        self.max_retries_per_step = 3

        self.max_observation_chars = 12000
        self.max_history_observation_chars = 6000

        self.command_context: Dict[str, Any] = command_context or {
            "read_only": True,
            "mutation_allowed": False,
        }

        self._successful_action_fingerprints: Set[str] = set()
        self._successful_observations: Dict[str, str] = {}

    async def execute_goal(
        self,
        session_id: str,
        goal: str,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        
        state = AgentState(session_id=session_id, goal=goal)
        yield {"type": "initialization", "state": state.model_dump()}

        while state.status == "running" and state.iteration_count < self.max_iterations:
            state.iteration_count += 1
            step_start_time = time.time()

            # Phase 1: Think & Plan
            thought_plan = await self._think_and_plan(state)
            state.current_plan = thought_plan.updated_plan or state.current_plan

            if thought_plan.is_finished:
                state.status = "completed"
                state.final_result = (
                    thought_plan.final_answer
                    or await self._synthesize_final_answer(state)
                )
                yield {"type": "completion", "state": state.model_dump()}
                break

            trace = ExecutionTrace(
                step_number=state.iteration_count,
                thought=thought_plan.thought or "Planning next action.",
            )
            yield {
                "type": "trace_update",
                "phase": "think",
                "trace": trace.model_dump(),
            }

            # Phase 2: Act
            if thought_plan.action:
                action = thought_plan.action
                trace.action = ToolExecution(
                    tool_name=action.tool_name,
                    arguments=action.arguments,
                )

                yield {
                    "type": "trace_update",
                    "phase": "act",
                    "trace": trace.model_dump(),
                }

                fingerprint = self._action_fingerprint(
                    action.tool_name,
                    action.arguments,
                )

                if (
                    action.tool_name in self.READ_ONLY_DEDUP_TOOLS
                    and fingerprint in self._successful_action_fingerprints
                ):
                    trace.observation = (
                        "DUPLICATE_ACTION_BLOCKED: This exact successful "
                        "read-only tool call already completed earlier in this "
                        "session. Reuse the existing observation and synthesize "
                        "the answer instead of repeating the tool call."
                    )
                    trace.reflection = "Required evidence was already collected. Duplicate I/O was prevented."
                    trace.confidence = 1.0
                    trace.is_error = False
                    trace.duration_seconds = round(time.time() - step_start_time, 2)

                    state.traces.append(trace)
                    yield {
                        "type": "trace_update",
                        "phase": "reflect",
                        "trace": trace.model_dump(),
                    }

                    final_answer = await self._synthesize_final_answer(state)
                    state.status = "completed"
                    state.final_result = final_answer
                    yield {"type": "completion", "state": state.model_dump()}
                    break

                # Phase 3: Observe
                observation, is_error = await self._execute_tool_with_recovery(trace.action)
                trace.observation = observation
                trace.is_error = is_error

                yield {
                    "type": "trace_update",
                    "phase": "observe",
                    "trace": trace.model_dump(),
                }

                if not is_error and action.tool_name in self.READ_ONLY_DEDUP_TOOLS:
                    self._successful_action_fingerprints.add(fingerprint)
                    self._successful_observations[fingerprint] = observation

                workspace_observation_incomplete = (
                    action.tool_name == "workspace_reader"
                    and self._workspace_observation_incomplete(
                        observation,
                        action.arguments,
                    )
                )

                # Phase 4: Reflect
                reflection_data = await self._reflect_on_observation(state, trace)
                trace.reflection = reflection_data.reflection
                trace.confidence = reflection_data.confidence

                if trace.is_error or trace.confidence < 0.5:
                    logger.warning("[AgentLoop] Low confidence or tool error detected. Triggering controlled replan.")
                    correction = (
                        reflection_data.correction_task
                        or "Analyze the failure and choose a different valid action."
                    )
                    state.current_plan.insert(0, correction)

                trace.duration_seconds = round(time.time() - step_start_time, 2)
                state.traces.append(trace)

                yield {
                    "type": "trace_update",
                    "phase": "reflect",
                    "trace": trace.model_dump(),
                }

            else:
                trace.observation = "NO_ACTION_SELECTED: No tool action was selected and the goal was not marked complete."
                trace.reflection = "The loop must either select a justified action or finish using the evidence already collected."
                trace.confidence = 0.5
                trace.duration_seconds = round(time.time() - step_start_time, 2)
                state.traces.append(trace)

                yield {
                    "type": "trace_update",
                    "phase": "reflect",
                    "trace": trace.model_dump(),
                }

                if (
                    self._successful_observations
                    and self._evidence_is_sufficient_for_synthesis(state)
                ):
                    final_answer = await self._synthesize_final_answer(state)
                    state.status = "completed"
                    state.final_result = final_answer
                    yield {"type": "completion", "state": state.model_dump()}
                    break

        if state.status == "running":
            if (
                self._successful_observations
                and self._evidence_is_sufficient_for_synthesis(state)
            ):
                state.status = "completed"
                state.final_result = await self._synthesize_final_answer(state)
                yield {"type": "completion", "state": state.model_dump()}
            else:
                state.status = "failed"
                state.final_result = f"Maximum iterations ({self.max_iterations}) reached without sufficient evidence for completion."
                yield {"type": "failure", "state": state.model_dump()}

    async def _think_and_plan(self, state: AgentState) -> CognitiveResponse:
        available_tools = self.tool_manager.registry.get_tool_schemas()
        tools_json = json.dumps(available_tools, indent=2)

        system_prompt = f"""
You are the NapsterTec Autonomous Execution Engine.

Follow the user's goal and the command authority supplied by the runtime.
Do not claim tools are unavailable when they are listed below.
Do not repeat a successful read-only tool call when its observation already
exists in execution history.

Available Tools:
{tools_json}

Return exactly one JSON object with:
{{
  "thought": "short operational reasoning",
  "updated_plan": ["remaining task 1", "remaining task 2"],
  "action": null OR {{
    "tool_name": "exact_tool_name",
    "arguments": {{}}
  }},
  "is_finished": true OR false,
  "final_answer": null OR "final response"
}}

Rules:
1. If existing observations already contain enough information to answer the goal,
   set is_finished=true and provide final_answer.
2. If a tool is required, select only one tool for this iteration.
3. Never return an empty object.
4. Never return action=null with is_finished=false unless there is genuinely no
   safe next action.
5. Respect READ_ONLY authority: never select a write/mutation tool when
   mutation_allowed is false.
6. For large source files, DO NOT blindly read the whole file. Prefer:
   workspace_reader(action="find_text", path="...", query="symbol or phrase")
   and then use read_file with start_line/max_lines around the match.
7. If a workspace observation says has_more=true or truncated_by_chars=true,
   do NOT treat the file as fully inspected unless the requested evidence is
   already present. Continue with a targeted find_text/read_file operation.
8. Never repeat the exact same successful workspace read/search unless the
   previous observation explicitly indicates that the requested evidence was
   not present and a different range/query is required.
9. CRITICAL JSON ENFORCEMENT: If the user goal asks you to "Return only" a specific string or format, you MUST STILL return the valid JSON object. Place their requested string inside the "final_answer" field. NEVER return raw text outside of the JSON.
"""

        payload = {
            "goal": state.goal,
            "current_plan": state.current_plan,
            "authority": self.command_context,
            "history": self._bounded_history(state),
        }

        raw = await self._robust_llm_call(
            prompt=json.dumps(payload),
            system=system_prompt,
        )

        return self._validate_cognitive_response(raw)

    async def _execute_tool_with_recovery(self, action: ToolExecution) -> tuple[str, bool]:
        for attempt in range(self.max_retries_per_step):
            try:
                parameters = dict(action.arguments or {})
                parameters.setdefault("context", self.command_context)

                result = await self.tool_manager.run_step(
                    tool_name=action.tool_name,
                    parameters=parameters,
                )
                observation = self._normalize_tool_result(result)
                return observation, False

            except Exception as exc:
                logger.error("[AgentLoop] Tool execution failed (Attempt %s): %s", attempt + 1, str(exc))
                if attempt == self.max_retries_per_step - 1:
                    return (f"System Error: {str(exc)}. Tool execution failed permanently after retries.", True)
                await asyncio.sleep(2 ** attempt)

        return "Unknown Execution Failure", True

    async def _reflect_on_observation(self, state: AgentState, trace: ExecutionTrace) -> ReflectionResponse:
        system_prompt = """
You are the NapsterTec Reflection Module.

Evaluate the last tool action and observation.

Return exactly:
{
  "reflection": "short operational assessment",
  "confidence": 0.0,
  "correction_task": null OR "specific recovery task"
}

A successful file read/search is normally high confidence.
Do not request the exact same successful read again.
"""
        payload = {
            "goal": state.goal,
            "intended_thought": trace.thought,
            "action_taken": trace.action.model_dump() if trace.action else None,
            "observation": self._truncate(trace.observation or "", self.max_history_observation_chars),
            "was_system_error": trace.is_error,
        }

        raw = await self._robust_llm_call(prompt=json.dumps(payload), system=system_prompt)

        try:
            return ReflectionResponse.model_validate(raw)
        except ValidationError as exc:
            logger.warning("[AgentLoop] Invalid reflection payload: %s | raw=%r", exc, raw)
            return ReflectionResponse(
                reflection="Reflection response was malformed; preserving the tool observation and continuing conservatively.",
                confidence=0.75 if not trace.is_error else 0.0,
                correction_task=(None if not trace.is_error else "Choose a different action after the tool failure.")
            )

    async def _synthesize_final_answer(self, state: AgentState) -> str:
        system_prompt = """
You are the NapsterTec Final Synthesis Module.

Answer the original goal using ONLY the execution evidence supplied below.
Do not request another tool.
Do not invent file contents, paths, or execution results.

Return EXACTLY one valid JSON object:
{
  "final_answer": "the complete user-facing answer"
}
CRITICAL: Even if the user requested "Return only: [Text]", you must output the JSON object with the requested text inside the "final_answer" string value. Do not output raw text.
"""
        evidence = self._bounded_history(state, limit=5)
        payload = {"goal": state.goal, "evidence": evidence}
        raw = await self._robust_llm_call(prompt=json.dumps(payload), system=system_prompt)

        if isinstance(raw, dict):
            final_answer = raw.get("final_answer")
            if isinstance(final_answer, str) and final_answer.strip():
                return final_answer.strip()

        return "The requested evidence was collected successfully, but the final synthesis response was malformed. Please retry the synthesis step."

    def _validate_cognitive_response(self, raw: Dict[str, Any]) -> CognitiveResponse:
        """
        Reject malformed/incomplete cognitive responses instead of silently
        turning them into repeated 'No thought generated' iterations.
        """
        
        # --- PHASE 2: NORMALIZATION BOUNDARY ---
        # Normalize semantically empty action representations to None to prevent false Pydantic failures
        normalized = False
        action_val = raw.get("action")
        
        if action_val in ["", "none", "None", "null", {}, None]:
            if "action" in raw and raw["action"] is not None:
                raw["action"] = None
                normalized = True
                logger.info("[AgentLoop] Cognitive response normalized: empty action -> None")
                
        try:
            response = CognitiveResponse.model_validate(raw)
            if normalized:
                logger.info("[AgentLoop] Cognitive response accepted after compatibility normalization")
                
        except ValidationError as exc:
            # PHASE 6: OBSERVABILITY
            logger.warning("[AgentLoop] Structured response invalid; retry justified")
            return CognitiveResponse(
                thought="Cognitive response validation failed.",
                updated_plan=["Use existing evidence if available", "Otherwise choose one valid tool action"],
                action=None,
                is_finished=False,
                final_answer=None,
            )

        if not response.thought.strip():
            response.thought = "Determine the next safe operation."

        # PHASE 3: Finished Response Validation
        if response.is_finished and not (response.final_answer and response.final_answer.strip()):
            response.is_finished = False

        return response

    def _action_fingerprint(self, tool_name: str, arguments: Dict[str, Any]) -> str:
        normalized_args = {key: value for key, value in (arguments or {}).items() if key not in {"context", "force_repeat"}}
        raw = json.dumps({"tool_name": tool_name, "arguments": normalized_args}, sort_keys=True, default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _normalize_tool_result(self, result: Any) -> str:
        payload: Any
        if hasattr(result, "model_dump"): payload = result.model_dump()
        elif isinstance(result, dict): payload = result
        else:
            payload = {
                "status": getattr(result, "status", None),
                "data": getattr(result, "data", None),
                "error": getattr(result, "error", None),
            }
            if not any(value is not None for value in payload.values()):
                payload = str(result)

        text = json.dumps(payload, ensure_ascii=False, default=str)
        return self._truncate(text, self.max_observation_chars)

    def _bounded_history(self, state: AgentState, limit: int = 3) -> List[Dict[str, Any]]:
        bounded: List[Dict[str, Any]] = []
        for trace in state.traces[-limit:]:
            item = trace.model_dump()
            if item.get("observation"):
                item["observation"] = self._truncate(str(item["observation"]), self.max_history_observation_chars)
            if item.get("reflection"):
                item["reflection"] = self._truncate(str(item["reflection"]), 2000)
            bounded.append(item)
        return bounded

    @staticmethod
    def _truncate(text: str, limit: int) -> str:
        if len(text) <= limit: return text
        omitted = len(text) - limit
        return text[:limit] + f"\n...[TRUNCATED {omitted} CHARACTERS BY AGENT LOOP]..."

    async def _robust_llm_call(self, prompt: str, system: str) -> Dict[str, Any]:
        """
        Provider failover plus lightweight structured-response recovery.
        """
        async def call_provider(provider, label: str) -> Dict[str, Any]:
            last_error: Optional[Exception] = None

            # --- PHASE 5: RETRY AMPLIFICATION SAFEGUARD ---
            # Hard request-level structured-attempt ceiling. 
            # Limited to 2 attempts to prevent cascading multiplication loops.
            for attempt in range(2):
                try:
                    result = await provider.generate_json(
                        prompt=prompt,
                        system_prompt=system,
                    )
                    
                    if not isinstance(result, dict):
                        raise TypeError(f"{label} returned non-dict JSON payload.")
                        
                    meaningful_keys = {"thought", "updated_plan", "action", "is_finished", "final_answer", "reflection", "confidence", "correction_task"}
                    
                    if not result:
                        raise ValueError(f"{label} returned an empty JSON object.")
                        
                    if not any(key in result for key in meaningful_keys):
                        raise ValueError(f"{label} returned JSON without expected fields.")
                        
                    return result
                except Exception as exc:
                    last_error = exc
                    logger.warning("[AgentLoop] %s structured response failed (attempt %s/2): %s", label, attempt + 1, str(exc))
                    if attempt == 0:
                        await asyncio.sleep(0.25)
            
            raise last_error or RuntimeError(f"{label} structured response failed.")

        try:
            return await call_provider(self.primary_llm, "Primary LLM")
        except Exception as primary_error:
            logger.warning("[AgentLoop] Primary LLM failed after structured retries: %s. Failing over to secondary.", primary_error)
            try:
                return await call_provider(self.fallback_llm, "Fallback LLM")
            except Exception as fallback_error:
                logger.critical("[AgentLoop] FATAL: Both Primary and Fallback LLMs failed: %s", fallback_error)
                return {
                    "thought": "Critical cognitive failure.",
                    "updated_plan": [],
                    "action": None,
                    "is_finished": True,
                    "final_answer": "Execution halted because the reasoning provider could not return a valid structured response."
                }

    def _workspace_observation_incomplete(self, observation: str, arguments: Dict[str, Any]) -> bool:
        try: payload = json.loads(observation)
        except Exception: return False

        data = payload.get("data", payload)
        if isinstance(data, dict) and "data" in data and isinstance(data["data"], dict):
            data = data["data"]

        if not isinstance(data, dict): return False
        action = (arguments or {}).get("action")

        if action == "read_file":
            return bool(data.get("has_more") or data.get("truncated_by_chars"))

        if action in {"find_text", "search_codebase"}: return False
        return False

    def _evidence_is_sufficient_for_synthesis(self, state: AgentState) -> bool:
        if not state.traces: return False

        last_incomplete_index: Optional[int] = None
        for index, trace in enumerate(state.traces):
            action = getattr(trace, "action", None)
            if not action or action.tool_name != "workspace_reader": continue
            if self._workspace_observation_incomplete(trace.observation or "", action.arguments or {}):
                last_incomplete_index = index

        if last_incomplete_index is None: return True

        for trace in state.traces[last_incomplete_index + 1:]:
            action = getattr(trace, "action", None)
            if not action or action.tool_name != "workspace_reader": continue
            workspace_action = (action.arguments or {}).get("action")
            if workspace_action in {"find_text", "search_codebase"}: return True
            if workspace_action == "read_file" and not self._workspace_observation_incomplete(trace.observation or "", action.arguments or {}):
                return True
        return False