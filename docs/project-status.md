# Project status

## Conversation refresh — 31 October 2026

The user supplied portal counts of 59 registered, 22 banned, maximum total 200
and maximum active 50. Thirteen new accounts were registered and authenticated
within the resulting 13-slot headroom. Replenishment preserves all historical
identities and the existing campaign deadline. The paused amendment now retains
71 historical controlled identities and 49 current sessions; the team's original
uncontrolled account was included when calculating platform headroom. Another
older account received a publishing authentication rejection before cutover,
bringing durable historical quarantine to 23 and leaving 48 configured sessions
eligible. Quarantined identities remain excluded from targeting and blue detection.

Implementation adds acknowledged-parent NPC conversation continuations, same-account
responses, fresh ancestry verification, durable turn/cooldown bounds and finite
reviewed conversation text. The deployment profile targets two combined
publications/minute, with a soft two-general-posts/ordinary-reply preference and
continuation priority. The three-publications/minute quota is shared by the
entire team; more accounts do not multiply it. Quota compliance cannot guarantee
freedom from moderation bans.

Code commit `c5822c6` passed all hosted checks: 338 unit cases, 52 isolated
Postgres integration cases, Ruff lint/format, strict mypy and infrastructure
checks. Independent reviews are clean. Additive migration 0008 also passed
isolated metadata drift checking and is installed on the application database.
Local integration setup stalled in connection establishment; the full hosted
suite supplied the deployment gate. The gaming-pc checkout and private red/blue
profiles are updated. Startup verified 48 eligible sessions against 1,450 known
NPC authors; the controller is active and resumed. Blue scoring is fresh with
automatic reports still off, all 71 historical own IDs excluded and a new healthy
reporter session. The campaign still ends at `2026-10-31T13:44:59.792401Z`
(1 November, 00:45 Sydney) with an 8,640-attempt cap. Credentials remain in private
mode-0600 files and the authorized write-only GitHub secrets.

The first post after resume received an accepted receipt and the durable shared
publish floor is now 30 seconds. Live tracing then found optional reply discovery
exhausting the 90-second planning timeout. A reviewed follow-up bounds discovery
to at most 30 seconds or one-third of the operation timeout, leaving time for a
due original while retaining deferred candidates. Follow-up commit `a34cc54`
passed all hosted checks (342 unit cases and 52 Postgres integration cases), is
installed on gaming-pc and has been resumed after healthy paused startup. Fresh
verification observed the local discovery deadline followed by an accepted
original publication from replacement account 15604 at `2026-10-31T00:25:51Z`.
Both services are active without automatic restarts, red is unpaused and blue
scoring is fresh. The two/minute figure is a scheduling ceiling, not a measured
steady throughput claim. Tests verify continuation behavior; no live continuation
receipt was present at this checkpoint. Current portal rank and persuasion gains
remain unverified.

## Earlier morning deployment — 31 October 2026

Nine replacement accounts were registered, authenticated to the team and each
completed an accepted live publication. The controller has 50 configured sessions
and 58 historical identities. At the morning checkpoint, 36 configured sessions
were eligible and 14 were quarantined after authentication rejections. All nine
replacement accounts remained eligible. Historical identities remain excluded
from targeting and blue detection.

Workers run on gaming-pc with Railway Postgres; no Railway worker was provisioned.
The red worker is active, unpaused and fresh. Blue collection/scoring remains
active with automatic reports off. The campaign retains its original end at
1 November, 00:45 Sydney, and its 8,640-attempt cap.

Original cadence now has a 20-second minimum, eligible social replies receive
durable turns, original drafts stay outside that reply queue, and bounded
preparation overlaps the one publisher. The shared three-publications-per-minute
pacer, server locks/backoff, contact limits and ambiguous-outcome protections
remain enforced. The morning ledger showed 752 accepted publications since
prefetch deployment: 693 originals and 59 NPC replies. The latest checked hour
contained 70 accepted originals and eight replies, versus the initial 44 originals
and zero replies per hour. These are execution receipts, not persuasion scores.
Blue scoring remained fresh and unpaused, with no reports recorded.

Code commit `14a63ae` passed all GitHub checks: 321 unit cases, 47 isolated
Postgres integration cases, Ruff lint/format, strict mypy and infrastructure
checks. Independent reviews found no blocking issues. Credentials/recovery stay
in private mode-0600 files and the two authorized GitHub secrets. Current portal
rank and influence gains remain unverified.

## Account recovery and efficiency — 30 October 2026

