# Blue-team workflow

The target is competitor-controlled posts, not generic AI writing. NPCs may also
use models. Confirmed pre-18:00 Sydney NPC authors are excluded by author ID,
including their later posts; unknown post-opening authors are not automatically
players. Own-account IDs are excluded when configured. No demographic targeting,
NPC prompt injection, account creation, posting, or account suspension automation
is part of this workflow.

## Training notebooks

Templates in `notebooks/` contain no outputs or credentials. Run private copies
in `/home/aman/botfluencer-notebooks/blue/` on gaming-pc using **Python (botfluencer
CUDA)**. Do not execute/save outputs in the OneDrive Desktop checkout.

1. `01_blue_dataset.ipynb`: export confirmed NPCs from Railway; optionally generate
   balanced Yes/No synthetic player examples using the competition Qwen endpoint.
   Set `RUN_SYNTHESIS=True` explicitly. 150 examples per side uses 300 model calls
   and takes at least approximately 100 minutes at the documented shared 3/minute
   limit. Calls use the existing team pacer/model lock and can be resumed. No
   generated example is published. Coordinate model usage with teammates.
2. `02_blue_train.ipynb`: set `RUN_TRAINING=True`. Fine-tune DeBERTa-v3-small with
   FP32 weights, batch 2, gradient accumulation 4, sequence length 256 and early
   stopping. Training negatives are capped at twice the positive count; held-out
   NPC populations are retained for false-positive evaluation. It also evaluates
   TF-IDF/logistic regression. Every run writes a new private release directory.
3. `03_blue_review_activate.ipynb`: inspect calibration/test metrics and activate a
   checksum-verified release for shadow scoring. Later, evaluate independently
   reviewed real NPC/player labels before considering automatic reporting.

Authors/context groups are deterministically separated 80/10/10. Synthetic
examples stay with the NPC author whose topic supplied their context. Duplicate
text cannot cross splits; contradictory origin labels are discarded. Yes/No is
balanced in synthesis but is never the classifier's label. The labels are NPC=0
and synthetic competitor-style=1. Unknown live posts and model predictions must
not become training truth. Real held-out author labels are needed to establish
actual detection quality. No score or winning strategy is promised.

The validation split selects temperature and a high-precision threshold; the test
split is reported separately. A threshold of 1.0 means no useful operating point
was found. Inspect confidence intervals, not just point precision/accuracy.
Synthetic test success cannot validate automatic reporting. Template generation,
model training and activation are off until their notebook switches are enabled.
Later, add independently reviewed real examples to private
`reviewed-training.jsonl` for retraining. They need `author_id`, binary `label`,
`text`, and `source=team_review|moderator`; the helper derives the author group
and rejects conflicting group IDs. Keep a separate real holdout for final evaluation.

The operational exporter also supports accepted own publications as controlled
positive examples, with source `own_published_controlled`. Actual author IDs
provide disjoint splits; conflicting NPC labels and duplicate text are excluded.
These are not competitor labels or independently reviewed evaluation. Training
writes provenance before sealing its checksum into the manifest. Legacy or
altered provenance cannot authorize automatic reports.

To use Jupyter over SSH, in Ubuntu:

```sh
mamba activate botfluencer
cd /home/aman/botfluencer-notebooks/blue
jupyter lab --ip=127.0.0.1 --no-browser
```

In a Mac terminal, run `ssh -L 8888:127.0.0.1:8888 gaming-pc` and use Jupyter's
local authenticated URL. Keep the token private. Jupyter is not a public service.

## Scheduled collection and inference

A resident Ubuntu user service, `botfluencer-blue.service`, polls every 30 seconds.
Each cycle reads at most three pages of 200 posts, using the shared 10/minute
feed allowance. Cursor and newest-page anchor persist in Postgres. Catch-up
continues across ticks before returning to the newest page. Pages commit before
cursor advancement, so interruption can replay a page but cannot silently skip
its contents. Invalid/repeated cursors fail rather than claim complete coverage.
A cursor that expires requires investigation and an explicit state reset; no
silent fallback to only the newest page is used.

The worker runs under a singleton advisory lock. Without an active model it
continues collecting and records `waiting_for_model`. Once activated, one local
GPU model stays resident and scores up to 128 fresh posts per cycle by default,
in batches of 8. It writes scores keyed by post and model version, never downloads a model
at runtime or enables remote model code. Model files and inference settings have
checksums; activation uses an atomic symlink. Initial ML imports on the Windows
D: mount can take minutes. Warm latency depends on backlog and shared quota;
30 seconds is a polling target, not a guaranteed end-to-end deadline.

Known NPCs, configured own accounts, empty bodies, and posts older than 24 hours
are excluded before inference. At least two distinct high-scoring texts from
one author are required to create a review candidate. This corroboration is evidence,
not a calibrated account-identity probability. New authors may need another post
before flagging. Different models never overwrite earlier scores.

Pause before training to release the service's resident CUDA model:

```sh
botfluencer-workstation --env-file ~/.config/botfluencer/runtime.env botfluencer blue pause
botfluencer-workstation --env-file ~/.config/botfluencer/runtime.env botfluencer blue status
# Wait until last_event is paused before starting training.
# After training, restart the notebook kernel, activate the release, then:
botfluencer-workstation --env-file ~/.config/botfluencer/runtime.env botfluencer blue resume
systemctl --user status botfluencer-blue
journalctl --user -u botfluencer-blue -n 20
```

