# Operations

## Local startup

Start Docker Desktop first. The local Postgres credentials are placeholders for
loopback-only development; do not reuse them for hosted databases.

```sh
cp .env.example .env
uv sync --locked
docker compose up --build -d
docker compose logs -f app
```

Compose starts Postgres, waits for database readiness, applies migrations once,
then starts the app. The app's database URL points to the Compose network; `.env`
uses localhost for native Python commands. Compose forwards the documented
runtime limits but deliberately fixes the container's fixture path and port.

```sh
curl --fail http://localhost:8000/health/worker
docker compose exec app botfluencer status
docker compose exec app botfluencer pause
docker compose exec app botfluencer resume
```

Pause stops new observation/generation/action work. Already reserved/in-flight
work may finish. Paused workers keep heartbeats and stay healthy. Pause is stored
in Postgres and survives restart. Use a stable namespace to preserve controls.
Status is available only via the authenticated CLI; public HTTP endpoints expose
minimal health information and offer no control actions.

## Native worker

Run only one worker per namespace. Stop the Compose app before using a native
worker against its namespace; otherwise it will safely wait in standby.

```sh
docker compose stop app
uv run botfluencer db upgrade
uv run botfluencer run --ticks 5
uv run botfluencer status
uv run botfluencer serve
```

`run --ticks N` ends after N completed ticks. `serve` supervises worker and health
server, handles SIGTERM, and returns a failure status on fatal worker errors.
`observe` mode only ingests synthetic observations; use a separate namespace.

## Verification

The test Postgres is independent of the persistent development database:

```sh
docker compose --profile test up -d --wait postgres-test
export TEST_DATABASE_URL=postgresql://botfluencer:botfluencer@localhost:55433/botfluencer_test
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
```

Tests refuse database names without the `_test` suffix. With no URL, local
integration tests skip; CI requires the URL and runs the full suite against
Postgres 16 and 18. Local Compose uses 16; Railway's current SDK helper selects
18. Test data uses unique namespaces and synthetic content.

## Action recovery

| State | Meaning | Next step |
| --- | --- | --- |
| queued | Intent exists, no current call | Claim when budget/cooldown permits |
| in_flight | Attempt reserved, acceptance uncertain | Recover to unknown after restart |
| unknown | Remote acceptance unconfirmed | Read-only reconciliation |
| succeeded | Receipt confirms acceptance | Never execute again |
| failed | Definitive rejection or attempts exhausted | Review; no automatic retry |

Only an adapter's explicit safe rejection or proof of non-acceptance allows a
retry. Generic timeouts/errors do not. Unresolved actions remain unknown. The
mock receipt table simulates remote idempotency; real API guarantees are pending.
Do not manually reset unknown or succeeded actions to queued in production.

## Budgets and health

Hourly caps count **attempts**, including failures and reserved generation calls.
They use a rolling one-hour window and the database clock. Per-account cooldowns
and action retry dates persist. Zero budgets disable the corresponding operation.
These are conservative operational defaults, not official competition quotas.

- `/health/live`: the HTTP process is responding.
- `/health/ready`: storage initialized and supervisor active/standby; deployment check.
- `/health/worker`: owned workload, fresh completed tick, and database reachable.

An exhausted budget or pause is a valid idle state with fresh heartbeat. A hung
tick hits the operation deadline and exits; stale heartbeat returns HTTP 503.
Compose health flags alone do not automatically restart an unhealthy container;
the app supervisor exits on fatal worker errors, and Compose restarts that exit.

Logs go to stdout as small JSON events with UTC timestamps and correlation IDs.
They omit content, prompts, credentials, URLs, and raw exception text. CLI failures
report only field names/error types. Detailed action context belongs in the DB,
which should remain private.

## Deployment, rollback, and backups

See [Railway setup](railway.md). Keep deployable changes committed and verified.
Use additive migrations so the old and new app can overlap. Roll back the app to
a compatible prior commit; do not automatically downgrade the database schema.
If a migration fails, the pre-deploy step blocks the replacement app.

Back up Postgres before schema changes. For a local development backup:

```sh
mkdir -p backups
docker compose exec -T postgres pg_dump -U botfluencer -d botfluencer -Fc > backups/local.dump
```

Backups are ignored by Git. For Railway configure scheduled database backups and
test restore into a **separate empty database**. Never practice restoring over the
team's current database. Record the restore drill and retention configuration in
the team's operating notes when completed.

```sh
docker compose down
```

This stops services and preserves the development volume. Do not add `--volumes`
unless explicitly choosing to erase the local development database.
