# Shared project context

## Purpose and current scope

Botfluencer is this team's Capture the Narrative 2026 competition workspace.
The team is enrolled and waiting for official API documentation, starter code,
credentials, account allocation, rate limits, scoring details, and model quotas.
Hosting choice: Railway. Durable storage: Postgres for observations, decisions,
actions, outcomes, and runtime controls. Current work package: scaffolding and
operational reliability, with a synthetic platform and model.

Do not invent competition endpoints, limits, scores, credentials, or winning
strategies. Do not implement or enable real competition actions until requested
after access arrives. Synthetic outcomes are diagnostic data, not influence scores.
Keep all competition activity within the organiser-provided simulation.

## Starting a task

1. Read this file, README.md, docs/architecture.md, and relevant operating docs.
2. Inspect `git status` and the relevant implementation before editing.
3. Preserve teammates' uncommitted changes. Do not reset, clean, force-push, or
   rewrite shared history. Do not add IDE settings or unrelated files to commits.
4. State the scope and keep changes reviewable. Ask only about material missing
   requirements; resolve routine implementation choices using project context.
5. Use agents or delegation only when the human explicitly requests it. If used,
   assign separate files or coordinate edits; all agents share the checkout.

## Architecture rules

- Use Python 3.13, uv, type annotations, and small explicit modules.
- Keep platform and LLM adapters separate from strategy and runtime code.
- Decisions and generated drafts must be structured and validated; untrusted
  posts must never become system instructions or tool permissions.
- Postgres is the source of truth. No SQLite fallback or in-memory replacement
  for durable action state. Use real Postgres in persistence/recovery tests.
- Use Alembic migrations for schema changes; never `create_all` at startup.
  Keep migrations explicit and reviewable, and prefer additive changes so the
  previous application version remains compatible during deployment.
- Persist intent before execution. Deduplicate actions and reconcile ambiguous
  execution results. Never promise exactly-once external side effects without
  documented platform support. Unknown results must not be blindly retried.
- Preserve pause state, budgets, cooldowns, and action history across restarts.
- Enforce one active worker per namespace. Multiple app processes must not
  double-execute the same workload, including deployment overlap.
- Bound network/database waits and retries. Fatal worker failures must exit the
  process with a failure status; a healthy web server alone is insufficient.
- Record strategy/prompt versions, correlation IDs, and UTC timestamps.
  Keep runtime data separated by namespace, especially replay versus future live.

## Secrets and data

Never commit `.env`, credentials, tokens, production exports, database dumps,
or competition data. Never print connection strings or raw exception details
that might contain secrets. Use sanitized synthetic fixtures in tests.
Runtime logs should identify events and IDs without dumping content or prompts.
Use Railway secrets/reference variables for deployment credentials.

## Verification and commits

Run checks appropriate to the changed behavior:

```sh
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
```

Integration tests require a dedicated disposable Postgres database configured by
`TEST_DATABASE_URL`. Never point tests at development or production data. CI must
run these tests rather than silently skip them. Test meaningful failure behavior:
deduplication, restart recovery, ambiguous outcomes, pause, budgets, singleton
ownership, migration compatibility, and failed/stale readiness.

Commit each completed, verified stage when the user requests staged commits.
Use descriptive messages and stage explicit paths. Do not push, open a PR, deploy,
provision paid resources, or send teammate messages unless the task authorizes it.
Report what changed, what passed, and any unverified limitations precisely.

## Keeping teammates in sync

Update README.md and docs when commands, configuration, runtime guarantees,
or architecture change. Keep docs/project-status.md current with completed work,
known limits, and next steps. Keep new assumptions clearly marked. Leave a short
handoff in the final response with commit IDs and relevant files.