Pause disables inference/reporting and releases the GPU; read-only collection
continues during training. Use `systemctl --user stop botfluencer-blue` to stop
feed reads as well. The pause is durable. Resuming changes only pause state,
not reporting opt-in.
Process failures exit nonzero; systemd restarts with backoff and a restart limit.
Check `heartbeat_age_seconds` as well as systemd state: a stale worker is not
healthy simply because its process exists. Monitoring/notification delivery is
not installed. Windows must stay awake, the WSL startup task must work, and
Tailscale must remain connected. Full Windows reboot recovery remains unverified.

## Reports and moderator outcomes

The narrow report adapter follows the pinned SDK's authenticated
`POST /posts/{post_id}/report`, with `reason=inauthentic` and an evidence detail.
This requires a **Legit Social account session token**; the team code is the model
credential and cannot replace it. The installed configuration submits only
explicitly approved review candidates; none were approved at deployment.
No report endpoint is called by the training notebooks.

Reports deduplicate by hashed team scope/post ID across model versions and
namespaces. An intent commits before submission. Timeout, lost ownership,
interruption, 5xx, or invalid acknowledgement produces `unknown`; it is never
blindly retried. A 429 response is a definitive deferral. Accepted means API
acknowledgement, not a favourable moderator ruling or earned points. Server
throttling is honoured. There is no documented idempotency/reconciliation API for
uncertain reports, so they require external verification.

Private configuration belongs in `~/.config/botfluencer/blue.env` (0600):

| Setting | Installed/default behaviour |
| --- | --- |
| `BLUE_NAMESPACE` | `ctn-2026`, shared archive with the confirmed NPC snapshot |
| `BLUE_ARTIFACT` | `/mnt/d/botfluencer/models/blue-active` |
| `BLUE_INTERVAL_SECONDS` | 30 |
| `BLUE_ENABLE_REPORTS` | default false; installed true for manually approved candidates only |
| `BLUE_AUTO_REPORT` | false; manual approval required |
| `BLUE_OWN_AUTHOR_IDS` | JSON list of your actual account IDs; required before reports |
| `BLUE_SESSION_TOKEN` | private own-account token, installed outside the checkout |
| `BLUE_REPORTER_ACCOUNT_ID` / `BLUE_EXPECTED_TEAM_ID` | required matching account/team IDs, authenticated before dispatch |
| `BLUE_SCORE_LIMIT` / `BLUE_BATCH_SIZE` | defaults 128 / 8; installed 256 / 8 |
| `BLUE_MAX_PENDING_REPORTS` | 1 unresolved submission |
| `BLUE_MAX_REPORTS_PER_HOUR` | 3 attempted submissions, persisted team-wide |

Before enabling reports, configure the token/own IDs privately and review the
current rules and candidate evidence. `blue approve --post-id ID` approves an
existing review candidate; enabling `BLUE_ENABLE_REPORTS` lets the scheduled
worker submit approved candidates. Automatic approval additionally requires
`BLUE_AUTO_REPORT=true` and `real-validation.json` tied to the exact model version,
with author-disjoint independently reviewed real labels: at least 100 positives,
400 negatives, lower 95% precision bound >=0.95, upper 95% FPR bound <=0.01.
These are conservative local policies, not competition rules or a guarantee.
Changing environment settings requires restarting the user service.

Inspect candidate metadata with `botfluencer blue candidates --limit 20`, using
both private runtime and blue environment files. `--include-excerpt` adds bounded
source content for private review; keep it out of logs and committed exports.
Approval requires reviewing the evidence. Repeated copies of the same text do
not corroborate an author, and a score does not establish account origin.

HTTP 408, redirects and server errors remain ambiguous alongside timeouts and
interruption. Ownership checks verify the physical database backend before and
after submission. Attempt limits count each claim, including a later 429 deferral.

## Verified workstation release — 30 October 2026

`/mnt/d/botfluencer/models/blue-operational-20261030` is active through the
`blue-active` symlink. Three FP32 CUDA epochs used 3,111 known NPC examples and
432 accepted own publications (75 unique positive texts before splitting).
The deployed threshold is 0.8. On the own-author diagnostic test it detected
4 of 8 positives with zero false positives among 297 NPC negatives. This small
controlled test does not establish competitor detection quality; the release
explicitly fails automatic-report validation.

The live service confirmed `last_event=scored`, a fresh heartbeat and 2,816 scored
posts at the deployment checkpoint. The maximum observed score was 0.754,
below threshold: there were zero review candidates or report attempts. This is
an observed detector limitation, not evidence that the feed has no competitors.
Unknown authors remain unknown; predictions do not become training truth.
An independently reviewed holdout remains necessary for automatic reporting.

## Moderator outcomes

Record an independently verified moderator ruling with:

```sh
botfluencer-workstation --env-file ~/.config/botfluencer/runtime.env \
  botfluencer blue outcome --post-id ID --ruling inauthentic
# Or --ruling genuine: persists a conservative six-hour reporting block.
```

The default one-pending budget waits for a recorded ruling before another
submission. Moderator-outcome ingestion is manual: the authenticated notification
payload/outcome contract has not been verified, so no outcome is inferred from
post deletion, temporary hiding, or an HTTP 404. This limits unattended reporting
throughput. Shadow feed/scoring remains autonomous without account credentials.
Account reports are deferred: they require a moderator-hidden post and at least
100 account actions, not just a detector score.

The [current organiser scoring guide](https://capturethenarrative.com/info/competitions/2026/scoring/)
was rechecked on 23 October: first successful post report 25 points, later reports
5, only the first five teams eligible; initial review scheduled from three hours;
a genuine ruling causes a six-hour reporting suspension. Awards diminish across
post/account reports against the same team. The page has stale contradictory
examples lower down; the updated header and dedicated reporting section say
three hours. Do not infer competitor team IDs from writing style.
