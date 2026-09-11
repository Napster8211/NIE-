"""Strict public contracts for Engineering Workspace APIs and model tools."""

from datetime import datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class WorkspaceStatus(str, Enum):
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"
    ERROR = "ERROR"


class WorkspaceCreate(StrictModel):
    name: str = Field(min_length=1, max_length=160)
    conversation_id: str | None = Field(default=None, max_length=160)
    runtime_type: Literal["LOCAL_DEVELOPMENT", "DOCKER", "VERCEL_SANDBOX"] = "LOCAL_DEVELOPMENT"
    metadata: dict[str, Any] = Field(default_factory=dict)


class WorkspaceUpdate(StrictModel):
    name: str | None = Field(default=None, min_length=1, max_length=160)
    status: WorkspaceStatus | None = None
    metadata: dict[str, Any] | None = None


class WorkspaceResponse(StrictModel):
    workspace_id: str
    name: str
    slug: str
    status: str
    runtime_type: str
    created_at: datetime
    updated_at: datetime
    last_activity_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)
    conversation_ids: list[str] = Field(default_factory=list)


class WorkspaceAttach(StrictModel):
    conversation_id: str = Field(min_length=1, max_length=160)


class FilePathInput(StrictModel):
    path: str = Field(default="", max_length=1000)


class FileCreateInput(StrictModel):
    path: str = Field(min_length=1, max_length=1000)
    content: str = Field(default="", max_length=1_000_000)
    overwrite: bool = False


class TextReplacement(StrictModel):
    old: str = Field(min_length=1, max_length=500_000)
    new: str = Field(default="", max_length=500_000)
    expected_occurrences: int = Field(default=1, ge=1, le=100)


class FilePatchInput(StrictModel):
    path: str = Field(min_length=1, max_length=1000)
    replacements: list[TextReplacement] = Field(min_length=1, max_length=100)


class FileRenameInput(StrictModel):
    source: str = Field(min_length=1, max_length=1000)
    destination: str = Field(min_length=1, max_length=1000)
    overwrite: bool = False


class SearchFilesInput(StrictModel):
    query: str = Field(min_length=1, max_length=200)
    path: str = Field(default="", max_length=1000)
    max_results: int = Field(default=100, ge=1, le=500)


class SearchContentInput(SearchFilesInput):
    case_sensitive: bool = False


class DeleteFileInput(FilePathInput):
    approval_granted: bool = False


class CommandInput(StrictModel):
    argv: list[str] = Field(min_length=1, max_length=64)
    cwd: str = Field(default="", max_length=1000)
    timeout_seconds: int = Field(default=60, ge=1, le=600)
    max_output_bytes: int = Field(default=100_000, ge=1000, le=1_000_000)
    approval_granted: bool = False
    allow_network: bool = False

    @field_validator("argv")
    @classmethod
    def validate_argv(cls, value: list[str]) -> list[str]:
        if any(not isinstance(item, str) or not item or "\x00" in item or len(item) > 2000 for item in value):
            raise ValueError("argv contains an invalid token")
        return value


class GitInput(StrictModel):
    action: Literal["init", "status", "diff", "current_branch", "create_branch", "stage", "commit"]
    files: list[str] = Field(default_factory=list, max_length=100)
    branch: str | None = Field(default=None, max_length=160)
    message: str | None = Field(default=None, max_length=500)
    approval_granted: bool = False


class ToolInvocation(StrictModel):
    tool_name: str = Field(min_length=1, max_length=120)
    arguments: dict[str, Any] = Field(default_factory=dict)
    conversation_id: str | None = Field(default=None, max_length=160)
    correlation_id: str | None = Field(default=None, max_length=120)


class StructuredToolResult(StrictModel):
    execution_id: str
    workspace_id: str
    tool_name: str
    status: Literal["RUNNING", "SUCCEEDED", "FAILED", "SYNC_FAILED", "TIMED_OUT", "CANCELLED", "APPROVAL_REQUIRED"]
    success: bool
    data: dict[str, Any] = Field(default_factory=dict)
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    duration_ms: int = 0
    error_type: str | None = None
    changed_files: list[str] = Field(default_factory=list)
    correlation_id: str
    evidence_verified: bool = False


class EngineeringChatRequest(StrictModel):
    prompt: str = Field(min_length=1, max_length=100_000)
    workspace_id: str = Field(min_length=1, max_length=160)
    conversation_id: str | None = Field(default=None, max_length=160)
    max_tool_iterations: int = Field(default=6, ge=1, le=8)


class ProcessStartInput(StrictModel):
    argv: list[str] = Field(min_length=2, max_length=32)
    cwd: str = Field(default="", max_length=1000)
    port: int = Field(ge=3000, le=9999)

    @field_validator("argv")
    @classmethod
    def validate_process_argv(cls, value: list[str]) -> list[str]:
        if any(not item or "\x00" in item or len(item) > 2000 for item in value):
            raise ValueError("argv contains an invalid token")
        return value


class ProcessResponse(StrictModel):
    process_id: str
    workspace_id: str
    status: str
    port: int | None = None
    health_status: str
    log_excerpt: str = ""
