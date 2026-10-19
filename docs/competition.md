# Verified CTN 2026 baseline

Initially checked 17 October 2026 against the official
[getting-started guide](https://capturethenarrative.com/info/competitions/2026/getting-started/),
[rules](https://capturethenarrative.com/info/competitions/2026/rules/),
[scoring guide](https://capturethenarrative.com/info/competitions/2026/scoring/),
and [SDK 0.5.0](https://pypi.org/project/capturethenarrative/0.5.0/), updated
19 October 2026 for live read collection.
Recheck organiser changes before enabling access.

Opening: 19 October 2026, 17:00 Australia/Sydney. Every competition request checks
this gate and explicit network opt-in. The authenticated portal confirmed team
readiness, side, and account allowances; these are private environment settings,
not assumptions inherited from an earlier event. No competition accounts created.

| Contract | Baseline |
| --- | --- |
| Social API | `https://social.legitreal.com/api` |
| News | `https://sites.legitreal.com` |
| Model endpoint | `https://llm.legitreal.com/v1` |
| Sole model | `qwen2.5-3b-instruct` |
| Model credentials | Team code; no OpenAI key fallback |
| Output | At most 1024 tokens, one completion; default 150 |
| Posts | At most 500 characters |

Documented team limits per minute: 3 combined posts/replies/reposts; 10 feed reads;
30 each likes, follows, and views; 3 each post/account reports; 300 per other-read route (the collector shares one conservative read bucket).
Authentication is 5/minute per network address. Model limit: 3/minute, burst 2,
concurrency 1. The baseline deliberately spaces model calls instead of using burst.
Server rate headers and Retry-After update durable pacing. These budgets cover
processes sharing this Postgres ledger; unrelated scripts must use the same limiter
or reserve explicit team quota. News pacing is local policy, not an official quota.

The async HTTPX adapters follow SDK wire contracts and keep requests cancelable.
The SDK is pinned for contract reference, rather than run in unbounded threads.
Social and news adapters expose reads only. The model adapter validates structured
JSON and the source target, and serializes generation with a shared advisory lock.
`doctor --online --llm` is an explicit quota-consuming probe after opening; normal
observe mode does not generate drafts. No endpoint for real publication is exposed.

Execution receipts are separate from influence scoring. The scoring guide describes
Red/Blue/Polling views; NPC engagement and the daily view cap matter, but this
scaffold does not estimate persuasion or score. NPC prompt injection is prohibited
and can silently yield zero score. Keep activity inside the organiser's simulation.
No competition strategy, medical advice generator, or moderation evasion is built.
