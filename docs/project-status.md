# Project status

## Red campaign implementation — 30 October 2026

The user authorized a bounded NPC-only red campaign, staged commits, push, and
deployment, and selected gaming-pc for workers to conserve Railway allowance.
Postgres remains on the existing Railway service; no Railway worker was created.
Four new neutral simulation accounts were registered and their team identities
verified through the official account contract. Credentials/recovery records stay
private outside the checkout. The authenticated portal verified Vote Yes and
allowances of 200 total / 50 active accounts; one account existed before this stage.

The narrow executor, durable 48-hour mandate, independent target reservations,
one-contact-per-author/24h limit, immutable configuration, private session checks,
curated policy, and loopback systemd template are implemented. Static checks pass;
the complete dedicated-Postgres suite and gaming-pc deployment verification are
in progress. Operational results will be recorded after verification. No score or
persuasion result is inferred from test data or account registration.

## Current baseline — 23 October 2026

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
- Windows/WSL2 GPU workstation reached over Tailscale SSH, with a Desktop clone,
  named `botfluencer` Mamba environment, Python 3.13, CUDA PyTorch, and optional
  training/notebook dependencies in the shared lockfile. Environment, model
  caches, datasets, and checkpoints use D: outside the OneDrive Desktop.
  Approved application/test credentials are private Ubuntu files (0600), with
  mock/noop defaults and competition network access disabled. See
  [GPU workstation](gaming-pc.md) for commands and locations.

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

On 22 October, all 52 tests passed on gaming-pc against the dedicated Railway
test database, including migration drift and recovery tests. Ruff lint/format
and strict mypy also passed locally. The workstation doctor confirms SDK 0.5.0,
configured private credentials, mock/replay/noop, competition networking disabled,
and publishing disabled. The installed training environment passed dependency
compatibility checks.

Tiny BERT completed three CUDA/BF16 optimizer steps and a matching checkpoint
reload on the RTX 3070. Pretrained DeBERTa-v3-small also completed three FP32
optimizer steps with finite losses and a matching checkpoint reload, using a
peak 2,731 MiB of allocated GPU memory. The diagnostic explicitly requests FP32
model weights to prevent the non-finite optimizer results seen with the inferred
checkpoint dtype. These synthetic installation checks do not establish detector
quality or memory requirements for longer sequences and larger batches.

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

No worker service is deployed and no live executor is enabled. Workstation setup
commits are shared directly with the PC; this task does not push to GitHub. The required
TEST_DATABASE_URL repository secret is configured; a hosted GitHub Actions run
remains unverified by this task. Hosted handover,
monitoring/alerts, a 24-hour soak, backup scheduling,
and a restore drill remain pending. Selective policies still need persisted skip
decisions; returning None currently defers the same observation. No winning
strategy is claimed.

The workstation installation does not schedule inference, train a useful
classifier, or enable reporting. The Windows startup installer was applied
through Administrator PowerShell on the PC. Its task is running,
forwarding readiness is logged, and SSH connectivity has been restored. A planned
Windows reboot test is still required before unattended recovery is proven. Windows sleep and
Tailscale key expiry also need operational attention.

## Blue-team delivery — 23 October 2026

Three clean notebook templates and private gaming-pc copies cover NPC export,
balanced Yes/No Qwen synthesis, DeBERTa training, a TF-IDF baseline, calibration,
review and shadow activation. Private exports contain 3,111 deduplicated NPC text
examples from 1,427 authors; raw archive counts below include duplicates/reposts.
No competition model calls or useful detector training were run by this delivery;
the user runs those notebook stages. Generated data, checkpoints, and executed
notebooks remain outside Git/OneDrive.

An additive migration to 0004 was verified against isolated test Postgres and
applied to the application database. The resident `botfluencer-blue` user service
is installed/enabled on gaming-pc, with user lingering enabled. Its read-only
feed collector is running on a 30-second target interval, with persisted catch-up
cursor and singleton ownership. Heartbeat/status confirmed `waiting_for_model`.
Reporting and automatic approval remain disabled; there is no active release or
account session credential. A stale PC application credential was refreshed from
the working private local configuration over the approved SSH path.

37 unit checks and two new Postgres integration tests passed, including migration
metadata drift, pagination recovery, author exclusions, score/report deduplication,
pause, uncertain execution recovery, pending budget and durable moderator blocks.
The existing integration groups were not rerun for this delivery. Ruff lint/format
and strict mypy pass. A tiny synthetic CUDA training run exercised the notebook
helper through training, evaluation, model export, manifest verification, local
inference and shadow symlink activation in an isolated diagnostic directory.
That diagnostic release is not the live worker's active model.

Next: run the private notebooks, inspect held-out metrics, activate a trained
shadow release, and review real candidates. Automatic reporting additionally
requires real author-disjoint validation and explicit private account settings.
Moderator outcomes currently need manual entry: the authenticated outcome payload
contract has not been verified. Default one-pending reporting budget therefore
limits unattended reporting throughput. Full reboot recovery and external
monitoring/alerts remain unverified. See [blue-team workflow](blue-team.md).

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
