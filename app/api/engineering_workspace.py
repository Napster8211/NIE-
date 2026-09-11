"""Authenticated Standard Chat APIs for persistent engineering workspaces."""

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db_session
from app.repositories.engineering_workspace_repository import EngineeringWorkspaceRepository
from app.schemas.engineering_workspace import (
    EngineeringChatRequest,
    FileCreateInput,
    FilePatchInput,
    ProcessResponse,
    ProcessStartInput,
    StructuredToolResult,
    ToolInvocation,
    WorkspaceAttach,
    WorkspaceCreate,
    WorkspaceResponse,
    WorkspaceUpdate,
)
from app.services.engineering_authorization import (
    EngineeringPrincipal as DirectorPrincipal,
)
from app.services.engineering_authorization import (
    require_engineering_access as require_director_session,
)
from app.services.engineering_authorization import (
    require_engineering_mutation as require_browser_mutation,
)
from app.services.engineering_chat_service import EngineeringChatError, EngineeringChatService
from app.services.engineering_execution_service import (
    ToolExecutionError,
    ToolExecutionService,
    engineering_tool_schemas,
)
from app.services.engineering_process_service import ProcessManager
from app.services.engineering_workspace_service import WorkspaceError, WorkspaceManager
from app.services.stream_events import sse_event

router = APIRouter(prefix="/engineering", tags=["Engineering Workspace"])


def _http_error(error: Exception) -> HTTPException:
    if isinstance(error, WorkspaceError):
        return HTTPException(status_code=error.http_status, detail=error.code)
    if isinstance(error, ToolExecutionError):
        return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=error.code)
    return HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="ENGINEERING_PERSISTENCE_UNAVAILABLE")


def _owner(principal: DirectorPrincipal) -> str:
    return principal.user_id


@router.get("/tools")
async def list_tool_schemas(
    _: DirectorPrincipal = Depends(require_director_session),
):
    return {"tools": engineering_tool_schemas()}


@router.post("/workspaces", response_model=WorkspaceResponse, status_code=status.HTTP_201_CREATED)
async def create_workspace(
    request: WorkspaceCreate,
    principal: DirectorPrincipal = Depends(require_browser_mutation),
    db: AsyncSession = Depends(get_db_session),
):
    try:
        return await WorkspaceManager(EngineeringWorkspaceRepository(db)).create(_owner(principal), request)
    except Exception as error:
        raise _http_error(error) from error


@router.get("/workspaces", response_model=list[WorkspaceResponse])
async def list_workspaces(
    include_archived: bool = False,
    principal: DirectorPrincipal = Depends(require_director_session),
    db: AsyncSession = Depends(get_db_session),
):
    try:
        return await WorkspaceManager(EngineeringWorkspaceRepository(db)).list(_owner(principal), include_archived)
    except Exception as error:
        raise _http_error(error) from error


@router.get("/workspaces/{workspace_id}", response_model=WorkspaceResponse)
async def get_workspace(
    workspace_id: str,
    principal: DirectorPrincipal = Depends(require_director_session),
    db: AsyncSession = Depends(get_db_session),
):
    repository = EngineeringWorkspaceRepository(db)
    manager = WorkspaceManager(repository)
    try:
        return await manager.response(await manager.require(workspace_id, _owner(principal), active=False))
    except Exception as error:
        raise _http_error(error) from error


@router.patch("/workspaces/{workspace_id}", response_model=WorkspaceResponse)
async def update_workspace(
    workspace_id: str,
    request: WorkspaceUpdate,
    principal: DirectorPrincipal = Depends(require_browser_mutation),
    db: AsyncSession = Depends(get_db_session),
):
    try:
        return await WorkspaceManager(EngineeringWorkspaceRepository(db)).update(
            workspace_id, _owner(principal), request
        )
    except Exception as error:
        raise _http_error(error) from error