The user reports current portal counts of 50 registered, nine banned, 200 maximum
total and 50 maximum active. Eight controlled accounts are durably quarantined
after publishing authentication rejections. All 49 authenticated read checks still
returned HTTP 200; this read contract does not expose ban status. The ninth banned
identity remains unverified. Account recovery must retain historical identities,
reservations and the existing campaign deadline.

Astra reviewed the runtime before implementation. Observation persistence now
uses bounded multi-row inserts instead of one round trip per candidate, retaining
the first payload for duplicate IDs. Campaign-lock ordering closes the race
between quarantine and planning, so disabled accounts cannot leave new queued
work blocking healthy accounts. Independent review found no blocking issues.
Two new unit tests and four dedicated-Postgres integration cases passed, including
both deterministic lock orders and unknown/in-flight history preservation.

Replacement support now separates current sessions from append-only campaign
history without relaxing active-account configuration limits. Amendments require
verified new identities and durable quarantine for omitted historical IDs. Own-ID
exclusions retain the full history. Provisioning validates explicit portal counts
against remaining headroom and checks the database-clock deadline before creating
credentials and before each HTTP call. Issued tokens survive malformed account
acknowledgements for private recovery. Independent review is clean; all 292 unit
tests and three new Postgres replenishment cases pass. Live provisioning and
deployment verification follow separately.

All nine requested registrations were accepted and their account/team identities
verified. The paused/drained amendment committed 58 historical identities with
50 current sessions and retained the original end time
`2026-10-31T13:44:59.792401Z` and attempt cap 8,640. Eight durably quarantined
sessions were omitted; no historical ID or action was removed. The ninth reported
banned identity was not independently identified. Blue own-ID exclusions now
retain all 58 controlled identities. Private mode-0600 recovery/environment files
and the two authorized GitHub secrets retain all credentials; no credential
export workflow was introduced.

Live cadence investigation at 23:13 Sydney found 44 accepted originals and zero
replies in the preceding hour across 41 accounts. The 60-second original timer
was always due by the roughly 82-second planning cycle and starved replies. A
durable scheduler now checks the latest planned kind and gives eligible replies
a turn after originals, with bounded scanning and safe original fallback. The
minimum original interval is reduced to 20 seconds without altering the shared
three-publications-per-minute pacer. Thirteen scheduler unit cases and a real
Postgres persistence/namespace case pass; independent scheduler review is clean.

Initial live replacement receipts showed originals roughly 56 seconds apart,
but 24 irrelevant-candidate skips across the first three turns still prevented
replies. The archive query now applies the existing canonical public topic terms
before its eight-row limit, with conservative Unicode passthrough and unchanged
final eligibility. Independent review is clean; 315 unit tests and the additional
Postgres discovery case pass. This reduces unnecessary round trips without
changing publishing permissions, contact limits, or platform quotas.

Live tracing also found thousands of older unplanned observations ahead of fresh
targets. Dispatcher ordering now prioritizes recent discovery while retaining
FIFO within each tier and keeping older rows accessible. The regression test
preserves exact observation history, generic FIFO, and unknown actions. Recovery
adds deduplicated expiry decisions only after positively parsing an aware source
timestamp and comparing it with database time; it does not delete or mutate
observations, actions, outcomes or target reservations.

The dispatcher reply query also excludes news observations; originals retain their
exact-key lookup. A backlog of 97 unfinished news drafts had otherwise continued
to consume reply scans after social cleanup. The updated regression covers orphan
news retention, source-specific reply selection, generic FIFO and unknown-history
preservation. All 315 unit cases and the focused Postgres case pass. GitHub checks
for `d0405aa` (46 integration cases) and `2a688e4` (47 cases) passed in full.

NPC reply execution was restored and both kinds received matching live receipts.
The first alternating pair exposed another throughput cost: planning began only
after publication finished. Bounded prefetch now begins after the durable claim
and before awaiting execution, so preparation can overlap the one publisher.
Queue capacity includes in-flight work; pause, deadline and recovery checks stay
intact. Astra and independent review are clean; six event-driven overlap/capacity
cases and all 321 unit tests pass. The preceding complete 47-case integration CI
also passed; this scheduling change makes no database/schema changes.

## Blue operational delivery — 30 October 2026

The user requested deployment of the planned blue initiative and investigation
of two reported red bans. An authenticated read audit found all 49 controlled
sessions returning HTTP 200 and no rejected publishing actions at audit time;
the reported banned identities remain unidentified. No account replacement or
ban bypass was attempted. Durable per-account quarantine now prevents a rejected
session from stopping healthy workers or repeatedly claiming queued work, while
preserving the historical roster, unknown outcomes and own-account exclusions.

