"""Database persistence for engineering workspaces and execution evidence."""

from collections.abc import Iterable
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.engineering_workspace import (
    EngineeringWorkspace,
    ExecutionEvent,
    ToolExecution,
    WorkspaceConversation,
    WorkspaceFileChange,
    WorkspaceProcess,
)
from app.models.memory_models import Conversation


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class EngineeringWorkspaceRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_workspace(
        self,
        record: EngineeringWorkspace,
        conversation_id: str | None = None,
    ) -> EngineeringWorkspace:
        self.session.add(record)
        if conversation_id:
            self.session.add(
                WorkspaceConversation(
                    workspace_id=record.workspace_id,
                    conversation_id=conversation_id,
                    owner_id=record.owner_id,
                )
            )
        try:
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            raise
        await self.session.refresh(record)
        return record

    async def get_workspace(self, workspace_id: str, owner_id: str) -> EngineeringWorkspace | None:
        result = await self.session.execute(
            select(EngineeringWorkspace).where(
                EngineeringWorkspace.workspace_id == workspace_id,
                EngineeringWorkspace.owner_id == owner_id,
            )
        )
        return result.scalar_one_or_none()

    async def conversation_owned_by_user(self, conversation_id: str, owner_id: str) -> bool:
        result = await self.session.execute(
            select(Conversation.id).where(
                Conversation.id == conversation_id,
                Conversation.user_id == owner_id,
            )
        )
        return result.scalar_one_or_none() is not None

    async def list_workspaces(self, owner_id: str, include_archived: bool = False) -> list[EngineeringWorkspace]:
        statement = select(EngineeringWorkspace).where(EngineeringWorkspace.owner_id == owner_id)
        if not include_archived:
            statement = statement.where(EngineeringWorkspace.status != "ARCHIVED")
        result = await self.session.execute(statement.order_by(EngineeringWorkspace.last_activity_at.desc()))
        return list(result.scalars().all())

    async def workspace_for_conversation(self, conversation_id: str, owner_id: str) -> EngineeringWorkspace | None:
        result = await self.session.execute(
            select(EngineeringWorkspace)
            .join(WorkspaceConversation, WorkspaceConversation.workspace_id == EngineeringWorkspace.workspace_id)
            .where(
                WorkspaceConversation.conversation_id == conversation_id,
                WorkspaceConversation.owner_id == owner_id,
                EngineeringWorkspace.owner_id == owner_id,
            )
        )
        return result.scalar_one_or_none()

    async def attach_conversation(
        self, workspace_id: str, conversation_id: str, owner_id: str
    ) -> WorkspaceConversation:
        existing = await self.session.execute(
            select(WorkspaceConversation).where(
                WorkspaceConversation.owner_id == owner_id,
                WorkspaceConversation.conversation_id == conversation_id,
            )
        )
        link = existing.scalar_one_or_none()
        if link:
            if link.workspace_id != workspace_id:
                raise ValueError("CONVERSATION_ALREADY_ATTACHED")
            return link
        link = WorkspaceConversation(
            workspace_id=workspace_id,
            conversation_id=conversation_id,
            owner_id=owner_id,
        )
        self.session.add(link)
        await self.session.commit()
        await self.session.refresh(link)
        return link

    async def conversation_ids(self, workspace_id: str, owner_id: str) -> list[str]:
        result = await self.session.execute(
            select(WorkspaceConversation.conversation_id).where(
                WorkspaceConversation.workspace_id == workspace_id,
                WorkspaceConversation.owner_id == owner_id,
            )
        )
        return list(result.scalars().all())

    async def update_workspace(self, workspace: EngineeringWorkspace) -> EngineeringWorkspace:
        workspace.updated_at = utc_now()
        workspace.last_activity_at = utc_now()
        await self.session.commit()
        await self.session.refresh(workspace)
        return workspace

    async def begin_execution(self, execution: ToolExecution) -> ToolExecution:
        self.session.add(execution)
        await self.session.commit()
        await self.session.refresh(execution)
        return execution

    async def finish_execution(
        self,
        execution: ToolExecution,
        *,
        status: str,
        exit_code: int | None,
        duration_ms: int,
        stdout: str,
        stderr: str,
        error_type: str | None,
        changed_files: Iterable[str],
        provider_metadata: dict[str, Any] | None = None,
    ) -> ToolExecution:
        execution.status = status
        execution.exit_code = exit_code
        execution.duration_ms = duration_ms
        execution.stdout = stdout
        execution.stderr = stderr
        execution.error_type = error_type
        execution.changed_files = list(changed_files)
        execution.provider_metadata = provider_metadata or {}
        execution.finished_at = utc_now()
        await self.session.commit()
        await self.session.refresh(execution)
        return execution

    async def add_event(self, execution_id: str, event_type: str, payload: dict[str, Any]) -> ExecutionEvent:
        existing = await self.session.execute(
            select(ExecutionEvent.sequence)
            .where(ExecutionEvent.execution_id == execution_id)
            .order_by(ExecutionEvent.sequence.desc())
            .limit(1)
        )
        sequence = (existing.scalar_one_or_none() or 0) + 1
        event = ExecutionEvent(
            execution_id=execution_id,
            sequence=sequence,
            event_type=event_type,
            payload=payload,
        )
        self.session.add(event)
        await self.session.commit()
        await self.session.refresh(event)
        return event

    async def add_file_change(
        self,
        *,
        execution_id: str,
        workspace_id: str,
        relative_path: str,
        operation: str,
        bytes_before: int,
        bytes_after: int,
        content_sha256: str | None,
    ) -> WorkspaceFileChange:
        change = WorkspaceFileChange(
            execution_id=execution_id,
            workspace_id=workspace_id,
            relative_path=relative_path,
            operation=operation,
            bytes_before=bytes_before,
            bytes_after=bytes_after,
            content_sha256=content_sha256,
        )
        self.session.add(change)
        await self.session.commit()
        return change

    async def get_execution(self, execution_id: str, workspace_id: str, owner_id: str) -> ToolExecution | None:
        result = await self.session.execute(
            select(ToolExecution).where(
                ToolExecution.execution_id == execution_id,
                ToolExecution.workspace_id == workspace_id,
                ToolExecution.owner_id == owner_id,
            )
        )
        return result.scalar_one_or_none()

    async def consume_execution_approval(self, execution_id: str, workspace_id: str, owner_id: str) -> None:
        result = await self.session.execute(
            update(ToolExecution)
            .where(
                ToolExecution.execution_id == execution_id,
                ToolExecution.workspace_id == workspace_id,
                ToolExecution.owner_id == owner_id,
                ToolExecution.status == "APPROVAL_REQUIRED",
                ToolExecution.approval_status == "REQUIRED",
            )
            .values(approval_status="CONSUMED")
        )
        if result.rowcount != 1:
            await self.session.rollback()
            raise ValueError("EXECUTION_APPROVAL_NOT_AVAILABLE")
        await self.session.commit()

    async def list_executions(self, workspace_id: str, owner_id: str, limit: int = 100) -> list[ToolExecution]:
        result = await self.session.execute(
            select(ToolExecution)
            .where(ToolExecution.workspace_id == workspace_id, ToolExecution.owner_id == owner_id)
            .order_by(ToolExecution.started_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def list_events(self, execution_id: str) -> list[ExecutionEvent]:
        result = await self.session.execute(
            select(ExecutionEvent)
            .where(ExecutionEvent.execution_id == execution_id)
            .order_by(ExecutionEvent.sequence.asc())
        )
        return list(result.scalars().all())

    async def create_process(self, process: WorkspaceProcess) -> WorkspaceProcess:
        self.session.add(process)
        await self.session.commit()
        await self.session.refresh(process)
        return process

    async def get_process(self, process_id: str, workspace_id: str, owner_id: str) -> WorkspaceProcess | None:
        result = await self.session.execute(
            select(WorkspaceProcess).where(
                WorkspaceProcess.process_id == process_id,
                WorkspaceProcess.workspace_id == workspace_id,
                WorkspaceProcess.owner_id == owner_id,
            )
        )
        return result.scalar_one_or_none()
