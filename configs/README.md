# Runtime configuration

Settings currently come from environment variables or an ignored `.env`.
See `.env.example` for defaults; `BOT_ACCOUNTS` is a JSON list of mock account IDs.
These limits are local operational caps, not competition limits. Zero action or
LLM budgets disable new attempts or generation respectively.

Use distinct namespaces and databases for local replay, tests, Railway rehearsal,
and future live competition activity. A namespace is permanently associated with
its execution mode. Real account credentials will remain environment secrets.
