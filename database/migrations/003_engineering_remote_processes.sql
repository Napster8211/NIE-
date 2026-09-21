-- Additive remote-preview state for isolated Vercel Sandbox processes.
-- Production must keep Engineering mode disabled until this migration and the
-- corresponding application release are both verified.

ALTER TABLE workspace_processes
    ADD COLUMN IF NOT EXISTS runner_provider VARCHAR(64) NOT NULL DEFAULT 'local',
    ADD COLUMN IF NOT EXISTS sandbox_identifier VARCHAR(255) NULL,
    ADD COLUMN IF NOT EXISTS preview_url TEXT NULL,
    ADD COLUMN IF NOT EXISTS workspace_revision VARCHAR(160) NULL,
    ADD COLUMN IF NOT EXISTS provider_metadata JSON NOT NULL DEFAULT '{}',
    ADD COLUMN IF NOT EXISTS last_checked_at TIMESTAMPTZ NULL,
    ADD COLUMN IF NOT EXISTS expires_at TIMESTAMPTZ NULL;

CREATE INDEX IF NOT EXISTS ix_workspace_processes_owner_status
    ON workspace_processes(owner_id, status);

CREATE UNIQUE INDEX IF NOT EXISTS uq_workspace_processes_active_workspace
    ON workspace_processes(workspace_id)
    WHERE status IN ('STARTING', 'RUNNING');
