# Red-team simulation worker

The red worker runs on **gaming-pc**, reached with `ssh gaming-pc`. Railway hosts
Postgres; it does not host this worker. The existing `botfluencer-workstation`
launcher activates the named Python 3.13 Mamba environment and runs
`uv run --locked --inexact --extra training` from the checkout. This worker uses
lightweight CPU code and HTTP requests; it does not load a model or use CUDA.
See [gaming-pc.md](gaming-pc.md) for the workstation paths and SSH setup.

Only the organiser's fictional CTN simulation is supported. One coordinator
assigns work across configured simulation accounts. The default policy uses
curated civic replies. The expanded policy uses a model to select reviewed
arguments and sources; it does not publish arbitrary model prose, generate
medical claims, profile people, infer demographics, or inject instructions into
NPC prompts. Accepted API receipts do not establish persuasion,
engagement, influence score, or strategy effectiveness.

## Current 50-session campaign

The current deployment configures 50 verified sessions, including nine newly
registered replacements. Eight unavailable controlled accounts are retired from
the session roster; all 58 controlled identities remain in campaign history and
own-account exclusions. The team's original account remains outside this
controller pending its credentials. The same team-wide three
publication writes per minute covers all posts, replies/comments and reposts.
More accounts do not raise that quota. Fair scheduling includes queued reservations
and recent attempts so unused community desks receive turns.

With `RED_USE_MODEL=true`, the organiser model selects only a source ID, one of
five reviewed civic angles and one of three styles. Published sentences come
from the server renderer, bounded topic labels and exact public source URLs.
Arbitrary model prose and source quotations never become published text. Fifty
editorial lenses provide functional variety without inventing human identities,
professional credentials, lived experiences or demographic profiles.

`RED_SELECTOR_MODE=reviewed` selects those same reviewed arguments and sources
locally using topic relevance and stable account/context hashes. This is the
current deployment: it removes model calls from the finite selection step and
retains the renderer, source freshness checks and publication quotas. The
default `llm` mode retains organiser-model selection. Neither mode generates free
prose. Source text cannot become instructions or published quotations.

Original posts use internal `post:ACCOUNT:SLOT` planning keys and the verified
SDK `kind=post` contract. Replies/comments use `kind=reply`. Optional
`RED_ALLOW_THREADS=true` accepts NPC replies only after every ancestor, up to
eight messages including target/root, is freshly verified as NPC or a configured
own account. Human or malformed branches fail closed. `GET /feed/lock` is checked
before publication; competition-ended and other platform locks stop/defer writes.

`RED_GIF_ENABLED=true` permits an occasional exact allowlisted GIF body link if
it fits the character limit. No upload endpoint is invented, and rendering a
media preview is the platform's behavior, not an execution guarantee.
The GIF URL precedes the news citation so the platform selects the GIF preview;
the exact source URL remains in the post. This order was verified through the
accepted API embed and the public social interface.

`botfluencer-red-context.timer` refreshes a private snapshot of the two organiser
news front pages hourly. `red refresh-world --output PATH` validates the actual
article title/summary/URL contract before atomic replacement. Snapshots older
than six hours block contextual planning. The background planner and dispatcher
share Postgres ownership, budgets, and a small queue; generated drafts record the
model/prompt/context digest. Default curated operation remains available.

An explicitly requested expansion uses `red amend --reason TEXT` while paused and
drained. Newly supplied account sessions are verified first, then an additive
revision records old/new historical account sets and attempt caps. Historical
accounts may only be added and caps may only increase. Current sessions may omit
historical accounts only after durable quarantine; their identities remain in
the campaign history and own-author exclusions. Every new session must pass
account/team verification. Namespace, archive, start/end times, prior
attempts, unknown outcomes and target reservations remain intact. Ordinary
startup still rejects unreviewed configuration replacement.

