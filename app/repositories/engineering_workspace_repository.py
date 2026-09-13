"""Database persistence for engineering workspaces and execution evidence."""

from collections.abc import Iterable
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import delete, exists, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.engineering_workspace import (
    EngineeringWorkspace,
    ExecutionEvent,
    ToolExecution,
    WorkspaceConversation,
    WorkspaceFileChange,
    WorkspaceFileObject,
    WorkspaceProcess,
    WorkspaceStagedObject,
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

    async def workspace_storage_state(
        self, workspace_id: str, owner_id: str
    ) -> tuple[int, str, list[WorkspaceFileObject]]:
        workspace_result = await self.session.execute(
            select(EngineeringWorkspace)
            .where(
                EngineeringWorkspace.workspace_id == workspace_id,
                EngineeringWorkspace.owner_id == owner_id,
            )
            .execution_options(populate_existing=True)
        )
        workspace = workspace_result.scalar_one_or_none()
        if workspace is None:
            raise ValueError("WORKSPACE_NOT_FOUND")
        result = await self.session.execute(
            select(WorkspaceFileObject)
            .where(
                WorkspaceFileObject.workspace_id == workspace_id,
                WorkspaceFileObject.owner_id == owner_id,
            )
            .order_by(WorkspaceFileObject.logical_path.asc())
        )
        return (
            int(workspace.storage_revision or 0),
            str(workspace.storage_manifest_sha256 or ""),
            list(result.scalars().all()),
        )

    async def commit_workspace_manifest(
        self,
        *,
        workspace_id: str,
        owner_id: str,
        execution_id: str,
        expected_revision: int,
        expected_manifest_sha256: str,
        manifest_sha256: str,
        files: list[dict[str, Any]],
        changes: list[dict[str, Any]],
        staged_objects: list[dict[str, Any]],
    ) -> int:
        """Atomically lock, compare and switch the authoritative manifest."""

        try:
            locked = await self.session.execute(
                select(EngineeringWorkspace)
                .where(
                    EngineeringWorkspace.workspace_id == workspace_id,
                    EngineeringWorkspace.owner_id == owner_id,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            workspace = locked.scalar_one_or_none()
            if workspace is None:
                raise ValueError("WORKSPACE_NOT_FOUND")
            if (
                int(workspace.storage_revision or 0) != expected_revision
                or str(workspace.storage_manifest_sha256 or "") != expected_manifest_sha256
            ):
                raise ValueError("WORKSPACE_SYNC_CONFLICT")

            next_revision = expected_revision + 1
            current_files_result = await self.session.execute(
                select(WorkspaceFileObject)
                .where(WorkspaceFileObject.workspace_id == workspace_id)
                .execution_options(populate_existing=True)
            )
            current_files = {item.logical_path: item for item in current_files_result.scalars().all()}
            desired_paths = [str(item["path"]) for item in files]
            delete_statement = delete(WorkspaceFileObject).where(WorkspaceFileObject.workspace_id == workspace_id)
            if desired_paths:
                delete_statement = delete_statement.where(WorkspaceFileObject.logical_path.not_in(desired_paths))
            await self.session.execute(delete_statement)
            for item in files:
                path = str(item["path"])
                record = current_files.get(path)
                if record is None:
                    record = WorkspaceFileObject(
                        workspace_id=workspace_id,
                        owner_id=owner_id,
                        logical_path=path,
                    )
                    self.session.add(record)
                record.storage_object_key = item["object_key"]
                record.content_sha256 = item["sha256"]
                record.size_bytes = item["size"]
                record.revision = next_revision
                record.execution_id = item.get("execution_id") or execution_id
                record.updated_at = utc_now()
            staged_keys = [str(item["object_key"]) for item in staged_objects]
            staged_by_key: dict[str, WorkspaceStagedObject] = {}
            if staged_keys:
                staged_result = await self.session.execute(
                    select(WorkspaceStagedObject)
                    .where(WorkspaceStagedObject.storage_object_key.in_(staged_keys))
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                staged_by_key = {item.storage_object_key: item for item in staged_result.scalars().all()}
            for item in staged_objects:
                key = str(item["object_key"])
                staged = staged_by_key.get(key)
                if (
                    staged is None
                    or staged.workspace_id != workspace_id
                    or staged.owner_id != owner_id
                    or staged.execution_id != execution_id
                    or staged.content_sha256 != item["sha256"]
                    or int(staged.size_bytes) != int(item["size"])
                ):
                    raise ValueError("STAGED_OBJECT_NOT_REGISTERED")
                staged.status = "COMMITTED"
                staged.updated_at = utc_now()
            for change in changes:
                self.session.add(
                    WorkspaceFileChange(
                        execution_id=execution_id,
                        workspace_id=workspace_id,
                        relative_path=change["path"],
                        operation=change["operation"],
                        bytes_before=change["bytes_before"],
                        bytes_after=change["bytes_after"],
                        content_sha256=change.get("sha256"),
                    )
                )
            execution = await self.session.get(ToolExecution, execution_id)
            if execution is None:
                raise ValueError("EXECUTION_NOT_FOUND")
            metadata = dict(execution.provider_metadata or {})
            metadata.update(
                {
                    "synchronization_status": "COMMITTED",
                    "workspace_revision_before": expected_revision,
                    "workspace_revision_after": next_revision,
                }
            )
            execution.provider_metadata = metadata
            workspace.storage_revision = next_revision
            workspace.storage_manifest_sha256 = manifest_sha256
            workspace.updated_at = utc_now()
            workspace.last_activity_at = utc_now()
            await self.session.commit()
            return next_revision
        except Exception:
            await self.session.rollback()
            raise

    async def record_pending_staged_objects(
        self,
        *,
        workspace_id: str,
        owner_id: str,
        execution_id: str,
        objects: list[dict[str, Any]],
    ) -> None:
        try:
            keys = [str(item["object_key"]) for item in objects]
            existing_by_key: dict[str, WorkspaceStagedObject] = {}
            if keys:
                existing_result = await self.session.execute(
                    select(WorkspaceStagedObject)
                    .where(WorkspaceStagedObject.storage_object_key.in_(keys))
                    .with_for_update()
                    .execution_options(populate_existing=True)
                )
                existing_by_key = {item.storage_object_key: item for item in existing_result.scalars().all()}
            for item in objects:
                key = str(item["object_key"])
                staged = existing_by_key.get(key)
                if staged is None:
                    staged = WorkspaceStagedObject(
                        workspace_id=workspace_id,
                        owner_id=owner_id,
                        execution_id=execution_id,
                        storage_object_key=key,
                        content_sha256=item["sha256"],
                        size_bytes=item["size"],
                    )
                    self.session.add(staged)
                elif (
                    staged.workspace_id != workspace_id
                    or staged.owner_id != owner_id
                    or staged.execution_id != execution_id
                    or staged.content_sha256 != item["sha256"]
                    or int(staged.size_bytes) != int(item["size"])
                    or staged.status == "COMMITTED"
                ):
                    raise ValueError("STAGED_OBJECT_CONFLICT")
                staged.status = "PENDING"
                staged.cleaned_at = None
                staged.updated_at = utc_now()
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            raise

    async def mark_staged_objects_cleanup_failed(self, object_keys: list[str]) -> None:
        if not object_keys:
            return
        await self.session.execute(
            update(WorkspaceStagedObject)
            .where(WorkspaceStagedObject.storage_object_key.in_(object_keys))
            .values(status="CLEANUP_FAILED", updated_at=utc_now())
        )
        await self.session.commit()

    async def abandoned_staged_objects(
        self,
        limit: int = 100,
        min_age_seconds: int = 3600,
    ) -> list[WorkspaceStagedObject]:
        cutoff = utc_now() - timedelta(seconds=max(0, min_age_seconds))
        active_execution = exists().where(
            ToolExecution.execution_id == WorkspaceStagedObject.execution_id,
            ToolExecution.status == "RUNNING",
            ToolExecution.started_at > cutoff,
        )
        result = await self.session.execute(
            select(WorkspaceStagedObject)
            .where(
                WorkspaceStagedObject.status.in_(("PENDING", "CLEANUP_FAILED")),
                WorkspaceStagedObject.created_at <= cutoff,
                ~active_execution,
            )
            .order_by(WorkspaceStagedObject.created_at.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def mark_staged_objects_cleaned(self, object_keys: list[str]) -> None:
        if not object_keys:
            return
        await self.session.execute(
            update(WorkspaceStagedObject)
            .where(WorkspaceStagedObject.storage_object_key.in_(object_keys))
            .values(status="CLEANED", cleaned_at=utc_now(), updated_at=utc_now())
        )
        await self.session.commit()

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
