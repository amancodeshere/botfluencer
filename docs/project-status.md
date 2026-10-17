# Project status

## Context

Team readiness verified in the authenticated portal on 17 October 2026. Team
code, assigned side, and account allowances are configured in the ignored local
`.env`; no credentials are committed. Network access remains opt-in and disabled.
The official SDK 0.4.0 and 2026 getting-started guide are now available. Current
scope: standards-aligned infrastructure and extension points, not a live campaign.

## Completed stages

1. Shared agent instructions, package layout, typed settings, and quality tooling.
2. Postgres schema, Alembic migration, transactional ledger, durable budgets,
   pause controls, synthetic receipts, and namespace singleton ownership.
3. Mock replay/observe runtime, versioned intents, bounded execution, safe retry
   backoff, timeout reconciliation, supervised HTTP health, and deployment handover.

4. Non-root container, local Compose databases, Railway IaC scaffold, CI checks,
   and deployment/operating runbooks.

## Verification and limits

All 24 tests pass against Postgres 16 and 18. Ruff lint/format checks, strict mypy,
Railway TypeScript validation, and Compose configuration validation pass.
The non-root deployment image builds and runs migrations. Local Compose startup,
worker health, persistent pause across container restart, resume, and unchanged
action counts after restart have been verified. The local mock stack is running.

Unit and real Postgres integration tests cover migration drift, deduplication,
pause/budget durability, restart reconciliation, retry deadlines and caps,
ownership loss, deployment overlap, and supervisor failure propagation.

No Railway resources provisioned, code pushed, hosted monitor configured, or
24-hour soak/backup restore completed. API and real LLM integration are pending.
Mock receipts emulate an idempotent API; they do not prove the live API offers it.
The scheduler is a conservative baseline, not a competition strategy or simulator.

## Next work package

Review official 2026 API/templates once received; adapt the real platform and
supplied model, then run a conservative baseline. Do not infer account counts
or limits from older competitions.