Additional private settings: `RED_WORLD_CONTEXT_FILE`, `RED_MAX_WORLD_AGE_HOURS`
(6), `RED_IDENTITY_TIMEOUT_SECONDS` (600), `RED_MAX_PENDING_ACTIONS` (4), and
`RED_POST_INTERVAL_SECONDS` (300 by default, minimum 20, now deployed at 20).
This is a minimum original-post interval, not an account-specific quota. A
namespace-scoped snapshot of the latest planned action gives eligible NPC replies
a turn after originals, even when planning takes longer than the interval.
Discovery skips up to eight ineligible/already-contacted observations per turn.
An exhausted eligible scan permits an original only when due; pacing failures
and timeouts defer work. The combined publisher still observes the shared
three-writes-per-minute allowance and server backoff.
The expanded configuration uses hourly attempt
ceilings of 180 for publications and model calls, a total cap of 8640, a two-second
dispatch polling target, 90-second operations and 130-second heartbeat tolerance.
These ceilings are not throughput promises; server headers and actual latency
still constrain work. The user-service drain grace is 120 seconds.

## Targets and durable limits

The source of eligible NPC author IDs is the explicitly reviewed archive in
`RED_ARCHIVE_NAMESPACE`, default `ctn-2026`. Rows marked `npc_status=npc` provide
the team-confirmed provenance recorded in [post-collection.md](post-collection.md):
the initial capture predates 18:00 Sydney on 19 October 2026, when the team
confirmed competitors were absent. An author with any conflicting `player` row
in that archive is excluded. Own accounts are also excluded. Public verified
badges, writing style, and model predictions do not establish NPC origin. New or
unlabelled authors remain ineligible. The worker loads the reviewed ID set at
startup; restart after an independently reviewed archive correction.

By default, only top-level NPC posts about the fictional referendum topics and
at most four hours old are considered. The executor reads the target again
before publishing and checks its author, kind, and thread root. A reply under
another thread requires the explicit verified-ancestry setting described above.
Reply bodies containing `@` are rejected because
the platform automatically notifies mentioned accounts.

Discovery uses recent posts from the existing blue collector's Postgres archive,
bounded to eight unseen candidates per tick. This avoids competitor traffic
crowding known NPC posts out of the latest-feed window and saves shared feed
quota. Archive rows do not supply thread roots: those remain unknown until the
executor retrieves the current post and verifies its top-level structure. The
archive query applies the same public topic terms before its eight-row limit;
locale-independent ASCII matching skips irrelevant ASCII rows, while multibyte
text passes through for the authoritative Python eligibility check. Filtered
rows are not marked processed. This avoids newer unrelated posts hiding relevant
NPC discussion farther down the archive.
The
blue collector must continue collecting into the configured archive; once its
posts age past four hours the red worker safely idles.

Dispatcher selection prioritizes observations discovered in the past four hours,
then preserves FIFO order within recent and older tiers. Source age is still
validated separately: ingestion time does not establish source freshness.
Older rows remain in the ledger and become selectable after recent work; a
historical backlog cannot take every reply turn ahead of newly discovered work.

Postgres commits each team-wide target reservation and action intent together,
before execution. The same post cannot receive another planned reply across accounts or
policy versions. At most one target per NPC author is reserved in any 24-hour
period. Rejected, interrupted, and unknown outcomes retain those reservations.
`RED_MAX_ATTEMPTS` bounds both total target reservations and total claimed
execution attempts; `BOT_MAX_ACTIONS_PER_HOUR` additionally bounds attempts in
the runtime namespace. Failed calls and safe deferrals after claim consume an
attempt. Account cooldowns and attempt limits remain in the durable ledger.
The coordinator drains existing queued/in-flight work before planning another
reply, so hourly caps and server backoff cannot build an unbounded draft queue.

The campaign has timezone-aware start and end instants, with a positive duration
of at most 48 hours. Its namespace, times, total cap, archive namespace, and
account set are persisted under the team scope. Restarts cannot extend the end
time, replace those values, or reset the caps. Only the audited, explicitly requested
expansion command can add accounts or increase the cap. After expiry the process keeps
its heartbeat but makes no new observations or publication attempts. The context
timer also stops making news requests once that same deadline passes. An existing
campaign must not be deleted or given a new namespace to evade its mandate.

## Private configuration

Use the self-contained `/home/aman/.config/botfluencer/red.env`, outside the
OneDrive checkout. The directory should be mode 0700 and files mode 0600. Retain
recovery files and backups in that private directory as well. The red service
does not read the blue service's private environment file.