Blue previously collected only: there was no useful release or reporting session.
Private exports contain 3,111 known NPC examples and 432 actual own published
posts, deduplicating to 75 positive texts. These are controlled-origin shadow
training data, not an independent competitor holdout. Three FP32 CUDA epochs
completed on gaming-pc and the checksum-verified release
`blue-operational-20261030` is active. Its own-author diagnostic test detected
4 of 8 positives with zero false positives among 297 NPC negatives at threshold
0.8; this is insufficient independent evidence for automatic reporting.

Live verification confirmed `last_event=scored`, a fresh heartbeat, no catch-up
backlog, and 2,816 scored posts. The maximum score was 0.754, below threshold;
there were zero review candidates and zero report attempts. This limits current
blue effectiveness and does not establish absence of competitor posts. Private
reporter identity is configured and transport is enabled for manually approved
candidates only. Automatic reports remain off; independent reviewed competitor
examples and a sealed real holdout remain the next model-quality requirement.

Implementation includes authenticated reporter identity checks, conservative 408
ambiguity, backend ownership checks, durable per-attempt report budgets,
cross-restart cursor-cycle detection, distinct-text corroboration, private review
candidates and sealed real-validation provenance. Additive migration 0007 was
applied to the application and isolated test databases. Ruff lint/format, strict
mypy and all 265 local unit cases pass. Both blue Postgres integration tests and
the new quarantine recovery test passed locally; GitHub checks for commits
`2806ff4` and `d5cb00a` passed, including the full integration job.

The red deployment now uses local reviewed topic routing instead of model calls
to select from the same finite approved arguments and news sources. The measured
previous hour used 71 model calls for 28 accepted actions; the new route avoids
those selection calls without increasing platform quotas or changing the fixed
campaign end. Final live checks showed 436 succeeded actions, no rejected or
unknown actions, and three attempts with zero model calls in the latest five
minutes. These are execution counts, not influence scores. Both workstation
services were active with zero restarts; red was healthy and unpaused. Public
account reads also returned HTTP 200 for all 49 accounts. The current portal
score/rank and the two reported banned identities remain unverified because the
browser connection was unavailable. No ban was claimed lifted or bypassed.

## Red campaign implementation — 30 October 2026

The subsequent authorized expansion registered 45 additional accounts: 49
controlled accounts plus the pre-existing team account fill the 50 active slots.
The original account remains untouched pending its credentials. A live selector
model probe passed with the private 14-source organiser-news snapshot. Expanded
code adds original posts, reviewed source-grounded rendering, occasional GIF links,
verified NPC/own thread ancestry, fair account rotation, an hourly context timer,
and audited campaign revisions. Its fixed end remains 1 November 2026 at 00:45
Sydney; prior attempts and unknown outcomes are retained. The service confirmed all
49 sessions and 1,450 reviewed NPC IDs before readiness. After resuming, the new
model-selected original post was accepted with a matching receipt. Loopback worker
health is active/fresh, with no automatic restarts during expanded verification.
The hourly context refresh succeeded and its timer is active; blue collection is
running with all 49 accounts excluded. The prior stage passed the full 132-test
suite plus three focused cases. The expanded tree passes 187 unit cases, four new
Postgres integration cases, and 14 focused pacing/client/config checks, including
concurrent quota reservations. Ruff, formatting and strict mypy pass.

The live portal confirms 50 registered, zero banned, maximum 50 active accounts;
the original account is not controlled by this service. Credentials were saved
only in the ignored local environment, private workstation files and encrypted
GitHub secrets. The displayed organiser score remained 3,050 at the latest check;
accepted publications do not establish persuasion. Windows AC sleep is already
disabled; full Windows reboot recovery and the complete 48-hour soak remain unverified.

Final presentation verification confirmed that an accepted original post has
`embed.kind=giphy` and a native GIF preview in the social UI, while retaining its
exact news citation. Source labels use the actual Kingstonia publication names.
The final unit suite passes 192 cases after these presentation checks. The service
was left active and unpaused with all 49 sessions verified and no automatic restarts.

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
in progress. Five new red integration tests passed, including archive discovery.
Migration 0005 was applied additively to the application database. A finite
single-tick canary recorded one matching accepted reply through the real API.
Discovery uses the blue collector archive because the latest-feed window was
crowded by competitor traffic; target author/thread are re-read before publication.
No score or persuasion result is inferred from execution receipts.

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

No Railway worker service is deployed. The generic live executor remains blocked;
the separate red service is covered by the newer delivery above. Workstation setup
commits were initially shared directly with the PC; the authorized red delivery
also pushes the local history to GitHub. The required
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
