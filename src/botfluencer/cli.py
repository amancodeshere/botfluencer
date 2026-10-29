"""Operator entrypoint with sanitized configuration and failure output."""

import argparse
import asyncio
import json
import sys
from datetime import datetime
from importlib.metadata import version

from pydantic import ValidationError

from botfluencer.blue.config import BlueSettings
from botfluencer.blue.worker import operator as blue_operator
from botfluencer.blue.worker import watch as blue_watch
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
    red = commands.add_parser("red", help="Bounded organiser NPC reply campaign")
    red_commands = red.add_subparsers(dest="red_operation", required=True)
    red_commands.add_parser("serve", help="Run the explicitly enabled simulation campaign")
    provision = red_commands.add_parser(
        "provision", help="Create private simulation account sessions"
    )
    provision.add_argument("--output", required=True)
    provision.add_argument("--count", type=int, default=4)
    provision.add_argument("--team-id", type=int, required=True)
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
    collect.add_argument(
        "--npc-before",
        type=datetime.fromisoformat,
        help="Team-confirmed NPC cutoff, ISO timestamp with timezone",
    )
    collect.add_argument("--max-pages", type=int, default=1000)
    collect.add_argument("--timeout-seconds", type=float, default=900)
    blue = commands.add_parser("blue", help="Blue-team shadow collector, scorer, and report ledger")
    blue_commands = blue.add_subparsers(dest="blue_operation", required=True)
    watch = blue_commands.add_parser("watch")
    watch.add_argument("--allow-network", action="store_true")
    watch.add_argument("--once", action="store_true")
    for operation in ("status", "pause", "resume"):
        blue_commands.add_parser(operation)
    approve = blue_commands.add_parser("approve")
    approve.add_argument("--post-id", type=int, required=True)
    outcome = blue_commands.add_parser("outcome")
    outcome.add_argument("--post-id", type=int, required=True)
    outcome.add_argument("--ruling", choices=["genuine", "inauthentic"], required=True)
    args = parser.parse_args()
    if args.command == "run" and args.ticks is not None and args.ticks < 1:
        parser.error("--ticks must be positive")
    if args.command == "doctor" and args.llm and not args.online:
        parser.error("--llm requires --online")
    configure_logging()
    try:
        settings = Settings()
        if args.command == "red":
            if args.red_operation == "serve":
                asyncio.run(serve(settings, red=True))
            else:
                from pathlib import Path

                from botfluencer.red.provision import provision as provision_accounts

                print(
                    json.dumps(
                        asyncio.run(
                            provision_accounts(
                                settings,
                                Path(args.output),
                                args.count,
                                expected_team_id=args.team_id,
                            )
                        )
                    )
                )
        elif args.command == "blue":
            blue_config = BlueSettings()
            if args.blue_operation == "watch":
                settings = settings.model_copy(
                    update={
                        "ctn_network_enabled": settings.ctn_network_enabled or args.allow_network,
                    }
                )
                asyncio.run(blue_watch(settings, blue_config, once=args.once))
            else:
                print(
                    json.dumps(
                        asyncio.run(
                            blue_operator(
                                settings,
                                blue_config,
                                args.blue_operation,
                                getattr(args, "post_id", None),
                                getattr(args, "ruling", None),
                            )
                        )
                    )
                )
        elif args.command == "collect-npc-posts":
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
                            settings,
                            args.namespace,
                            args.max_pages,
                            args.timeout_seconds,
                            args.npc_before,
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
