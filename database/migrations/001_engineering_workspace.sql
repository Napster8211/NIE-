-- Engineering Workspace control-plane migration.
-- Production deployments must apply this before enabling Engineering mode.
-- SQLAlchemy metadata remains the development bootstrap path.

CREATE TABLE IF NOT EXISTS engineering_workspaces (
    workspace_id VARCHAR PRIMARY KEY,
    owner_id VARCHAR NOT NULL,
    name VARCHAR(160) NOT NULL,
    slug VARCHAR(180) NOT NULL,
    root_path TEXT NOT NULL UNIQUE,
    status VARCHAR(32) NOT NULL DEFAULT 'ACTIVE',
    runtime_type VARCHAR(32) NOT NULL DEFAULT 'LOCAL_DEVELOPMENT',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_activity_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    metadata JSON NOT NULL DEFAULT '{}',
    archived_at TIMESTAMPTZ NULL,
    CONSTRAINT uq_engineering_workspace_owner_slug UNIQUE (owner_id, slug),
    CONSTRAINT ck_engineering_workspace_status CHECK (status IN ('ACTIVE', 'ARCHIVED', 'ERROR')),
    CONSTRAINT ck_engineering_workspace_runtime_type CHECK (
        runtime_type IN ('LOCAL_DEVELOPMENT', 'DOCKER', 'VERCEL_SANDBOX')
    )
);

CREATE INDEX IF NOT EXISTS ix_engineering_workspaces_owner_id ON engineering_workspaces(owner_id);
CREATE INDEX IF NOT EXISTS ix_engineering_workspaces_status ON engineering_workspaces(status);

CREATE TABLE IF NOT EXISTS workspace_conversations (
    link_id VARCHAR PRIMARY KEY,
    workspace_id VARCHAR NOT NULL REFERENCES engineering_workspaces(workspace_id) ON DELETE CASCADE,
    conversation_id VARCHAR NOT NULL,
    owner_id VARCHAR NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_workspace_conversation_owner UNIQUE (owner_id, conversation_id)
);

CREATE INDEX IF NOT EXISTS ix_workspace_conversations_workspace_id ON workspace_conversations(workspace_id);
CREATE INDEX IF NOT EXISTS ix_workspace_conversations_conversation_id ON workspace_conversations(conversation_id);
CREATE INDEX IF NOT EXISTS ix_workspace_conversations_owner_id ON workspace_conversations(owner_id);

CREATE TABLE IF NOT EXISTS tool_executions (
    execution_id VARCHAR PRIMARY KEY,
    workspace_id VARCHAR NOT NULL REFERENCES engineering_workspaces(workspace_id) ON DELETE CASCADE,
    owner_id VARCHAR NOT NULL,
    conversation_id VARCHAR NULL,
    tool_name VARCHAR(120) NOT NULL,
    sanitized_arguments JSON NOT NULL DEFAULT '{}',
    approval_status VARCHAR(32) NOT NULL DEFAULT 'NOT_REQUIRED',
    started_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    finished_at TIMESTAMPTZ NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'RUNNING',
    exit_code INTEGER NULL,
    duration_ms INTEGER NULL,
    stdout TEXT NOT NULL DEFAULT '',
    stderr TEXT NOT NULL DEFAULT '',
    error_type VARCHAR(120) NULL,
    changed_files JSON NOT NULL DEFAULT '[]',
    correlation_id VARCHAR(120) NOT NULL,
    provider_metadata JSON NOT NULL DEFAULT '{}',
    CONSTRAINT ck_tool_execution_status CHECK (
        status IN ('RUNNING', 'SUCCEEDED', 'FAILED', 'SYNC_FAILED', 'TIMED_OUT', 'CANCELLED', 'APPROVAL_REQUIRED')
    ),
    CONSTRAINT ck_tool_execution_approval_status CHECK (
        approval_status IN ('NOT_REQUIRED', 'REQUIRED', 'GRANTED', 'CONSUMED')
    ),
    CONSTRAINT ck_tool_execution_duration CHECK (duration_ms IS NULL OR duration_ms >= 0)
);

CREATE INDEX IF NOT EXISTS ix_tool_executions_workspace_id ON tool_executions(workspace_id);
CREATE INDEX IF NOT EXISTS ix_tool_executions_owner_id ON tool_executions(owner_id);
CREATE INDEX IF NOT EXISTS ix_tool_executions_correlation_id ON tool_executions(correlation_id);
CREATE INDEX IF NOT EXISTS ix_tool_executions_conversation_id ON tool_executions(conversation_id);
CREATE INDEX IF NOT EXISTS ix_tool_executions_tool_name ON tool_executions(tool_name);
CREATE INDEX IF NOT EXISTS ix_tool_executions_status ON tool_executions(status);

