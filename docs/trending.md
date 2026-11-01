# Bounded visibility strategy

This experiment operates only inside CTN. It does not guarantee Trending placement,
persuasion, scores or protection from moderation. Workers remain on gaming-pc;
Railway provides the existing durable Postgres only.

The organiser's [scoring guide](https://capturethenarrative.com/info/competitions/2026/scoring/)
states that recent likes, replies and reposts affect Trending. Red points come
from NPC activity, not a team's own reactions. Reposting NPC content does not
transfer original-author credit. Ranked feed snapshots measure visibility;
receipts and synthetic tests are not influence scores.

The [rules](https://capturethenarrative.com/info/competitions/2026/rules/) permit
automation inside the fictional environment and distinguish its in-game conduct
policies from competition restrictions. Respect rate limits, attribution and
sandbox boundaries. No NPC prompt injection or real-world social activity is used.

## Selection and execution

`RED_ENGAGEMENT_ENABLED` defaults to false. When enabled, the same namespace owner
and sole publisher dispatch engagement and editorial intents from the existing
ledger. The generic CTN executor remains blocked.

Recent accepted own originals absent from the latest observed top-20 Trending
feed receive priority. An initial like, a repost and a reviewed comment precede
further likes. Actors cannot engage with their own post. Each actor/target/kind
is reserved once across policy versions. A fresh snapshot containing the target
stops additional candidate boosts while that snapshot remains current.

Own targets require a succeeded action and accepted external receipt. The live
author, top-level structure and exact body digest must still match. Team comments
use finite reviewed text on acknowledged own originals, without human-root threads.
After own boosts, editorial planning gets a turn; reviewed NPC support is secondary.
NPC candidates must be known, non-conflicting NPCs with explicit positive Vote Yes
language and a public referendum topic. Negation, quotes, instructions, ambiguity
and unreviewed links fail closed. Human and unknown authors remain excluded.
Fresh server timestamps and body digests are checked again; targets are at most
two hours old. Candidate text never supplies tool permission or proof.

SDK 0.5.0 contracts: likes use authenticated `POST /posts/{id}/like`; reposts use
`POST /posts` with `kind=repost` and `repost_of_post_id`; team comments use the
reply contract. Likes share the documented 30/minute bucket; posts, replies and
reposts share 3/minute. The deployed 30-second publication floor and stricter
server backoff take precedence. Additional accounts do not multiply these limits.

Migration 0009 adds reservations, identifier/rank snapshots and mandate audits.
Intent is committed before a write. The campaign lock protects actor/target caps,
the combined pending queue, pause, quarantine and window checks. All claimed
actions consume the existing hourly attempt budget and cumulative campaign cap.

Timeouts, 5xx and malformed acknowledgements remain unknown. Authenticated
`GET /likes/mine` and `GET /reposts/mine` can confirm positive existing state;
absence never authorizes reissuing an unknown write. Recent unknown engagements
receive bounded persisted read-reconciliation schedules. Ambiguous comments stay
unknown because there is no authoritative absence lookup.

## Settings

| Setting | Default | Meaning |
| --- | --- | --- |
| `RED_ENGAGEMENT_ENABLED` | false | Explicit strategy opt-in |
| `RED_ENGAGEMENT_MAX_AGE_HOURS` | 2 | Maximum source age, at most two hours |
| `RED_ENGAGEMENT_LIKES_PER_TARGET` | 3 | Distinct actors, configurable through 49 |
| `RED_ENGAGEMENT_REPOSTS_PER_TARGET` | 1 | Reserved repost cap per target |
| `RED_ENGAGEMENT_COMMENTS_PER_TARGET` | 1 | Reviewed own-post comment cap |
| `RED_ENGAGEMENT_MAX_PER_HOUR` | 100 | Reservation cap including failed/unknown intents |
| `RED_ENGAGEMENT_SCAN_LIMIT` | 8 | Bounded candidate scan |
| `RED_TRENDING_REFRESH_SECONDS` | 120 | Snapshot interval, subject to shared feed pacing |

These are application ceilings, not extra platform allowances or throughput
promises. Safe retries still consume the existing attempt budgets.

## A subsequent mandate

The first mandate expired on 1 November at 00:45 Sydney. A fresh user request can
authorize a new bounded window using the same namespace, cap and history. Pause,
drain and stop the worker, then invoke:

```sh
botfluencer-workstation --env-file /home/aman/.config/botfluencer/red.env \
  botfluencer red authorize-window --ends-at 2026-11-02T07:00:00Z \
  --reason 'User-requested visibility strategy until verified competition close'
```

The authenticated portal was checked on 1 November and shows closure at **18:00
Sydney on 2 November**, the end above. Renewal requires the namespace ownership
lock, an expired paused campaign and no in-flight actions. It appends a revision,
records database time as the new start, and limits duration to 48 hours. It retains
the 8,640-attempt cap, usage, unknowns, reservations, quarantines and historical
identities. Old queued intents receive a failed expiry outcome rather than replay.

Install the exact returned start/end privately; startup still requires an exact
match and never renews automatically. Replenishment uses fresh portal counts and
the existing recovery-aware provisioning/amendment workflow. Resume after schema,
roster and context verification. Never delete or re-namespace a campaign to evade
a stop or budget. Credentials and competition exports remain outside Git.

## Parallel throughput opt-in

`RED_PARALLEL_LANES=true` runs independent bounded like and publication tasks
under the same namespace owner. `RED_LIKE_CONCURRENCY` is 1–8 (default 4).
Publication dispatch remains single-file: posts, NPC replies, reposts and team
comments share the official 3/minute bucket. Likes use their separate 30/minute
bucket. Background work stops before ownership is released; all tasks retain
persisted intent, unknown-result recovery and the shared team pacer. Default
serial behavior remains unchanged.

The publication planner gives both editorial and boost work turns; like batches
have separate planning and execution. Half the pending queue is reserved for
likes, half for publications, enforced inside the campaign transaction. Parallel
own-post likes may continue after observed Trending entry; NPC provenance remains
unchanged. Recent own posts receive priority. Configurable repost caps support
multiple distinct peers but cannot enlarge the shared publishing allowance.

The requested deployment prioritises originals and NPC replies, with one repost
and one reviewed team comment per original, up to 48 peer likes, 8 like dispatchers,
16 pending slots, 1-minute original spacing and a 20-second publication floor.
Hourly ceilings are 1,980 total attempts and 1,800 engagement reservations;
these are ceilings, not measured throughput. Increasing the cumulative cap uses
the existing paused, verified `red amend` audit rather than resetting history.
The requested deployment cap is 50,000 attempts with the existing deadline.
A stopped-owner pacing adjustment from the old 30-second floor to 20 seconds
must preserve `next_allowed_at`, including existing backoff; subsequent server
headers and 429s remain authoritative.
