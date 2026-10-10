# Botfluencer

Team workspace for Capture the Narrative 2026. Current scope: repo scaffolding,
Postgres persistence, and operational reliability. Competition access is pending.
The platform and language model are deliberately mocked; no competition actions
or external model calls are implemented.

## Development

Use Python 3.13 and [uv](https://docs.astral.sh/uv/). Read [AGENTS.md](AGENTS.md)
before making changes.

```sh
uv sync --locked
uv run botfluencer --help
uv run ruff check .
uv run ruff format --check .
uv run mypy src
uv run pytest
```

Runtime, database, and deployment instructions will be added with their stages.
Keep credentials in environment variables or an ignored `.env`; commit only the
placeholder `.env.example`.
