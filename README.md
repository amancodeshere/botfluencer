# Botfluencer

Team workspace for Capture the Narrative 2026. Current scope: repo scaffolding,
Postgres persistence, and operational reliability. Competition access is pending.
The platform and language model are deliberately mocked; no competition actions
or external model calls are implemented.

## Start locally

```sh
cp .env.example .env
docker compose up --build -d
curl --fail http://localhost:8000/health/worker
docker compose exec app botfluencer status
```

Start Docker Desktop first. Compose runs Postgres, applies migrations, and starts
the supervised mock worker. State persists in the Postgres volume across restarts.

## Development

Use Python 3.13 and [uv](https://docs.astral.sh/uv/). Read [AGENTS.md](AGENTS.md)
before making changes.

```sh
uv sync --locked
uv run botfluencer --help
uv run ruff check .
uv run ruff format --check .
uv run mypy src
docker compose --profile test up -d --wait postgres-test
TEST_DATABASE_URL=postgresql://botfluencer:botfluencer@localhost:55433/botfluencer_test uv run pytest
```

Keep credentials in environment variables or an ignored `.env`; commit only the
placeholder `.env.example`.

## Team references

- [Agent instructions](AGENTS.md): shared context, scope, and collaboration rules.
- [Architecture](docs/architecture.md): ledger, recovery, and deployment ownership.
- [Operations](docs/operations.md): native execution, pause/resume, tests, and backups.
- [Railway deployment](docs/railway.md): rehearsal infrastructure and hosted checks.
- [Project status](docs/project-status.md): completed work and remaining limitations.

## Available commands

`botfluencer db upgrade`, `run --ticks N`, `serve`, `status`, `pause`, and `resume`.
Run them via `uv run`, Compose exec, or an authenticated Railway shell. Public
HTTP routes expose health only; there is no unauthenticated control API.

Replay creates synthetic decisions/actions/outcomes; observe only stores fixture
observations. Live mode is blocked. No external LLM is called. Hourly budgets and
cooldowns are local operational caps, not official competition limits. Unknown
actions require reconciliation and are never blindly retried.

Railway files describe one app and one Postgres service. They have not been applied,
and the repo has not been pushed by this scaffolding task.