| Setting | Required value or default |
| --- | --- |
| `DATABASE_URL` | Existing Railway Postgres URL, kept private |
| `BOT_NAMESPACE` | A dedicated red namespace chosen before campaign initialization |
| `BOT_PLATFORM_BACKEND` / `BOT_MODE` | `ctn` / `observe`; the separate `red serve` command enables this executor |
| `BOT_ACCOUNTS` | JSON array of actual account IDs as strings, matching sessions exactly |
| `CTN_NETWORK_ENABLED` | Explicit `true`; default false |
| `CTN_TEAM_CODE` | Private team code |
| `CTN_SIDE` | Portal-verified assignment compatible with the reviewed Yes policy |
| `CTN_MAX_TOTAL_ACCOUNTS` / `CTN_MAX_ACTIVE_ACCOUNTS` | Verified portal allowances; neither is inferred |
| `RED_ENABLE_REPLIES` | Explicit `true`; default false |
| `RED_EXPECTED_TEAM_ID` | Required private team ID, verified by `/auth/me` |
| `RED_STARTS_AT` / `RED_ENDS_AT` | Required timezone-aware instants, at most 48 hours apart |
| `RED_MAX_ATTEMPTS` | 1200 by default; local maximum 8640 |
| `RED_ARCHIVE_NAMESPACE` | `ctn-2026` by default |
| `RED_SESSIONS_JSON` | Required private JSON list described below |
| `BOT_MAX_ACTIONS_PER_HOUR` | 60 by default; attempted calls, not accepted replies |
| `BOT_ACCOUNT_COOLDOWN_SECONDS` | 5 by default |
| `BOT_MAX_ACTION_ATTEMPTS` | 3 by default; only proven safe deferrals can be retried |
| `BOT_POLL_INTERVAL_SECONDS` | 5 by default; 30 is a conservative workstation setting sharing feed quota with blue |
| `BOT_OPERATION_TIMEOUT_SECONDS` | 30 by default; deployed at 90 with the service's 120-second stop grace |
| `BOT_HEARTBEAT_TIMEOUT_SECONDS` | 60 by default; use 120 when polling is 30 and operation timeout is 60 |
| `BOT_BIND_HOST` / `PORT` | Set `127.0.0.1` / `8001`; defaults are `0.0.0.0` / `8000` |

The generic heartbeat timeout must exceed polling interval plus operation
timeout. Use a distinct health port from the blue service and other local
applications. Changing private environment values requires a service restart;
persisted campaign values still cannot change.

The explicit `red provision` command writes a private recovery envelope with an
`accounts` array. Before each registration request it persists the generated
username and password with status `registering`; acknowledged token/account IDs
are saved before identity verification. Existing recovery files are refused,
and an ambiguous registration is never repeated. The service does not create
accounts at startup.

Registration additionally requires `--current-total N --current-active N` from
the current team portal, including accounts outside this controller. The requested
batch must fit remaining total and active headroom. The server remains
authoritative if the snapshot changes. An existing campaign's database-clock
deadline is checked before recovery-file creation and each registration/identity
request. Banned IDs are never deleted or reused, and uncertain registrations are
not repeated. For example, with freshly verified counts, use:

```sh
botfluencer-workstation --env-file /home/aman/.config/botfluencer/red.env \
  botfluencer red provision --team-id TEAM_ID --count COUNT \
  --current-total REGISTERED --current-active ACTIVE --output PRIVATE_PATH
```

After all entries have status `verified`, transform the recovery envelope's
`accounts` array into a **JSON list** retaining only `account_id`, `token`, and
optional `expires_at`. Store that compact list as `RED_SESSIONS_JSON` in the
private environment file. The recovery envelope itself is not accepted as this
setting. For example, the shape below contains synthetic placeholders:

```dotenv
BOT_ACCOUNTS='["10"]'
RED_SESSIONS_JSON='[{"account_id":10,"token":"PRIVATE_SESSION_TOKEN","expires_at":"2026-11-02T00:00:00+00:00"}]'
```

Use actual verified IDs/tokens and the server-provided expiry. Do not paste the
list into shell command arguments, terminal output, Git, or chat. At startup
each usable token must identify its configured account and expected team through
`/auth/me`. Expired sessions and authentication rejections are quarantined in
Postgres; verified healthy accounts continue. Identity mismatch still fails the
worker. Authentication rejection alone is not proof of an account ban. Known
session expiry and the campaign deadline are checked again immediately before a reply.

