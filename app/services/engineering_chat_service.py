"""Bounded structured-tool compatibility loop for Standard Chat Engineering mode."""

import asyncio
import hashlib
import json
import re
import uuid
from collections.abc import AsyncGenerator, Awaitable, Callable
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from app.schemas.engineering_workspace import StructuredToolResult
from app.services.engineering_execution_service import ToolExecutionService, engineering_tool_schemas


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ModelToolCall(_Strict):
    call_id: str = Field(min_length=1, max_length=120)
    name: str = Field(min_length=1, max_length=120)
    arguments: dict[str, Any] = Field(default_factory=dict)


class ModelToolEnvelope(_Strict):
    type: str
    calls: list[ModelToolCall] = Field(default_factory=list, max_length=4)
    content: str = ""
    evidence_execution_ids: list[str] = Field(default_factory=list)


class EngineeringIntentRouter:
    ACTIONS = re.compile(
        r"\b(build|create|implement|edit|modify|patch|debug|fix|run|execute|test|lint|format|compile|inspect|search)\b",
        re.I,
    )
    TARGETS = re.compile(
        r"\b(project|workspace|repository|repo|code|file|application|app|test|build|command|script|server)\b", re.I
    )
    EXPLANATION = re.compile(r"^(what|why|how does|explain|describe|compare|teach me)\b", re.I)

    @classmethod
    def classify(cls, prompt: str, explicit: bool = False) -> bool:
        if explicit:
            return True
        text = prompt.strip()
        if cls.EXPLANATION.search(text) and not re.search(
            r"\b(in|inside|within|my)\s+(project|workspace|repo|file)\b", text, re.I
        ):
            return False
        return bool(cls.ACTIONS.search(text) and cls.TARGETS.search(text))


class EngineeringChatError(Exception):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