@router.post("/workspaces/{workspace_id}/conversations", response_model=WorkspaceResponse)
async def attach_workspace_to_conversation(
    workspace_id: str,
    request: WorkspaceAttach,
    principal: DirectorPrincipal = Depends(require_browser_mutation),
    db: AsyncSession = Depends(get_db_session),
):
    try:
        return await WorkspaceManager(EngineeringWorkspaceRepository(db)).attach(
            workspace_id, request.conversation_id, _owner(principal)
        )
    except Exception as error:
        raise _http_error(error) from error


async def _execute(
    *,
    workspace_id: str,
    tool_name: str,
    arguments: dict,
    principal: DirectorPrincipal,
    db: AsyncSession,
    conversation_id: str | None = None,
    correlation_id: str | None = None,
) -> StructuredToolResult:
    repository = EngineeringWorkspaceRepository(db)
    manager = WorkspaceManager(repository)
    workspace = await manager.require(workspace_id, _owner(principal))
    return await ToolExecutionService(repository).execute(
        workspace=workspace,
        owner_id=_owner(principal),
        tool_name=tool_name,
        arguments=arguments,
        conversation_id=conversation_id,
        correlation_id=correlation_id,
    )


@router.post("/workspaces/{workspace_id}/tools", response_model=StructuredToolResult)
async def execute_tool(
    workspace_id: str,
    request: ToolInvocation,
    principal: DirectorPrincipal = Depends(require_browser_mutation),
    db: AsyncSession = Depends(get_db_session),
):
    try:
        return await _execute(
            workspace_id=workspace_id,
            tool_name=request.tool_name,
            arguments=request.arguments,
            principal=principal,
            db=db,
            conversation_id=request.conversation_id,
            correlation_id=request.correlation_id,
        )
    except Exception as error:
        raise _http_error(error) from error


@router.get("/workspaces/{workspace_id}/files", response_model=StructuredToolResult)
async def list_workspace_files(
    workspace_id: str,
    path: str = "",
    principal: DirectorPrincipal = Depends(require_director_session),
    db: AsyncSession = Depends(get_db_session),
):
    try:
        return await _execute(
            workspace_id=workspace_id,
            tool_name="filesystem.list",
            arguments={"path": path},
            principal=principal,
            db=db,
        )
    except Exception as error:
        raise _http_error(error) from error


@router.get("/workspaces/{workspace_id}/file", response_model=StructuredToolResult)
async def read_workspace_file(
    workspace_id: str,
    path: str = Query(..., min_length=1),
    principal: DirectorPrincipal = Depends(require_director_session),
    db: AsyncSession = Depends(get_db_session),
):
    try:
        return await _execute(
            workspace_id=workspace_id,
            tool_name="filesystem.read",
            arguments={"path": path},
            principal=principal,
            db=db,
        )
    except Exception as error:
        raise _http_error(error) from error


@router.post("/workspaces/{workspace_id}/files", response_model=StructuredToolResult)
async def create_workspace_file(
    workspace_id: str,
    request: FileCreateInput,
    principal: DirectorPrincipal = Depends(require_browser_mutation),
    db: AsyncSession = Depends(get_db_session),
):
    try:
        return await _execute(
            workspace_id=workspace_id,
            tool_name="filesystem.create",
            arguments=request.model_dump(),
            principal=principal,
            db=db,
        )
    except Exception as error:
        raise _http_error(error) from error


@router.patch("/workspaces/{workspace_id}/file", response_model=StructuredToolResult)
async def patch_workspace_file(
    workspace_id: str,
    request: FilePatchInput,
    principal: DirectorPrincipal = Depends(require_browser_mutation),
    db: AsyncSession = Depends(get_db_session),
):
    try:
        return await _execute(
            workspace_id=workspace_id,
            tool_name="filesystem.patch",
            arguments=request.model_dump(),
            principal=principal,
            db=db,
        )
    except Exception as error:
        raise _http_error(error) from error