Inspect controls with `botfluencer red accounts`. To disable an identified account,
use `botfluencer red quarantine --account ID --reason operator_disabled` with the
private red environment. Quarantine persists across restarts, fails that account's
queued work, and preserves unknown outcomes and campaign history. It does not
replace accounts, lift platform bans, or reassign ambiguous work.

## Start, pause, and inspect

Apply reviewed Alembic migrations before starting the worker. From gaming-pc,
use the explicit red environment for every command:

```sh
botfluencer-workstation --env-file /home/aman/.config/botfluencer/red.env \
  botfluencer db upgrade
botfluencer-workstation --env-file /home/aman/.config/botfluencer/red.env \
  botfluencer red serve
```

The user-service template is
[`deployment/systemd/botfluencer-red.service`](../deployment/systemd/botfluencer-red.service).
It uses the installed launcher, private environment, a five-second restart
delay, a restart limit, SIGTERM draining, and a 120-second stop timeout. Install
it under `~/.config/systemd/user/` for the Ubuntu user `aman`. This template alone
does not prove deployment or health.

In a noninteractive SSH session, point commands at that user's existing systemd
manager before enabling the unit:

```sh
export XDG_RUNTIME_DIR="/run/user/$(id -u)"
export DBUS_SESSION_BUS_ADDRESS="unix:path=$XDG_RUNTIME_DIR/bus"
mkdir -p ~/.config/systemd/user
cp deployment/systemd/botfluencer-red.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now botfluencer-red.service
systemctl --user status botfluencer-red.service
journalctl --user -u botfluencer-red.service -n 20
curl --fail http://127.0.0.1:8001/health/worker
```

Run installation commands from the checkout. The workstation's existing user
manager must be available; the environment variables do not create a manager
or enable lingering. `/health/ready` can pass for a standby deployment;
`/health/worker` additionally requires active ownership, a fresh heartbeat, and
database connectivity. An expired or paused campaign can still have a healthy
heartbeat, so inspect the stored state and configured end time too.

```sh
botfluencer-workstation --env-file /home/aman/.config/botfluencer/red.env \
  botfluencer status
botfluencer-workstation --env-file /home/aman/.config/botfluencer/red.env \
  botfluencer pause
botfluencer-workstation --env-file /home/aman/.config/botfluencer/red.env \
  botfluencer resume
```

Pause is persisted in the red namespace and stops new red work while keeping
the heartbeat running. A call already in flight may finish. For immediate
bounded shutdown, use `systemctl --user stop botfluencer-red.service` and wait
for the service to drain. Resuming does not extend the campaign window. Blue
collection/reporting controls remain separate; keep using its own environment
and `blue` commands as described in [blue-team.md](blue-team.md).

## Execution and availability guarantees

All intents and attempt budgets commit before the external write. A singleton
Postgres advisory lock permits one active red worker per namespace. Red and blue
share the Postgres team pacer. Blue collects feeds into the common archive;
red performs fresh target reads and uses the shared publication bucket. Server rate headers
and `Retry-After` constrain subsequent requests across processes using that
ledger. Account registration separately uses conservative 13-second auth pacing.
Scripts outside this pacer still consume the same organiser quota and must be
coordinated.

An acknowledged reply must match the intended author, body, target, and thread
root. A definitive rejection fails the action; a 429 can schedule a safe durable
deferral. Timeout, interruption, lost ownership, 408, 5xx, redirect, malformed
acknowledgement, or post-write bookkeeping failure is ambiguous. Recovery marks
interrupted in-flight actions `unknown`. SDK 0.5.0 has no documented idempotency
key or authoritative acceptance lookup, so unknown writes remain unresolved and
are never automatically retried.

Worker failure exits the process even if the HTTP server could still run.
Systemd can restart failures within its limit; a stale heartbeat or exhausted
restart limit requires operator investigation. The PC must remain awake and its
existing WSL/Tailscale/SSH startup layer must work. Full Windows reboot recovery
remains unverified as recorded in [gaming-pc.md](gaming-pc.md). This deployment
does not change Windows sleep settings, startup tasks, or power policy.
