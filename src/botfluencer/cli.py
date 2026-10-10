"""Operator entrypoint with sanitized configuration and failure output."""

import argparse
import asyncio
import json
import sys
from importlib.metadata import version

from pydantic import ValidationError

from botfluencer.config import Settings
from botfluencer.runtime.events import configure_logging
from botfluencer.runtime.service import run_worker, serve
from botfluencer.storage.database import Database
from botfluencer.storage.migrations import upgrade
from botfluencer.storage.repository import Repository


async def database_command(settings: Settings, operation: str) -> None:
    database = Database(settings)
    repo = Repository(database)
    try:
        async with asyncio.timeout(settings.operation_timeout_seconds):
            if operation == "status":
                print(json.dumps(await repo.summary()))
            elif operation in {"pause", "resume"}:
                await repo.set_paused(operation == "pause")
                print(json.dumps({"namespace": settings.namespace, "paused": operation == "pause"}))
    finally:
        await database.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="Botfluencer mock competition runtime")
    parser.add_argument("--version", action="version", version=version("botfluencer"))
    commands = parser.add_subparsers(dest="command", required=True)
    db = commands.add_parser("db", help="Database schema operations")
    db.add_argument("operation", choices=["upgrade"])
    for name in ("status", "pause", "resume"):
        commands.add_parser(name)
    run = commands.add_parser("run", help="Run the mock worker without HTTP")
    run.add_argument("--ticks", type=int, help="Stop after this many successful ticks")
    commands.add_parser("serve", help="Supervise worker and HTTP health server")
    args = parser.parse_args()
    if args.command == "run" and args.ticks is not None and args.ticks < 1:
        parser.error("--ticks must be positive")
    configure_logging()
    try:
        settings = Settings()
        if args.command == "db":
            upgrade(settings)
        elif args.command == "run":
            asyncio.run(run_worker(settings, args.ticks))
        elif args.command == "serve":
            asyncio.run(serve(settings))
        else:
            asyncio.run(database_command(settings, args.command))
    except ValidationError as error:
        # Pydantic's default repr includes the invalid input, which can be a secret.
        fields = sorted({".".join(str(part) for part in item["loc"]) for item in error.errors()})
        print(f"Invalid configuration: {', '.join(fields)}", file=sys.stderr)
        sys.exit(2)
    except Exception as error:
        print(
            f"Command failed ({type(error).__name__}); see the operating runbook", file=sys.stderr
        )
        sys.exit(1)