class EngineeringChatService:
    CLAIM_PATTERN = re.compile(
        r"\b(created|updated|modified|deleted|renamed|ran|executed|passed|built|installed|started|stopped)\b", re.I
    )

    def __init__(
        self,
        execution_service: ToolExecutionService,
        model_generate: Callable[[str], Awaitable[tuple[str, dict[str, Any]]]],
    ):
        self.execution_service = execution_service
        self.model_generate = model_generate

    @staticmethod
    def _parse_envelope(raw: str) -> ModelToolEnvelope:
        text = raw.strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if len(lines) >= 3 and lines[-1].strip() == "```":
                text = "\n".join(lines[1:-1])
                if text.lstrip().startswith("json\n"):
                    text = text.lstrip()[5:]
        try:
            payload = json.loads(text)
            envelope = ModelToolEnvelope.model_validate(payload)
        except (json.JSONDecodeError, ValidationError) as exc:
            raise EngineeringChatError("ENGINEERING_MODEL_PROTOCOL_INVALID") from exc
        if envelope.type not in {"tool_calls", "final"}:
            raise EngineeringChatError("ENGINEERING_MODEL_PROTOCOL_INVALID")
        if envelope.type == "tool_calls" and not envelope.calls:
            raise EngineeringChatError("ENGINEERING_TOOL_CALLS_EMPTY")
        if envelope.type == "final" and not envelope.content.strip():
            raise EngineeringChatError("ENGINEERING_FINAL_CONTENT_EMPTY")
        return envelope

    @staticmethod
    def _tool_prompt(user_prompt: str, workspace_id: str, prior_results: list[dict[str, Any]]) -> str:
        schemas = engineering_tool_schemas()
        return (
            "[System Instruction]\n"
            "You are NapsterTec Engineering mode. Return ONE JSON object and no prose. "
            'Use {"type":"tool_calls","calls":[{"call_id":"...","name":"...","arguments":{...}}]} '
            'when evidence-producing work is required. Use {"type":"final","content":"...",'
            '"evidence_execution_ids":["tex_..."]} only after reviewing tool results. '
            "Never claim a file or command succeeded without a successful execution ID. "
            "Request at most four independent tool calls at once. Do not request unknown tools.\n\n"
            f"Workspace ID: {workspace_id}\n"
            f"Tool schemas: {json.dumps(schemas, separators=(',', ':'), default=str)}\n"
            f"Prior tool results: {json.dumps(prior_results, separators=(',', ':'), default=str)}\n"
            f"User request: {user_prompt}"
        )

    async def run(
        self,
        *,
        workspace: Any,
        owner_id: str,
        prompt: str,
        conversation_id: str | None,
        max_iterations: int,
        correlation_id: str | None = None,
    ) -> AsyncGenerator[tuple[str, dict[str, Any]], None]:
        correlation = correlation_id or f"cor_{uuid.uuid4().hex}"
        prior_results: list[dict[str, Any]] = []
        successful_ids: set[str] = set()
        fingerprints: set[str] = set()
        yield (
            "message.started",
            {"correlation_id": correlation, "workspace_id": workspace.workspace_id, "mode": "engineering"},
        )
        for iteration in range(1, max_iterations + 1):
            raw, provider_metadata = await self.model_generate(
                self._tool_prompt(prompt, workspace.workspace_id, prior_results)
            )
            envelope = self._parse_envelope(raw)
            if envelope.type == "final":
                referenced = set(envelope.evidence_execution_ids)
                if not referenced.issubset(successful_ids):
                    raise EngineeringChatError("ENGINEERING_EVIDENCE_REFERENCE_INVALID")
                if self.CLAIM_PATTERN.search(envelope.content) and not referenced:
                    raise EngineeringChatError("ENGINEERING_SUCCESS_CLAIM_UNVERIFIED")
                yield "message.delta", {"correlation_id": correlation, "content": envelope.content}
                yield (
                    "message.completed",
                    {
                        "correlation_id": correlation,
                        "workspace_id": workspace.workspace_id,
                        "evidence_execution_ids": sorted(referenced),
                        "provider": provider_metadata,
                        "tool_iterations": iteration - 1,
                    },
                )
                return
            for call in envelope.calls:
                fingerprint = hashlib.sha256(
                    json.dumps({"name": call.name, "arguments": call.arguments}, sort_keys=True, default=str).encode(
                        "utf-8"
                    )
                ).hexdigest()
                if fingerprint in fingerprints:
                    raise EngineeringChatError("REPEATED_IDENTICAL_TOOL_CALL")
                fingerprints.add(fingerprint)
                yield (
                    "tool.requested",
                    {
                        "correlation_id": correlation,
                        "call_id": call.call_id,
                        "tool_name": call.name,
                        "arguments": self.execution_service._sanitize(call.arguments),
                    },
                )
                started_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=1)

                async def on_started(payload: dict[str, Any]) -> None:
                    await started_queue.put(payload)

                execution_task = asyncio.create_task(
                    self.execution_service.execute(
                        workspace=workspace,
                        owner_id=owner_id,
                        tool_name=call.name,
                        arguments=call.arguments,
                        conversation_id=conversation_id,
                        correlation_id=correlation,
                        on_started=on_started,
                    )
                )
                started_task = asyncio.create_task(started_queue.get())
                done, _ = await asyncio.wait({execution_task, started_task}, return_when=asyncio.FIRST_COMPLETED)
                if started_task in done:
                    yield "tool.started", started_task.result()
                else:
                    started_task.cancel()
                result: StructuredToolResult = await execution_task
                event_type = (
                    "tool.completed"
                    if result.success
                    else ("tool.approval_required" if result.status == "APPROVAL_REQUIRED" else "tool.failed")
                )
                yield event_type, result.model_dump(mode="json")
                prior_results.append(result.model_dump(mode="json"))
                if result.success:
                    successful_ids.add(result.execution_id)
        raise EngineeringChatError("ENGINEERING_TOOL_ITERATION_LIMIT")
