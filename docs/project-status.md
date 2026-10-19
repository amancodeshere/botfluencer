# Project status

## Current baseline — 19 October 2026

Team readiness, side, and allowances were verified in the authenticated portal.
The team code is configured in ignored private local settings and an approved
encrypted GitHub Actions repository secret. SDK 0.5.0 is
pinned, and the verified 2026 sources are recorded in docs/competition.md.
Competition access is gated until 19 October, 17:00 Australia/Sydney, and remains
disabled in the normal worker configuration. An explicitly authorized read-only
social API collection completed on 19 October. No model requests, account creation, or
publishing were performed.

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
  cannot connect to the application database. Both schemas migrated to 0003,
  including the new npc_posts archive.
- Private local environment helpers and native startup instructions. Docker
  database workflows removed. The Dockerfile remains future app packaging only.
- CI separates static/unit checks from hosted Postgres integration checks and
  fails clearly without the required test secret. Infrastructure example renamed
  to prevent treating it as an imported graph for the existing database.
- Supervisor owns termination signals and drains work before exiting.
- Read-only cursor-paginated post archive with author names, original UTC times,
  preserved empty reposts, deduplicated reruns, and the team-confirmed
  pre-18:00 Sydney NPC cutoff.

## Verification

Ruff lint/format, strict mypy, infrastructure TypeScript checks, credential scans,
and private-file permissions pass. Three finite noop ticks ran against the Railway
application database. The 17 October baseline passed 45 tests: 21 unit tests and
24 integration tests against Railway Postgres 18. On 19 October, after updating
to SDK 0.5.0, all 27 unit tests and the new archive integration test passed,
including migration drift, deduplication, restart reuse, namespace isolation,
UTC normalization, and the strict NPC cutoff boundary. Integration groups use
independent synthetic namespaces.
The final payload compatibility/noop test also passed after the last storage change.
All three health endpoints returned 200; SIGTERM drained and exited with code 0.

Tests cover migration drift, deduplication, pause and budget durability, ambiguous
acceptance, retry scheduling, cooldowns, singleton ownership and ownership loss,
overlap, stale readiness, worker failure, deferred feeds, profiles, model contracts,
shared pacing, and shutdown behavior. Synthetic results are not competition scores.

## Shared GitHub configuration

Eleven non-sensitive repository variables are configured and verified, covering
Railway resource identifiers and mock/noop defaults. All six approved Actions
repository secrets are configured: application/test URLs, team code, assigned side,
and both account allowances. Values were sent privately; verification reads only
names and timestamps. See docs/team-access.md for the inventory and teammate
onboarding. Personal Railway tokens and database admin credentials were not shared.

## Remaining limits / next stage

No worker service deployed, code pushed, or live executor enabled. The required
TEST_DATABASE_URL repository secret is configured; a hosted GitHub Actions run
remains unverified until the local workflow commits are pushed. Hosted handover,
monitoring/alerts, a 24-hour soak, backup scheduling,
and a restore drill remain pending. Selective policies still need persisted skip
decisions; returning None currently defers the same observation. No winning
strategy is claimed.

## Initial live snapshot

The recent-feed cursor was exhausted across 21 pages, capturing 4,069 public posts
from 1,777 authors. All usernames were resolved; 10 posts have no display name
in their public profile. The snapshot includes 856 posts, 3,041 replies, and
172 bare reposts (NULL body), with source timestamps normalized to UTC. Names
finished resolving at 19:21 Sydney on 19 October 2026. No content is committed.

The team confirmed that no player posts existed before 18:00 Sydney that day.
On that basis, 3,196 rows from 1,450 authors are labelled NPC, with no missing
usernames or display names in that subset; 873 later rows are retained in the
archive. The latest captured source timestamp is 19:06:23 Sydney. New posts
arriving afterward require another run. Source history begins 18 October 23:16
Sydney; this is a public-history snapshot rather than only a since-opening window.

Future work: implement and evaluate team bot strategies behind the existing
registry, recheck organiser changes, verify read/model probes after opening, and
review real write/reconciliation guarantees before explicitly enabling publishing.
