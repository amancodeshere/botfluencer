import asyncio
from contextlib import suppress

import uvicorn

from botfluencer.config import Settings
from botfluencer.llm.mock import MockGenerator
from botfluencer.platform.mock import MockPlatform
from botfluencer.reporting.health import create_app
from botfluencer.runtime.worker import Worker
from botfluencer.storage.database import Database
from botfluencer.storage.repository import Repository


def build_worker(database: Database) -> Worker:
    repo = Repository(database)
    return Worker(repo, MockPlatform(repo, database.settings.fixture_path), MockGenerator())


async def run_worker(settings: Settings, ticks: int | None = None) -> None:
    database = Database(settings)
    try:
        await build_worker(database).run(ticks)
    finally:
        await database.close()


async def supervise(worker: Worker, server: uvicorn.Server) -> None:
    worker_task = asyncio.create_task(worker.run(), name="worker")
    server_task = asyncio.create_task(server.serve(), name="health-server")
    try:
        done, _ = await asyncio.wait(
            {worker_task, server_task}, return_when=asyncio.FIRST_COMPLETED
        )
        if worker_task in done:
            worker_task.result()  # Propagate failure to the CLI's nonzero exit.
            raise RuntimeError("Worker stopped unexpectedly")
        server_task.result()
        if not server.started:
            raise RuntimeError("Health server did not start")
    finally:
        worker.stop.set()
        server.should_exit = True
        # Give in-flight work a bounded chance to finish on SIGTERM.
        try:
            async with asyncio.timeout(worker.settings.operation_timeout_seconds + 2):
                await asyncio.gather(worker_task, server_task, return_exceptions=True)
        except TimeoutError:
            for task in (worker_task, server_task):
                task.cancel()
            with suppress(asyncio.CancelledError):
                await asyncio.gather(worker_task, server_task, return_exceptions=True)


async def serve(settings: Settings) -> None:
    database = Database(settings)
    try:
        worker = build_worker(database)
        server = uvicorn.Server(
            uvicorn.Config(
                create_app(worker),
                host="0.0.0.0",
                port=settings.port,
                access_log=False,
                log_config=None,
                timeout_graceful_shutdown=int(settings.operation_timeout_seconds) + 2,
            )
        )
        await supervise(worker, server)
    finally:
        await database.close()
