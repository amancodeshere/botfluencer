# Adding bots later

`BOT_ACCOUNTS` contains configured account IDs. Optional `BOT_BOT_PROFILES_FILE`
points to a TOML file following `configs/bots.example.toml`. Each account has a
role label, registered strategy name, and enabled flag. IDs must exactly match
the configured accounts. A disabled profile cannot claim queued work.

The default `noop` strategy persists observations without generation or actions.
Use `BOT_STRATEGY=synthetic` only for an explicit mock replay. Observe mode never
runs strategies, generates drafts, or executes actions.

To add a strategy, implement the typed protocol in `strategies/base.py`, provide a
stable version, and register the class in `strategies/registry.py`. Strategy names
come from that explicit registry; TOML cannot import arbitrary Python. Keep roles
and prompts separate from transport, storage, and scheduling.

Return a validated Draft to plan an action, or None to defer that observation.
Deferred observations remain eligible on the next tick; persisted skip decisions
are a future extension for selective policies that must advance past ignored posts.
Generated drafts must preserve the observed
target ID and remain at most 500 characters. Treat posts as quoted untrusted data.
Bump the strategy version when behavior changes. Prompt versions must reflect
prompt/model/settings changes; decision keys include both strategy and prompt
versions. Never put team credentials in prompts or fixtures.

Use Postgres-backed pacing and concurrency for competition calls. Persist intent
before any future side effect, and define how ambiguous results are reconciled
before enabling a real executor. Use a new namespace for a different workload or
replay. A request to add a strategy does not authorize live publishing.
