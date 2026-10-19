# Read-only public post collection

`npc_posts` stores the requested `user_name`, `name`, `post`, and `time_stamp`
columns. `user_name` is the author's username, `name` is the display name, `post`
is the original body, and `time_stamp` is the source creation time normalized to
UTC. Empty repost bodies remain NULL; unavailable author names remain NULL.
Additional columns hold namespace, source post/author IDs, kind, collection time,
and classification. The composite namespace/post-ID key deduplicates reruns.

The public API exposes no NPC identifier. Every imported row has
`npc_status='unverified'`, including accounts with verified badges. The table name
reflects the requested archive name, not a proven NPC-only classification. Player
posts may also be present. No text-based NPC inference is performed.

## Capture

```sh
uv run botfluencer db upgrade
uv run botfluencer collect-npc-posts --allow-network --namespace ctn-2026
```

This explicit command opts in to reads for that run; it does not change `.env`,
GitHub variables, normal worker mode, or publishing controls. Public account/feed
reads do not need authentication. The team code is used locally to coordinate the
shared request budget; it is not sent to anonymous API routes.

The collector first walks the recent feed using opaque cursors until exhausted,
committing bodies and timestamps immediately. It then fills missing names in
batches from the public account directory, stopping when all observed authors
are resolved. Missing/new authors are looked up individually once. It includes
whatever post kinds the public feed exposes and does not crawl private profiles.
A missing/inaccessible profile preserves the post with NULL names.
It does not register, post, like, report, follow, record views, or call the model.

Rate headers and Retry-After are respected through durable shared pacing. Each
request/wait has a 180-second deadline; the run defaults to 900 seconds and at most
1000 pages per listing. Increase `--timeout-seconds` (maximum 86400) or `--max-pages`
explicitly for a larger history. Only safe GETs retry transient transport/502/503/504
errors, up to three attempts. Repeated/broken cursors and exhausted safety limits
fail rather than claim completion. A singleton lock prevents overlapping collectors
in the same namespace.

Each post page commits independently. A failed/interrupted run retains completed
pages; rerunning starts from the newest page and deduplicates existing rows.
Existing rows preserve the first captured content and non-NULL names. Name
enrichment only fills NULL fields, including after an interrupted lookup phase;
it never replaces a post body with a later edit. New posts arriving during a traversal require another run.
A completed traversal means the available public recent-feed history was exhausted,
not that deleted, hidden, private, or future posts have been recovered.

## Inspect privately

Run this through an authenticated database connection, not public CI logs:

```sql
SELECT user_name, name, post, time_stamp
FROM npc_posts
WHERE namespace = 'ctn-2026'
ORDER BY time_stamp, post_id;
```

Do not commit collected posts, profile data, exports, or database URLs. Progress
logs and the command summary show only counts and completion metadata.
