# Engineering Workspace staging runbook

## Safety boundary

`render.staging.yaml` is a proposal only and disables Engineering mode. It uses a separate PostgreSQL database, a staging-only disk, explicit trusted-origin input, one API worker, and conservative quotas. Do not connect it to production data, OAuth redirects, webhooks, workspaces, or secrets.

The selected staging runner is the official Python `vercel==0.10.0` SDK behind
`VercelSandboxRunner`. Render's API process does not run user commands directly
and is not assumed to provide Docker. The staging-only Render disk is the
authoritative workspace-file store; PostgreSQL is authoritative for ownership,
conversation links, execution evidence, events, and file-change metadata.

Each approved command receives a fresh, non-persistent Vercel Sandbox. A content
revision and bounded manifest are captured, uploaded to
`/vercel/sandbox/workspace`, executed as structured argv, downloaded, validated,
and atomically reconciled only if the durable revision is unchanged. The Sandbox
is destroyed after success, failure, or cancellation. Vercel credentials stay in
the Render control plane and never enter the browser or command environment.

This release deliberately does not expose persistent dev-server previews. The
managed-process API fails closed outside trusted local development until a
separately reviewed persistent-Sandbox process adapter exists.

## Pre-deploy checklist

1. Create a separate Vercel staging frontend project, a Vercel project/team for
   Sandbox access, and a separate Render Blueprint from `render.staging.yaml`.
2. Use a separate Firebase project or staging-authorized OAuth configuration. Do not use production redirect URLs.
3. Enter all `sync: false` values in Render; never commit them.
4. Set the exact staging Vercel origin in `NIE_TRUSTED_FRONTEND_ORIGINS`.
5. Confirm the staging database and disk names before Blueprint creation.
6. Validate the Blueprint with Render CLI 2.7 or later.
7. Run `python scripts/verify_engineering_postgres.py` against a disposable
   database before the first staging migration. Set
   `NIE_TEST_POSTGRES_CONFIRM_DISPOSABLE=YES`; production-like database names
   are rejected.
8. Keep `NIE_ENGINEERING_MODE_ENABLED=false` through API/database smoke testing.
9. Opt in to exactly one real Sandbox smoke test with
   `NIE_RUN_VERCEL_SANDBOX_SMOKE=YES`. Confirm network-off default, limits,
   cancellation, secret isolation, synchronization, and Sandbox destruction.
10. Enable Engineering mode for `NIE_OWNER_FIREBASE_UIDS` only; do not populate the general-user allowlist.

## Server-side Vercel Sandbox configuration

Configure these only in Render staging, never as `VITE_*` variables:

- `VERCEL_TOKEN`
- `VERCEL_TEAM_ID`
- `VERCEL_PROJECT_ID`
- `NIE_ENGINEERING_SANDBOX_IMAGE`
- `NIE_ENGINEERING_SANDBOX_SNAPSHOT_ID` (optional)
- `NIE_ENGINEERING_SANDBOX_TIMEOUT_SECONDS`
- `NIE_ENGINEERING_SANDBOX_VCPUS`
- `NIE_ENGINEERING_SANDBOX_MEMORY_MB`
- `NIE_ENGINEERING_SYNCHRONIZATION_TIMEOUT_SECONDS`
- `NIE_ENGINEERING_CLEANUP_TIMEOUT_SECONDS`
- `NIE_ENGINEERING_NETWORK_POLICY`
- `NIE_ENGINEERING_NETWORK_ALLOWLIST`

Keep `NIE_ENGINEERING_NETWORK_POLICY=deny_all` during initial staging. An
approved dependency installation also requires authenticated user approval and
`allow_network=true`. Only after a separate network-policy smoke test may the
owner set the policy to `allowlist`; the model cannot enable networking.

The adapter requests one vCPU and 2048 MB by default through the SDK's
`SandboxResources`. These are explicit Sandbox resources; the separate
`NIE_ENGINEERING_CPU_LIMIT`, `NIE_ENGINEERING_MEMORY_LIMIT`, and
`NIE_ENGINEERING_PROCESS_LIMIT` settings apply to the retained Docker/local
runner contract and are not presented as Vercel-enforced process limits.

The adapter uses `vercel/sandbox/universal:latest` without a snapshot. An
optional snapshot may contain only reviewed runtimes and build tools. Create it
from a non-production Vercel project using the current official SDK/CLI process,
never upload user workspaces or secrets, then place only its identifier in
`NIE_ENGINEERING_SANDBOX_SNAPSHOT_ID`. Snapshot creation consumes external
resources and is not performed automatically.

## Disposable PostgreSQL verification command

```powershell
$env:NIE_ENV='test'
$env:NIE_TEST_POSTGRES_URL='<disposable URL whose database name includes test or staging>'
$env:NIE_TEST_POSTGRES_CONFIRM_DISPOSABLE='YES'
.\.venv\Scripts\python.exe scripts\verify_engineering_postgres.py
```

The verifier applies the migration repeatedly, checks real tables, columns,
indexes, constraints and foreign keys, writes all six record types, reconnects,
reads the evidence, verifies cascade cleanup, and drops only its unique schema.
Missing PostgreSQL fails explicitly; SQLite and mocks are not substitutes.

## Deployment order

1. Create separate staging identity, PostgreSQL, Render disk, Vercel frontend,
   Vercel Sandbox project/team, and scoped server-side token.
2. Run the disposable PostgreSQL verifier.
3. Configure Render staging with Engineering mode disabled and deploy the API.
4. Apply migration 001 through the Render pre-deploy command.
5. Confirm `/health` reports staging and a configured `vercel_sandbox` runner.
6. Run the explicitly opted-in one-Sandbox smoke test.
7. Deploy the separate frontend pointed only to the staging API.
8. Verify identity, ordinary chat, CORS, cookies, and anonymous/second-user denial.
9. Enable Engineering mode for the staging owner allowlist only and redeploy
   staging.
10. Execute the full browser smoke test below.

## Staging smoke test

1. Confirm `/health` returns a successful status and the API reports the staging environment.
2. Confirm an anonymous Engineering API request returns 401.
3. Confirm a valid non-owner returns 403 while owner-only mode is enabled.
4. Run the complete `napstertec-capability-test` Standard Chat workflow from the task specification.
5. Reload the browser and API process, then verify the workspace, conversation link, execution, events, file changes, and files remain.
6. Verify a second user, traversal, absolute path, symlink escape, forbidden command, timeout, and cancellation all fail safely.
7. Verify a failed provider stream stores an incomplete message and a successful stream emits exactly one `message.completed` event.
8. Inspect runner/API logs for secrets and confirm no production hosts or data were contacted.

## Rollback

1. Set `NIE_ENGINEERING_MODE_ENABLED=false` first and redeploy the staging API.
2. Stop the isolated staging runner and cancel active staging executions.
3. Preserve the staging database and disk for forensic review; do not delete data during incident response.
4. Roll the Vercel staging project and Render staging service back to the last known-good staging deployment.
5. Restore database state only from a staging backup when a schema rollback has been separately reviewed. Migration 001 has no automatic destructive down migration.

## Manual browser verification

Until browser automation is installed, use two staging Firebase test identities and follow every browser step in the task specification. Capture the workspace ID, execution ID, stdout, stderr, exit code, duration, reload evidence, denial responses, SSE terminal events, browser console, and backend logs. A code-level or mocked test is not a substitute for this check.
