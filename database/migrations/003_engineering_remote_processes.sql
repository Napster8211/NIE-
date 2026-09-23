-- Additive remote-preview state for isolated Vercel Sandbox processes.
-- Production must keep Engineering mode disabled until this migration and the
-- corresponding application release are both verified.

ALTER TABLE workspace_processes
    ADD COLUMN IF NOT EXISTS runner_provider VARCHAR(64) NOT NULL DEFAULT 'local',
    ADD COLUMN IF NOT EXISTS sandbox_identifier VARCHAR(255) NULL,
    ADD COLUMN IF NOT EXISTS provider_preview_url TEXT NULL,
    ADD COLUMN IF NOT EXISTS workspace_revision VARCHAR(160) NULL,
    ADD COLUMN IF NOT EXISTS provider_metadata JSON NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS startup_claim_id VARCHAR(96) NULL,
    ADD COLUMN IF NOT EXISTS startup_claimed_at TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS cleanup_claim_id VARCHAR(96) NULL,
    ADD COLUMN IF NOT EXISTS cleanup_claimed_at TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS cancellation_requested_at TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS stop_reason VARCHAR(32) NULL,
    ADD COLUMN IF NOT EXISTS terminal_reason VARCHAR(120) NULL,
    ADD COLUMN IF NOT EXISTS preview_session_token_hash VARCHAR(64) NULL,
    ADD COLUMN IF NOT EXISTS preview_session_expires_at TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS last_checked_at TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ NULL;

ALTER TABLE workspace_processes
    DROP CONSTRAINT IF EXISTS ck_workspace_process_status,
    DROP CONSTRAINT IF EXISTS ck_workspace_process_health_status,
    DROP CONSTRAINT IF EXISTS ck_workspace_process_stop_reason,
    DROP CONSTRAINT IF EXISTS ck_workspace_process_preview_token_hash;

ALTER TABLE workspace_processes
    ADD CONSTRAINT ck_workspace_process_status CHECK (
        status IN ('STARTING', 'RUNNING', 'READY', 'STOPPING', 'STOPPED', 'FAILED', 'CANCELLED', 'EXPIRED')
    ),
    ADD CONSTRAINT ck_workspace_process_health_status CHECK (
        health_status IN ('UNKNOWN', 'STARTING', 'RUNNING', 'READY', 'STOPPING', 'STOPPED',
                          'FAILED', 'CANCELLED', 'EXPIRED')
    ),
    ADD CONSTRAINT ck_workspace_process_stop_reason CHECK (
        stop_reason IS NULL OR stop_reason IN ('STOP', 'CANCEL', 'EXPIRE')
    ),
    ADD CONSTRAINT ck_workspace_process_preview_token_hash CHECK (
        preview_session_token_hash IS NULL OR length(preview_session_token_hash) = 64
    );

CREATE INDEX IF NOT EXISTS ix_workspace_processes_owner_status
    ON workspace_processes(owner_id, status);

CREATE INDEX IF NOT EXISTS ix_workspace_processes_workspace_expires
    ON workspace_processes(workspace_id, expires_at)
    WHERE status IN ('STARTING', 'RUNNING', 'READY', 'STOPPING');

DO $$
BEGIN
    IF EXISTS (
        SELECT workspace_id
        FROM workspace_processes
        WHERE status IN ('STARTING', 'RUNNING', 'READY', 'STOPPING')
        GROUP BY workspace_id
        HAVING count(*) > 1
    ) THEN
        RAISE EXCEPTION 'ENGINEERING_ACTIVE_PROCESS_DUPLICATES_DETECTED';
    END IF;
END
$$;

CREATE UNIQUE INDEX IF NOT EXISTS uq_workspace_processes_active_workspace
    ON workspace_processes(workspace_id)
    WHERE status IN ('STARTING', 'RUNNING', 'READY', 'STOPPING');
