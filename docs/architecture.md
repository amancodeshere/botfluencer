# Architecture

## Agreed choices

Python 3.13 with uv; Railway hosting; Postgres durability. One asynchronous
application owns the worker and exposes health endpoints. Accounts are
configuration entries rather than separate deployments.

The runtime observes, persists observations, creates versioned decisions and
action intents, executes bounded actions, and records outcomes. Platform and
LLM adapters are replaceable. The first adapters use synthetic fixtures only.

## Boundaries

`config.py` validates environment configuration. `platform/` defines the platform
contract and mock behavior. `llm/` defines bounded structured generation.
`strategies/` selects actions. `storage/` owns database transactions and migrations.
`runtime/` owns scheduling, control, and recovery. `reporting/` exposes health and
operator summaries. `cli.py` provides operational commands.

Real API behavior, quotas, and score attribution are unknown. Replay tests prove
software behavior; they do not estimate citizen persuasion or competition score.
