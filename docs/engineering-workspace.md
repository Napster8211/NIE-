# Engineering Workspace

Engineering mode is an authenticated, user-scoped execution control plane for Standard Chat. A Firebase ID token is verified by NIE for browser calls and the resulting UID becomes the server-controlled workspace owner ID. Existing Director sessions remain compatible, but Engineering mode does not require Executive OS and never accepts a client-supplied owner ID. It stores ownership and execution evidence in PostgreSQL and stores project files beneath `NIE_ENGINEERING_WORKSPACE_ROOT`.

When Standard Chat has a Firebase identity, its memory requests use the same verified UID. A conversation can be linked to a workspace only when the persisted conversation owner matches the authenticated workspace owner. Legacy anonymous chat remains isolated under the non-execution `local_user` profile; it cannot authorize Engineering APIs or be attached to an authenticated user's workspace.

## Local development

1. Set `NIE_ENV=development`, `NIE_ENGINEERING_RUNNER=local`, and a durable `NIE_ENGINEERING_WORKSPACE_ROOT`.
2. Apply the migration with `python scripts/apply_engineering_migration.py` or start the application against an empty development database so SQLAlchemy can create the tables.
3. Sign in from Standard Chat, enable **Engineering**, and create/select a workspace.

The local runner is intended only for trusted development. It is not a secure multi-tenant sandbox.

Authorization is controlled by `NIE_ENGINEERING_MODE_ENABLED`. `NIE_ENGINEERING_OWNER_ONLY=true` restricts the initial release to Firebase UIDs in `NIE_OWNER_FIREBASE_UIDS`. When owner-only mode is disabled, `NIE_ENGINEERING_ALLOWED_FIREBASE_UIDS` can hold a comma-separated release allowlist. Leaving that allowlist empty permits any Firebase identity verified for this project, so production should use the owner-only default until multi-user quotas and an isolated shared runner are verified.

## Staging and production

Staging uses the official Vercel Sandbox Python SDK through
`VercelSandboxRunner`. A staging-only mounted Render disk remains the durable
file source. Each command synchronizes a bounded content manifest into a fresh
microVM and back under an optimistic content revision. Network is deny-all by
default. See `docs/engineering-staging.md` for exact resource setup and
activation order.

The Docker runner remains available for a future self-hosted production runner.
Build the image with:

```text
docker build -f docker/engineering-runner.Dockerfile -t napstertec-engineering-runner:local .
```

On a trusted Docker host, configure `NIE_ENGINEERING_RUNNER=docker`. The runner uses a non-root user, a read-only base filesystem, no network by default, bounded CPU/memory/PIDs, a disposable container, and only the selected workspace bind-mounted at `/workspace`. Never mount the Docker socket into the runner container.

Sandbox-local and serverless filesystems are not authoritative workspace
storage. A horizontally scaled production control plane additionally requires
shared object storage (or equivalent), a distributed workspace lock, and shared
execution admission control. Staging is intentionally one API worker because
its file lock is process-local.

Dependency installation is separately classified, requires an explicit authenticated owner approval, and is audited. Remote Git operations, shell interpreters, system administration, broad deletion, and host paths are forbidden.

Command concurrency, output size, duration, workspace size, file size, container CPU, memory, and process counts are independently bounded through the `NIE_ENGINEERING_*` settings. These limits are enforced per API worker; a production remote runner must add shared admission control when the API is horizontally scaled.