@router.get("/workspaces/{workspace_id}/executions")
async def list_workspace_activity(
    workspace_id: str,
    limit: int = Query(default=100, ge=1, le=200),
    principal: DirectorPrincipal = Depends(require_director_session),
    db: AsyncSession = Depends(get_db_session),
):
    repository = EngineeringWorkspaceRepository(db)
    manager = WorkspaceManager(repository)
    try:
        await manager.require(workspace_id, _owner(principal), active=False)
        executions = await repository.list_executions(workspace_id, _owner(principal), limit)
        return {
            "executions": [
                {
                    "execution_id": item.execution_id,
                    "tool_name": item.tool_name,
                    "status": item.status,
                    "exit_code": item.exit_code,
                    "duration_ms": item.duration_ms,
                    "stdout": item.stdout,
                    "stderr": item.stderr,
                    "error_type": item.error_type,
                    "changed_files": item.changed_files or [],
                    "correlation_id": item.correlation_id,
                    "started_at": item.started_at,
                    "finished_at": item.finished_at,
                }
                for item in executions
            ]
        }
    except Exception as error:
        raise _http_error(error) from error


@router.get("/workspaces/{workspace_id}/executions/{execution_id}/events")
async def list_execution_events(
    workspace_id: str,
    execution_id: str,
    principal: DirectorPrincipal = Depends(require_director_session),
    db: AsyncSession = Depends(get_db_session),
):
    repository = EngineeringWorkspaceRepository(db)
    try:
        execution = await repository.get_execution(execution_id, workspace_id, _owner(principal))
        if execution is None:
            raise WorkspaceError("EXECUTION_NOT_FOUND", 404)
        events = await repository.list_events(execution_id)
        return {
            "events": [
                {
                    "event_id": event.event_id,
                    "sequence": event.sequence,
                    "type": event.event_type,
                    "payload": event.payload,
                    "created_at": event.created_at,
                }
                for event in events
            ]
        }
    except Exception as error:
        raise _http_error(error) from error


@router.post("/workspaces/{workspace_id}/executions/{execution_id}/cancel")
async def cancel_execution(
    workspace_id: str,
    execution_id: str,
    principal: DirectorPrincipal = Depends(require_browser_mutation),
    db: AsyncSession = Depends(get_db_session),
):
    repository = EngineeringWorkspaceRepository(db)
    try:
        execution = await repository.get_execution(execution_id, workspace_id, _owner(principal))
        if execution is None:
            raise WorkspaceError("EXECUTION_NOT_FOUND", 404)
        if execution.status != "RUNNING" or execution.tool_name not in {"command.run", "git.run"}:
            return {"execution_id": execution_id, "cancel_requested": False}
        cancelled = await ToolExecutionService(repository).cancel(execution_id)
        return {"execution_id": execution_id, "cancel_requested": cancelled}
    except Exception as error:
        raise _http_error(error) from error


@router.post("/workspaces/{workspace_id}/executions/{execution_id}/approve", response_model=StructuredToolResult)
async def approve_execution(
    workspace_id: str,
    execution_id: str,
    principal: DirectorPrincipal = Depends(require_browser_mutation),
    db: AsyncSession = Depends(get_db_session),
):
    repository = EngineeringWorkspaceRepository(db)
    manager = WorkspaceManager(repository)
    try:
        owner_id = _owner(principal)
        workspace = await manager.require(workspace_id, owner_id)
        requested = await repository.get_execution(execution_id, workspace_id, owner_id)
        if requested is None:
            raise WorkspaceError("EXECUTION_NOT_FOUND", 404)
        if requested.status != "APPROVAL_REQUIRED" or requested.approval_status != "REQUIRED":
            raise WorkspaceError("EXECUTION_APPROVAL_NOT_AVAILABLE", 409)
        arguments = dict(requested.sanitized_arguments or {})
        arguments["approval_granted"] = True
        await repository.consume_execution_approval(execution_id, workspace_id, owner_id)
        return await ToolExecutionService(repository).execute(
            workspace=workspace,
            owner_id=owner_id,
            tool_name=requested.tool_name,
            arguments=arguments,
            conversation_id=requested.conversation_id,
            correlation_id=requested.correlation_id,
            user_approval_verified=True,
        )
    except Exception as error:
        raise _http_error(error) from error