CREATE TABLE IF NOT EXISTS execution_events (
    event_id VARCHAR PRIMARY KEY,
    execution_id VARCHAR NOT NULL REFERENCES tool_executions(execution_id) ON DELETE CASCADE,
    sequence INTEGER NOT NULL,
    event_type VARCHAR(80) NOT NULL,
    payload JSON NOT NULL DEFAULT '{}',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_execution_event_sequence UNIQUE (execution_id, sequence)
);

CREATE INDEX IF NOT EXISTS ix_execution_events_execution_id ON execution_events(execution_id);
CREATE INDEX IF NOT EXISTS ix_execution_events_event_type ON execution_events(event_type);

CREATE TABLE IF NOT EXISTS workspace_file_changes (
    change_id VARCHAR PRIMARY KEY,
    execution_id VARCHAR NOT NULL REFERENCES tool_executions(execution_id) ON DELETE CASCADE,
    workspace_id VARCHAR NOT NULL REFERENCES engineering_workspaces(workspace_id) ON DELETE CASCADE,
    relative_path TEXT NOT NULL,
    operation VARCHAR(32) NOT NULL,
    bytes_before INTEGER NOT NULL DEFAULT 0,
    bytes_after INTEGER NOT NULL DEFAULT 0,
    content_sha256 VARCHAR(64) NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT ck_workspace_file_change_bytes_before CHECK (bytes_before >= 0),
    CONSTRAINT ck_workspace_file_change_bytes_after CHECK (bytes_after >= 0)
);

CREATE INDEX IF NOT EXISTS ix_workspace_file_changes_execution_id ON workspace_file_changes(execution_id);
CREATE INDEX IF NOT EXISTS ix_workspace_file_changes_workspace_id ON workspace_file_changes(workspace_id);

CREATE TABLE IF NOT EXISTS workspace_processes (
    process_id VARCHAR PRIMARY KEY,
    workspace_id VARCHAR NOT NULL REFERENCES engineering_workspaces(workspace_id) ON DELETE CASCADE,
    owner_id VARCHAR NOT NULL,
    execution_id VARCHAR NULL REFERENCES tool_executions(execution_id) ON DELETE SET NULL,
    sanitized_command JSON NOT NULL DEFAULT '[]',
    runner_process_id VARCHAR NULL,
    permitted_port INTEGER NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'STARTING',
    started_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    stopped_at TIMESTAMPTZ NULL,
    health_status VARCHAR(32) NOT NULL DEFAULT 'UNKNOWN',
    log_excerpt TEXT NOT NULL DEFAULT '',
    CONSTRAINT ck_workspace_process_status CHECK (
        status IN ('STARTING', 'RUNNING', 'STOPPED', 'FAILED', 'CANCELLED')
    ),
    CONSTRAINT ck_workspace_process_health_status CHECK (
        health_status IN ('UNKNOWN', 'STARTING', 'READY', 'FAILED', 'STOPPED')
    ),
    CONSTRAINT ck_workspace_process_port CHECK (
        permitted_port IS NULL OR (permitted_port >= 1024 AND permitted_port <= 65535)
    )
);

CREATE INDEX IF NOT EXISTS ix_workspace_processes_workspace_id ON workspace_processes(workspace_id);
CREATE INDEX IF NOT EXISTS ix_workspace_processes_owner_id ON workspace_processes(owner_id);
CREATE INDEX IF NOT EXISTS ix_workspace_processes_status ON workspace_processes(status);

DO $$
BEGIN
    -- A clean disposable Engineering schema may not include the legacy chat
    -- tables. Existing application databases do, so extend the table only
    -- when it is present instead of making this migration impossible to
    -- verify independently.
    IF to_regclass('messages') IS NOT NULL THEN
        ALTER TABLE messages ADD COLUMN IF NOT EXISTS status VARCHAR(32) NOT NULL DEFAULT 'COMPLETED';
        ALTER TABLE messages ADD COLUMN IF NOT EXISTS correlation_id VARCHAR(120) NULL;
        ALTER TABLE messages ADD COLUMN IF NOT EXISTS metadata JSON NOT NULL DEFAULT '{}';
        CREATE INDEX IF NOT EXISTS ix_messages_correlation_id ON messages(correlation_id);
    END IF;
END
$$;
