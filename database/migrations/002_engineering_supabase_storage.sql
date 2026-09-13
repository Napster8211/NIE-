-- Supabase Storage-backed Engineering Workspace manifests.
-- Forward-only, non-destructive and safe to re-run.

ALTER TABLE engineering_workspaces
    ADD COLUMN IF NOT EXISTS storage_backend VARCHAR(32) NOT NULL DEFAULT 'LOCAL';
ALTER TABLE engineering_workspaces
    ADD COLUMN IF NOT EXISTS storage_revision BIGINT NOT NULL DEFAULT 0;
ALTER TABLE engineering_workspaces
    ADD COLUMN IF NOT EXISTS storage_manifest_sha256 VARCHAR(64) NOT NULL DEFAULT '';

UPDATE engineering_workspaces
SET storage_manifest_sha256 = 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855'
WHERE storage_manifest_sha256 = '';
ALTER TABLE engineering_workspaces
    ALTER COLUMN storage_manifest_sha256 SET DEFAULT 'e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855';

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ck_engineering_workspace_storage_backend'
          AND connamespace = current_schema()::regnamespace
    ) THEN
        ALTER TABLE engineering_workspaces ADD CONSTRAINT ck_engineering_workspace_storage_backend
            CHECK (storage_backend IN ('LOCAL', 'SUPABASE'));
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ck_engineering_workspace_storage_revision'
          AND connamespace = current_schema()::regnamespace
    ) THEN
        ALTER TABLE engineering_workspaces ADD CONSTRAINT ck_engineering_workspace_storage_revision
            CHECK (storage_revision >= 0);
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ck_engineering_workspace_manifest_sha256'
          AND connamespace = current_schema()::regnamespace
    ) THEN
        ALTER TABLE engineering_workspaces ADD CONSTRAINT ck_engineering_workspace_manifest_sha256
            CHECK (storage_manifest_sha256 ~ '^[0-9a-f]{64}$');
    END IF;
END
$$;

CREATE INDEX IF NOT EXISTS ix_engineering_workspaces_owner_storage
    ON engineering_workspaces(owner_id, storage_backend);

CREATE TABLE IF NOT EXISTS engineering_workspace_files (
    file_id VARCHAR PRIMARY KEY,
    workspace_id VARCHAR NOT NULL REFERENCES engineering_workspaces(workspace_id) ON DELETE CASCADE,
    owner_id VARCHAR NOT NULL,
    logical_path TEXT NOT NULL,
    storage_object_key TEXT NOT NULL,
    content_sha256 VARCHAR(64) NOT NULL,
    size_bytes BIGINT NOT NULL,
    revision BIGINT NOT NULL,
    execution_id VARCHAR NULL REFERENCES tool_executions(execution_id) ON DELETE SET NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_engineering_workspace_file_path UNIQUE (workspace_id, logical_path),
    CONSTRAINT ck_engineering_workspace_file_size CHECK (size_bytes >= 0),
    CONSTRAINT ck_engineering_workspace_file_revision CHECK (revision >= 0),
    CONSTRAINT ck_engineering_workspace_file_sha256 CHECK (content_sha256 ~ '^[0-9a-f]{64}$')
);

CREATE INDEX IF NOT EXISTS ix_engineering_workspace_files_workspace_id
    ON engineering_workspace_files(workspace_id);
CREATE INDEX IF NOT EXISTS ix_engineering_workspace_files_owner_id
    ON engineering_workspace_files(owner_id);
CREATE INDEX IF NOT EXISTS ix_engineering_workspace_files_owner_workspace
    ON engineering_workspace_files(owner_id, workspace_id);
CREATE INDEX IF NOT EXISTS ix_engineering_workspace_files_workspace_revision
    ON engineering_workspace_files(workspace_id, revision);
CREATE INDEX IF NOT EXISTS ix_engineering_workspace_files_execution_id
    ON engineering_workspace_files(execution_id);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ck_engineering_workspace_file_sha256'
          AND connamespace = current_schema()::regnamespace
    ) THEN
        ALTER TABLE engineering_workspace_files ADD CONSTRAINT ck_engineering_workspace_file_sha256
            CHECK (content_sha256 ~ '^[0-9a-f]{64}$');
    END IF;
END
$$;

CREATE TABLE IF NOT EXISTS engineering_workspace_staged_objects (
    staging_id VARCHAR PRIMARY KEY,
    workspace_id VARCHAR NOT NULL REFERENCES engineering_workspaces(workspace_id) ON DELETE CASCADE,
    owner_id VARCHAR NOT NULL,
    execution_id VARCHAR NULL REFERENCES tool_executions(execution_id) ON DELETE SET NULL,
    storage_object_key TEXT NOT NULL UNIQUE,
    content_sha256 VARCHAR(64) NOT NULL,
    size_bytes BIGINT NOT NULL,
    status VARCHAR(32) NOT NULL DEFAULT 'PENDING',
    created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
    cleaned_at TIMESTAMPTZ NULL,
    CONSTRAINT ck_engineering_staged_object_size CHECK (size_bytes >= 0),
    CONSTRAINT ck_engineering_staged_object_sha256 CHECK (content_sha256 ~ '^[0-9a-f]{64}$'),
    CONSTRAINT ck_engineering_staged_object_status CHECK (
        status IN ('PENDING', 'COMMITTED', 'CLEANED', 'CLEANUP_FAILED')
    )
);

CREATE INDEX IF NOT EXISTS ix_engineering_workspace_staged_objects_workspace_id
    ON engineering_workspace_staged_objects(workspace_id);
CREATE INDEX IF NOT EXISTS ix_engineering_workspace_staged_objects_owner_id
    ON engineering_workspace_staged_objects(owner_id);
CREATE INDEX IF NOT EXISTS ix_engineering_workspace_staged_objects_status
    ON engineering_workspace_staged_objects(status);
CREATE INDEX IF NOT EXISTS ix_engineering_staged_objects_workspace_status
    ON engineering_workspace_staged_objects(workspace_id, status);
CREATE INDEX IF NOT EXISTS ix_engineering_workspace_staged_objects_execution_id
    ON engineering_workspace_staged_objects(execution_id);

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'ck_engineering_staged_object_sha256'
          AND connamespace = current_schema()::regnamespace
    ) THEN
        ALTER TABLE engineering_workspace_staged_objects ADD CONSTRAINT ck_engineering_staged_object_sha256
            CHECK (content_sha256 ~ '^[0-9a-f]{64}$');
    END IF;
END
$$;