@router.post("/chat")
async def engineering_chat(
    body: EngineeringChatRequest,
    request: Request,
    principal: DirectorPrincipal = Depends(require_browser_mutation),
    db: AsyncSession = Depends(get_db_session),
):
    repository = EngineeringWorkspaceRepository(db)
    manager = WorkspaceManager(repository)
    try:
        workspace = await manager.require(body.workspace_id, _owner(principal))
        if body.conversation_id:
            await manager.attach(body.workspace_id, body.conversation_id, _owner(principal))
    except Exception as error:
        raise _http_error(error) from error

    async def model_generate(prompt: str) -> tuple[str, dict]:
        # Import after application startup so this router does not create a
        # second provider registry or model client.
        from app.api.endpoints import capability_router
        from app.engine.models import Capability

        content = ""
        async for chunk in capability_router.route_skill_execution(
            prompt=prompt,
            required_capabilities=[Capability.CODING],
            preferences=["openrouter", "gemini", "groq", "cerebras", "kimi", "auto"],
            cost_preference="balanced",
            reasoning_level="medium",
            max_model_cost_per_request_usd=0.03,
        ):
            content += chunk
        return content, {"router": "capability_router", "capability": "coding"}

    async def stream():
        terminal_sent = False
        correlation_id = f"cor_{__import__('uuid').uuid4().hex}"
        service = EngineeringChatService(ToolExecutionService(repository), model_generate)
        try:
            async for event_type, payload in service.run(
                workspace=workspace,
                owner_id=_owner(principal),
                prompt=body.prompt,
                conversation_id=body.conversation_id,
                max_iterations=body.max_tool_iterations,
                correlation_id=correlation_id,
            ):
                if await request.is_disconnected():
                    raise EngineeringChatError("ENGINEERING_REQUEST_CANCELLED")
                terminal_sent = event_type in {"message.completed", "message.failed"}
                yield sse_event(event_type, payload, correlation_id)
        except EngineeringChatError as error:
            if not terminal_sent:
                terminal_sent = True
                yield sse_event(
                    "message.failed",
                    {"correlation_id": correlation_id, "error": error.code, "incomplete": True},
                    correlation_id,
                )
        except Exception:
            if not terminal_sent:
                yield sse_event(
                    "message.failed",
                    {"correlation_id": correlation_id, "error": "ENGINEERING_STREAM_FAILED", "incomplete": True},
                    correlation_id,
                )

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache, no-transform", "X-Accel-Buffering": "no"},
    )


@router.post("/workspaces/{workspace_id}/processes", response_model=ProcessResponse)
async def start_workspace_process(
    workspace_id: str,
    body: ProcessStartInput,
    principal: DirectorPrincipal = Depends(require_browser_mutation),
    db: AsyncSession = Depends(get_db_session),
):
    repository = EngineeringWorkspaceRepository(db)
    manager = WorkspaceManager(repository)
    try:
        workspace = await manager.require(workspace_id, _owner(principal))
        return await ProcessManager(repository).start(workspace, _owner(principal), body)
    except Exception as error:
        raise _http_error(error) from error


@router.get("/workspaces/{workspace_id}/processes/{process_id}", response_model=ProcessResponse)
async def inspect_workspace_process(
    workspace_id: str,
    process_id: str,
    principal: DirectorPrincipal = Depends(require_director_session),
    db: AsyncSession = Depends(get_db_session),
):
    try:
        return await ProcessManager(EngineeringWorkspaceRepository(db)).inspect(
            workspace_id, _owner(principal), process_id
        )
    except Exception as error:
        raise _http_error(error) from error


@router.post("/workspaces/{workspace_id}/processes/{process_id}/stop", response_model=ProcessResponse)
async def stop_workspace_process(
    workspace_id: str,
    process_id: str,
    principal: DirectorPrincipal = Depends(require_browser_mutation),
    db: AsyncSession = Depends(get_db_session),
):
    try:
        return await ProcessManager(EngineeringWorkspaceRepository(db)).stop(
            workspace_id, _owner(principal), process_id
        )
    except Exception as error:
        raise _http_error(error) from error
