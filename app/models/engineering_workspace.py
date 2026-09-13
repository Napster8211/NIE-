"""Persistent records for the owner-scoped Engineering Workspace control plane."""

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    BigInteger,
    CheckConstraint,
    Column,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import relationship

from app.database import Base


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex}"


def _now() -> datetime:
    return datetime.now(timezone.utc)


class EngineeringWorkspace(Base):
    __tablename__ = "engineering_workspaces"

    workspace_id = Column(String, primary_key=True, default=lambda: _id("ews"))
    owner_id = Column(String, nullable=False, index=True)
    name = Column(String(160), nullable=False)
    slug = Column(String(180), nullable=False)
    root_path = Column(Text, nullable=False, unique=True)
    storage_backend = Column(String(32), nullable=False, default="LOCAL")
    storage_revision = Column(BigInteger, nullable=False, default=0)
    storage_manifest_sha256 = Column(
        String(64), nullable=False, default="e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    )
    status = Column(String(32), nullable=False, default="ACTIVE", index=True)
    runtime_type = Column(String(32), nullable=False, default="LOCAL_DEVELOPMENT")
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_now, onupdate=_now)
    last_activity_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    metadata_json = Column("metadata", JSON, nullable=False, default=dict)
    archived_at = Column(DateTime(timezone=True), nullable=True)

    conversations = relationship("WorkspaceConversation", cascade="all, delete-orphan")
    executions = relationship("ToolExecution", cascade="all, delete-orphan")

    __table_args__ = (
        UniqueConstraint("owner_id", "slug", name="uq_engineering_workspace_owner_slug"),
        CheckConstraint("status IN ('ACTIVE', 'ARCHIVED', 'ERROR')", name="ck_engineering_workspace_status"),
        CheckConstraint(
            "runtime_type IN ('LOCAL_DEVELOPMENT', 'DOCKER', 'VERCEL_SANDBOX')",
            name="ck_engineering_workspace_runtime_type",
        ),
        CheckConstraint("storage_backend IN ('LOCAL', 'SUPABASE')", name="ck_engineering_workspace_storage_backend"),
        CheckConstraint("storage_revision >= 0", name="ck_engineering_workspace_storage_revision"),
        CheckConstraint(
            "length(storage_manifest_sha256) = 64",
            name="ck_engineering_workspace_manifest_sha256",
        ),
    )


