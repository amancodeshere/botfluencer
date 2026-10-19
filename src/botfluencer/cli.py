"""Operator entrypoint with sanitized configuration and failure output."""

import argparse
import asyncio
import json
import sys
from importlib.metadata import version

from pydantic import ValidationError

from botfluencer.config import Settings
from botfluencer.platform.collect import collect_posts
from botfluencer.reporting.doctor import doctor
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
    check = commands.add_parser(
        "doctor", help="Validate baseline setup without exposing credentials"
    )
    check.add_argument(
        "--online", action="store_true", help="One read-only feed probe after opening"
    )
    check.add_argument("--llm", action="store_true", help="Also make one explicit model smoke call")
    collect = commands.add_parser(
        "collect-npc-posts", help="Archive all public recent-feed pages; never publishes"
    )
    collect.add_argument(
        "--allow-network", action="store_true", help="Opt in for this read-only run"
    )
    collect.add_argument("--namespace", default="ctn-2026")
    collect.add_argument("--max-pages", type=int, default=1000)
    collect.add_argument("--timeout-seconds", type=float, default=900)
    args = parser.parse_args()
    if args.command == "run" and args.ticks is not None and args.ticks < 1:
        parser.error("--ticks must be positive")
    if args.command == "doctor" and args.llm and not args.online:
        parser.error("--llm requires --online")
    configure_logging()
    try:
        settings = Settings()
        if args.command == "collect-npc-posts":
            if args.max_pages < 1 or not 0 < args.timeout_seconds <= 86400:
                parser.error(
                    "Collection requires positive page limit and timeout up to 86400 seconds"
                )
            settings = settings.model_copy(
                update={
                    "ctn_network_enabled": settings.ctn_network_enabled or args.allow_network,
                }
            )
            print(
                json.dumps(
                    asyncio.run(
                        collect_posts(
                            settings, args.namespace, args.max_pages, args.timeout_seconds
                        )
                    )
                )
            )
        elif args.command == "db":
            upgrade(settings)
        elif args.command == "run":
            asyncio.run(run_worker(settings, args.ticks))
        elif args.command == "serve":
            asyncio.run(serve(settings))
        elif args.command == "doctor":
            report = asyncio.run(doctor(settings, args.online, args.llm))
            print(json.dumps(report))
            if report.get("online", {}).get("status") == "blocked":
                sys.exit(1)
        else:
            asyncio.run(database_command(settings, args.command))
    except ValidationError as error:
        # Pydantic's default repr includes the invalid input, which can be a secret.
        fields = sorted(
            {
                ".".join(str(part) for part in item["loc"]) or "runtime settings"
                for item in error.errors()
            }
        )
        print(f"Invalid configuration: {', '.join(fields)}", file=sys.stderr)
        sys.exit(2)
    except Exception as error:
        print(
            f"Command failed ({type(error).__name__}); see the operating runbook", file=sys.stderr
        )
        sys.exit(1)
