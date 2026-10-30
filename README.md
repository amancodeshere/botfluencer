# Botfluencer

Capture the Narrative 2026 team workspace. Python 3.13, uv, and Railway-hosted
Postgres provide a durable baseline for future bot strategies. The official SDK
is pinned to 0.5.0; async adapters follow its documented wire contracts.

The default is `mock` + `replay` + `noop`: fixture observations are persisted,
without generated drafts or actions. The separate, explicitly enabled
`botfluencer red serve` workload can publish bounded replies to confirmed NPCs
inside the organiser simulation; see [red-team operations](docs/red-team.md).

## Get running

Read [AGENTS.md](AGENTS.md) before changing the project. Install Python 3.13,
[uv](https://docs.astral.sh/uv/), and the Railway CLI, then authenticate and link
this checkout to the team's existing `botfluencer` project and production environment.
Do not create a second database service.

```sh
uv sync --locked
# New teammates only: copy the template without overwriting existing secrets.
cp -n .env.example .env
uv run python scripts/railway_env.py --service Postgres
uv run python scripts/railway_env.py --service Postgres --test
uv run botfluencer db upgrade
uv run botfluencer doctor
uv run botfluencer run --ticks 3
uv run botfluencer serve
```

The helpers capture Railway variables privately and create `.env` / `.env.test`
with mode 0600. Request project access from a teammate; never share URLs in Git
or chat. Add your team code and assigned side privately if competition access is
needed later. The public TCP connection requires TLS and database authentication.
There is no Docker database or local fallback.

```sh
curl --fail http://localhost:8000/health/worker
uv run botfluencer status
uv run botfluencer pause
uv run botfluencer resume
```

## Verify

```sh
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run --env-file .env.test pytest
npm --prefix .railway ci
npm --prefix .railway run check
```

Tests require the dedicated `botfluencer_test` database. They must never use the
application database. CI requires a `TEST_DATABASE_URL` repository secret and
serializes integration runs. Read [operations](docs/operations.md) for details.

## Team references

- [Blue-team workflow](docs/blue-team.md): private training notebooks, scheduled feed/scoring,
  author evidence, and opt-in moderation reports. A trained workstation model now
  scores live posts; reporting requires manual approval pending independent evaluation.
- [Red-team workflow](docs/red-team.md): a bounded NPC-only campaign hosted on gaming-pc,
  private account sessions, team-wide deduplication, and durable stop controls.
- [GPU workstation](docs/gaming-pc.md): gaming-pc SSH, Mamba, CUDA, and local training checks.
- [Post collection](docs/post-collection.md): archive live public posts without publishing.
- [Team access](docs/team-access.md): GitHub secrets, shared settings, and teammate onboarding.
- [Architecture](docs/architecture.md): ledger, recovery, ownership, and boundaries.
- [Competition standards](docs/competition.md): verified 2026 contracts and limits.
- [Bot extension guide](docs/bots.md): strategies, profiles, and versioning.
- [Railway runbook](docs/railway.md): existing database and future worker deployment.
- [Project status](docs/project-status.md): checks, limitations, and next steps.

A Railway database service is provisioned; workers run on gaming-pc to conserve
the hosting allowance. The red campaign has separate private credentials and
controls. Synthetic outcomes describe execution, not score.
