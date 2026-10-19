# Operations

## Native development with Railway Postgres

Use the setup commands in [README](../README.md). Application and test databases
live inside the same Railway Postgres service, with distinct non-superuser owner
roles. Each database revokes public CONNECT privileges. The test role cannot
connect to the application database. `.env` holds the application URL;
`.env.test` holds only the dedicated test URL. Both are ignored and permission 0600.

`botfluencer db upgrade` applies explicit Alembic migrations. Startup never
creates tables automatically. `run --ticks N` completes a finite rehearsal;
`serve` supervises the worker and HTTP server and exits on fatal worker failure.
Use a stable namespace to preserve pause, budgets, and action history. Separate
mock/replay and competition observation namespaces.

Pause stops new work; already reserved or in-flight work may finish. Heartbeats
continue while paused. Controls are available only through the CLI, with no public
HTTP control routes. Never manually requeue unknown or successful actions.

## Verification

```sh
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run --env-file .env.test pytest
```

Tests refuse database names without `_test`. Local integration tests skip without
a URL; CI fails if its secret is absent. The supplied test URL is restricted to a
disposable database. Tests create synthetic records with unique namespaces; do not
run maintenance or data deletion against the application database. Shared CI uses
a concurrency group so migration checks do not race another CI integration run.
For independent teams/branches needing concurrent schema work, provision separate
logical test databases and credentials in the existing service.

## Recovery

| State | Meaning | Behavior |
| --- | --- | --- |
| queued | Persisted intent | Claim when eligible |
| in_flight | Attempt reserved | Restart converts to unknown |
| unknown | Acceptance uncertain | Read-only reconciliation |
| succeeded | Acceptance confirmed | Never execute again |
| failed | Definitive rejection / attempts exhausted | Operator review |

Retries require explicit safe rejection or evidence of non-acceptance. The mock
receipt table simulates remote idempotency. The real platform executor is disabled
because its idempotency and reconciliation guarantees have not been established.

Hourly local generation/action caps count attempts, including failures, using the
database clock. They supplement shared CTN request pacing; they do not replace
competition limits. CTN backoff is persisted by team and bucket across namespaces.
A deferred feed read continues healthy heartbeat ticks without waiting in a call.

## Health and logs

- `/health/live`: HTTP process responds.
- `/health/ready`: initialized storage and active/standby supervisor, for deployment.
- `/health/worker`: owned workload, fresh completed tick, and reachable database.

A replacement may pass deployment readiness in standby while the previous worker
holds the session advisory lock. It cannot execute until ownership transfers.
Fatal worker failures exit the process; stale worker health returns HTTP 503.
Use the direct Postgres connection, never transaction-mode pooling. Native defaults
allow a 30-second operation and 60-second heartbeat threshold for public-network
latency; the hosted test harness allows 60/90 seconds. Failure tests still assert
that a stalled generator was actually entered before its deadline expires.

JSON logs identify events, UTC times, correlation IDs, and action IDs. They omit
post content, prompts, tokens, connection strings, and raw exception details.
Inspect private ledger data through an authenticated database connection.

## Rollback and backups

Apply additive migrations before starting a new application version. Roll back
the application to a schema-compatible commit; never automatically downgrade the
schema. Configure Railway backups and restore into a separate empty database
before relying on the system for competition work. Backup scheduling and a restore
drill are pending; no completed restore is claimed. Never commit exports or dumps.

## Live public-post snapshots

Use `uv run botfluencer collect-npc-posts --allow-network` for a one-shot archive
into npc_posts after migrations. See [post collection](post-collection.md) for
coverage, UTC timestamps, deduplication, and the team-confirmed NPC cutoff.
This does not run the worker or enable publishing.
