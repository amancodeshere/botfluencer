# Architecture

## Agreed choices

Python 3.13 with uv; Railway hosting; Postgres durability. One asynchronous
application owns the worker and exposes health endpoints. Accounts are
configuration entries rather than separate deployments.

The runtime observes, persists observations, creates versioned decisions and
action intents, executes bounded actions, and records outcomes. Platform and
LLM adapters are replaceable. The default adapters use synthetic fixtures. Read-only CTN social/news and structured
model adapters are separately gated; real platform execution is blocked. Strategies
are registered explicitly, with validated account profiles and noop as the default.

## Boundaries

`config.py` validates environment configuration. `platform/` defines the platform
contract and mock behavior. `llm/` defines bounded structured generation.
`strategies/` selects actions. `storage/` owns database transactions and migrations.
`runtime/` owns scheduling, control, and recovery. `reporting/` exposes health and
operator summaries. `cli.py` provides operational commands.

Verified 2026 contracts are in docs/competition.md. Shared request pacing is stored
by hashed team scope and bucket, independently of worker namespaces; generation
also holds a team-wide advisory lock. This coordinates clients using this database.
Replay tests prove
software behavior; they do not estimate citizen persuasion or competition score.

## Durability and recovery

Observations deduplicate by namespace/external ID. Decision and action keys hash
namespace, account, observation ID, strategy version, and prompt version. Decision
and action intent creation is one transaction. Hourly budgets count reserved
generation calls and action attempts before the respective call is made. Failed
calls consume budget too. Budget windows use the database clock.

Actions transition from queued to in_flight before execution. Accepted actions
become succeeded; definitive rejection becomes failed; safe rejection schedules
persisted backoff. Unexpected errors and interrupted calls become unknown. On
restart all in_flight actions become unknown. Reconciliation either confirms
acceptance, proves non-acceptance before requeueing, or leaves the action unknown.

The mock's remote receipt table emulates durable idempotent acceptance. It does
not establish any guarantee about the actual competition API. Future adapters
must document their idempotency and reconciliation support. Unknown actions must
never be treated as safely rejected merely because a lookup returned no data.

## Worker ownership and deployment

A dedicated direct Postgres connection holds a session advisory lock per
namespace. Do not put it behind transaction-mode connection pooling. Losing the
connection ends the worker. One service supervises both worker and HTTP server;
fatal worker errors end the process with failure. SIGTERM drains work for a bounded
period, then cancels it; interrupted execution is reconciled on restart.

A replacement deployment starts in standby if an old worker owns the lock.
Standby can pass `/health/ready` after database initialization so Railway can
switch deployments and terminate the old process. It cannot execute actions.
After ownership transfers, the new worker recovers the ledger and begins ticking.
`/health/worker` requires an active, fresh heartbeat and database connectivity;
use it for ongoing monitoring. Pause leaves heartbeat running and stops new work;
an already reserved/in-flight call may finish.

## Public post archive

`npc_posts` is an independent read-only archive, with the requested author names,
body, and original UTC creation time. Namespace plus source post ID deduplicates
reruns. It does not create decisions or actions. NPC labels support an explicit team-confirmed
timestamp cutoff; the initial capture uses 18:00 Sydney on 19 October 2026. See post-collection.md for capture
commands, source coverage, restart behavior, and limits.

## Blue-team detection and moderation

`blue/` owns private dataset preparation, optional workstation training/inference,
incremental public-feed collection, and a narrow moderation report adapter.
ML libraries load lazily and remain outside Railway's default dependencies.
`blue_state` checkpoints catch-up and pause/heartbeat state; `blue_scores` retains
model-versioned evidence. `blue_reports` deduplicates team/post intents across
namespaces and models, separate from the mock publishing action ledger.
`blue_team_controls` persists reporting blocks. Reports recover interrupted
attempts to unknown rather than reissuing them. A separate team lock spans claim
and submission. Ordinary publishing remains unavailable.

The resident workstation service defaults to collection/shadow scoring. Reports
require private account authentication and opt-in; automatic approval additionally
requires independent real-label evaluation. Moderator outcome ingestion is
manual until its authenticated payload contract can be verified. See blue-team.md.

## Bounded red campaign

`red/` adds a separate `red serve` entrypoint; ordinary CTNPlatform execution and
the generic live-mode gate remain blocked. This workload uses the same durable
action ledger and namespace ownership. Additive migration 0005 stores an immutable
team campaign (namespace, UTC window of at most 48 hours, accounts, source archive,
and total attempt limit), plus team/target reservations independent of prompt and
strategy versions. One reserved contact per author per 24 hours limits repeat replies.

The reviewed NPC source is the existing team-confirmed pre-opening archive; later
unverified posts never establish identity, conflicting player labels are excluded,
and own accounts cannot be targets. A fresh target read must confirm a top-level
NPC post before a reply. No mentions or human-root threads are permitted. Curated
civic arguments use public topic matching and do not consume model quota or infer
personal traits. Skip decisions persist so irrelevant observations advance.

Account sessions must match the configured team through authenticated `/auth/me`.
The SDK reply contract uses `POST /posts`; there is no idempotency guarantee.
Timeouts, 5xx, interrupted calls and malformed acknowledgements remain unknown;
only explicit pre-call deferrals and 429 responses allow safe retry. The fixed
deadline is checked before claims and again before the HTTP write. Postgres remains
on Railway, while a loopback-only systemd user service runs on gaming-pc.
