# Project status

## Current baseline — 17 October 2026

Team readiness, side, and allowances were verified in the authenticated portal.
The team code is configured only in ignored private local settings. SDK 0.4.0 is
pinned, and the verified 2026 sources are recorded in docs/competition.md.
Competition access is gated until 19 October, 17:00 Australia/Sydney, and remains
disabled locally. No social/news/model API requests, account creation, or publishing ran.

## Completed

- Typed Python 3.13/uv package, shared AGENTS.md, versioned ledger and migrations.
- Durable observation, decision, action, outcome, controls, budgets, cooldowns,
  ambiguous-result recovery, singleton ownership, supervised health, and handover.
- Async CTN read/model adapter boundaries, validated generation, opening/opt-in
  guards, team-wide persisted request pacing, and serialized model access.
- Explicit strategy registry and validated per-account profiles; noop is the
  default and cannot claim older queued actions. Synthetic replay stays opt-in.
- One approved Railway Postgres 18 service with an approved public TLS endpoint.
  Application/test databases have separate non-superuser roles; the test role
  cannot connect to the application database. Both schemas migrated to 0002.
- Private local environment helpers and native startup instructions. Docker
  database workflows removed. The Dockerfile remains future app packaging only.
- CI separates static/unit checks from hosted Postgres integration checks and
  fails clearly without the required test secret. Infrastructure example renamed
  to prevent treating it as an imported graph for the existing database.
- Supervisor owns termination signals and drains work before exiting.

## Verification

Ruff lint/format, strict mypy, infrastructure TypeScript checks, credential scans,
and private-file permissions pass. Three finite noop ticks ran against the Railway
application database. All 45 tests pass: 21 unit tests and 24 integration tests
against Railway Postgres 18. Integration groups use independent synthetic namespaces.
The final payload compatibility/noop test also passed after the last storage change.
All three health endpoints returned 200; SIGTERM drained and exited with code 0.

Tests cover migration drift, deduplication, pause and budget durability, ambiguous
acceptance, retry scheduling, cooldowns, singleton ownership and ownership loss,
overlap, stale readiness, worker failure, deferred feeds, profiles, model contracts,
shared pacing, and shutdown behavior. Synthetic results are not competition scores.

## Remaining limits / next stage

No worker service deployed, code pushed, or live executor enabled. GitHub CI still
needs the dedicated TEST_DATABASE_URL repository secret before integration runs
can pass. Hosted handover, monitoring/alerts, a 24-hour soak, backup scheduling,
and a restore drill remain pending. Selective policies still need persisted skip
decisions; returning None currently defers the same observation. No winning
strategy is claimed.

Future work: implement and evaluate team bot strategies behind the existing
registry, recheck organiser changes, verify read/model probes after opening, and
review real write/reconciliation guarantees before explicitly enabling publishing.