class WorkspaceConversation(Base):
    __tablename__ = "workspace_conversations"

    link_id = Column(String, primary_key=True, default=lambda: _id("wcl"))
    workspace_id = Column(
        String, ForeignKey("engineering_workspaces.workspace_id", ondelete="CASCADE"), nullable=False, index=True
    )
    conversation_id = Column(String, nullable=False, index=True)
    owner_id = Column(String, nullable=False, index=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (UniqueConstraint("owner_id", "conversation_id", name="uq_workspace_conversation_owner"),)


class ToolExecution(Base):
    __tablename__ = "tool_executions"

    execution_id = Column(String, primary_key=True, default=lambda: _id("tex"))
    workspace_id = Column(
        String, ForeignKey("engineering_workspaces.workspace_id", ondelete="CASCADE"), nullable=False, index=True
    )
    owner_id = Column(String, nullable=False, index=True)
    conversation_id = Column(String, nullable=True, index=True)
    tool_name = Column(String(120), nullable=False, index=True)
    sanitized_arguments = Column(JSON, nullable=False, default=dict)
    approval_status = Column(String(32), nullable=False, default="NOT_REQUIRED")
    started_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    finished_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(String(32), nullable=False, default="RUNNING", index=True)
    exit_code = Column(Integer, nullable=True)
    duration_ms = Column(Integer, nullable=True)
    stdout = Column(Text, nullable=False, default="")
    stderr = Column(Text, nullable=False, default="")
    error_type = Column(String(120), nullable=True)
    changed_files = Column(JSON, nullable=False, default=list)
    correlation_id = Column(String(120), nullable=False, index=True)
    provider_metadata = Column(JSON, nullable=False, default=dict)

    events = relationship("ExecutionEvent", cascade="all, delete-orphan")

    __table_args__ = (
        CheckConstraint(
            "status IN ('RUNNING', 'SUCCEEDED', 'FAILED', 'SYNC_FAILED', 'TIMED_OUT', 'CANCELLED', "
            "'APPROVAL_REQUIRED')",
            name="ck_tool_execution_status",
        ),
        CheckConstraint(
            "approval_status IN ('NOT_REQUIRED', 'REQUIRED', 'GRANTED', 'CONSUMED')",
            name="ck_tool_execution_approval_status",
        ),
        CheckConstraint("duration_ms IS NULL OR duration_ms >= 0", name="ck_tool_execution_duration"),
    )


class ExecutionEvent(Base):
    __tablename__ = "execution_events"

    event_id = Column(String, primary_key=True, default=lambda: _id("eev"))
    execution_id = Column(
        String, ForeignKey("tool_executions.execution_id", ondelete="CASCADE"), nullable=False, index=True
    )
    sequence = Column(Integer, nullable=False)
    event_type = Column(String(80), nullable=False, index=True)
    payload = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (UniqueConstraint("execution_id", "sequence", name="uq_execution_event_sequence"),)


class WorkspaceFileChange(Base):
    __tablename__ = "workspace_file_changes"

    change_id = Column(String, primary_key=True, default=lambda: _id("wfc"))
    execution_id = Column(
        String, ForeignKey("tool_executions.execution_id", ondelete="CASCADE"), nullable=False, index=True
    )
    workspace_id = Column(
        String, ForeignKey("engineering_workspaces.workspace_id", ondelete="CASCADE"), nullable=False, index=True
    )
    relative_path = Column(Text, nullable=False)
    operation = Column(String(32), nullable=False)
    bytes_before = Column(Integer, nullable=False, default=0)
    bytes_after = Column(Integer, nullable=False, default=0)
    content_sha256 = Column(String(64), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)

    __table_args__ = (
        CheckConstraint("bytes_before >= 0", name="ck_workspace_file_change_bytes_before"),
        CheckConstraint("bytes_after >= 0", name="ck_workspace_file_change_bytes_after"),
    )


class WorkspaceFileObject(Base):
    """Authoritative logical-path to immutable-object mapping for one workspace."""

    __tablename__ = "engineering_workspace_files"

    file_id = Column(String, primary_key=True, default=lambda: _id("ewf"))
    workspace_id = Column(
        String, ForeignKey("engineering_workspaces.workspace_id", ondelete="CASCADE"), nullable=False, index=True
    )
    owner_id = Column(String, nullable=False, index=True)
    logical_path = Column(Text, nullable=False)
    storage_object_key = Column(Text, nullable=False)
    content_sha256 = Column(String(64), nullable=False)
    size_bytes = Column(BigInteger, nullable=False)
    revision = Column(BigInteger, nullable=False)
    execution_id = Column(String, ForeignKey("tool_executions.execution_id", ondelete="SET NULL"), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_now, onupdate=_now)

    __table_args__ = (
        UniqueConstraint("workspace_id", "logical_path", name="uq_engineering_workspace_file_path"),
        CheckConstraint("size_bytes >= 0", name="ck_engineering_workspace_file_size"),
        CheckConstraint("revision >= 0", name="ck_engineering_workspace_file_revision"),
        CheckConstraint("length(content_sha256) = 64", name="ck_engineering_workspace_file_sha256"),
        Index("ix_engineering_workspace_files_owner_workspace", "owner_id", "workspace_id"),
        Index("ix_engineering_workspace_files_workspace_revision", "workspace_id", "revision"),
    )


class WorkspaceStagedObject(Base):
    """Tracks uploaded objects that need safe commit or explicit garbage collection."""

    __tablename__ = "engineering_workspace_staged_objects"

    staging_id = Column(String, primary_key=True, default=lambda: _id("ewsobj"))
    workspace_id = Column(
        String, ForeignKey("engineering_workspaces.workspace_id", ondelete="CASCADE"), nullable=False, index=True
    )
    owner_id = Column(String, nullable=False, index=True)
    execution_id = Column(String, ForeignKey("tool_executions.execution_id", ondelete="SET NULL"), nullable=True)
    storage_object_key = Column(Text, nullable=False, unique=True)
    content_sha256 = Column(String(64), nullable=False)
    size_bytes = Column(BigInteger, nullable=False)
    status = Column(String(32), nullable=False, default="PENDING", index=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=_now, onupdate=_now)
    cleaned_at = Column(DateTime(timezone=True), nullable=True)

    __table_args__ = (
        CheckConstraint("size_bytes >= 0", name="ck_engineering_staged_object_size"),
        CheckConstraint("length(content_sha256) = 64", name="ck_engineering_staged_object_sha256"),
        CheckConstraint(
            "status IN ('PENDING', 'COMMITTED', 'CLEANED', 'CLEANUP_FAILED')",
            name="ck_engineering_staged_object_status",
        ),
        Index("ix_engineering_staged_objects_workspace_status", "workspace_id", "status"),
    )


class WorkspaceProcess(Base):
    __tablename__ = "workspace_processes"

    process_id = Column(String, primary_key=True, default=lambda: _id("wpr"))
    workspace_id = Column(
        String, ForeignKey("engineering_workspaces.workspace_id", ondelete="CASCADE"), nullable=False, index=True
    )
    owner_id = Column(String, nullable=False, index=True)
    execution_id = Column(String, ForeignKey("tool_executions.execution_id", ondelete="SET NULL"), nullable=True)
    sanitized_command = Column(JSON, nullable=False, default=list)
    runner_process_id = Column(String, nullable=True)
    permitted_port = Column(Integer, nullable=True)
    status = Column(String(32), nullable=False, default="STARTING", index=True)
    started_at = Column(DateTime(timezone=True), nullable=False, default=_now)
    stopped_at = Column(DateTime(timezone=True), nullable=True)
    health_status = Column(String(32), nullable=False, default="UNKNOWN")
    log_excerpt = Column(Text, nullable=False, default="")

    __table_args__ = (
        CheckConstraint(
            "status IN ('STARTING', 'RUNNING', 'STOPPED', 'FAILED', 'CANCELLED')",
            name="ck_workspace_process_status",
        ),
        CheckConstraint(
            "health_status IN ('UNKNOWN', 'STARTING', 'READY', 'FAILED', 'STOPPED')",
            name="ck_workspace_process_health_status",
        ),
        CheckConstraint(
            "permitted_port IS NULL OR (permitted_port >= 1024 AND permitted_port <= 65535)",
            name="ck_workspace_process_port",
        ),
    )
