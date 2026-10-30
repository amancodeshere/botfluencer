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

Observations deduplicate by namespace/external ID. Inserts use batches of at most
500 unique IDs and retain the first payload, including repeated input IDs.
Decision and action keys hash
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
and submission; `blue_report_attempts` counts every claimed submission, including
safe deferrals. Cursor history persists across catch-up cycles to detect loops.
Ordinary publishing remains unavailable.

The resident workstation service defaults to collection/shadow scoring. Reports
require private account authentication and opt-in; automatic approval additionally
requires independent real-label evaluation. Moderator outcome ingestion is
manual until its authenticated payload contract can be verified. See blue-team.md.
Model manifests checksum-bind training provenance. Own published controls and
synthetic examples support training and diagnostics but cannot validate automatic
reporting. Report identity is verified against the configured account and team.

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
civic arguments use public topic matching without inferring personal traits.
Expanded mode selects finite reviewed angles, styles and source IDs. Selection
can use the organiser model or deterministic local topic routing; no free model
prose crosses the publishing boundary.
Private news snapshots refresh hourly and expire after six hours. A bounded
background planner keeps a small queue while the sole owner dispatches actions.
Skip decisions persist independently of prompt versions so irrelevant or already
contacted observations advance. Explicit paused/drained expansions append an
audit revision before adding accounts or increasing caps; deadlines never reset.

The campaign account array is historical and append-only. The configured session
roster stays within the active allowance; historical IDs may be omitted only
after durable quarantine. Historical own IDs remain excluded from NPC targets and
blue detection. Audited amendments authenticate every new ID before extending
history. Provisioning requires current total/active counts, checks cumulative
headroom and the existing campaign deadline, and retains partial credentials
without repeating ambiguous registrations.

Account sessions must match the configured team through authenticated `/auth/me`.

Additive migration 0008 attaches nullable continuation parent/root coordinates
to target reservations. A continuation requires an accepted, succeeded own
parent action, the same active account and fresh NPC/own ancestry verification.
Per-author, per-root and pending-root bounds are checked under the campaign lock;
the executor reloads durable authority by child action ID before publishing.
Ordinary contact limits remain unchanged. Shared publishing can use a configured
interval floor, retained across responses and restarts.
Durable `red_account_controls` quarantines unavailable sessions while healthy
accounts continue. Queued work for disabled accounts fails; unknown and in-flight
work, campaign membership and historical own-account exclusions remain intact.
Planning and quarantine serialize on the campaign row before checking account
eligibility or locking actions, so quarantine cannot leave newly queued work behind.
The SDK reply contract uses `POST /posts`; there is no idempotency guarantee.
Timeouts, 5xx, interrupted calls and malformed acknowledgements remain unknown;
only explicit pre-call deferrals and 429 responses allow safe retry. The fixed
deadline is checked before claims and again before the HTTP write. Postgres remains
on Railway, while a loopback-only systemd user service runs on gaming-pc.
