import asyncio
import signal
from collections.abc import Iterator
from contextlib import contextmanager, suppress

import uvicorn

from botfluencer.competition.pacing import TeamPacer
from botfluencer.competition.transport import CompetitionTransport
from botfluencer.config import Settings
from botfluencer.llm.mock import MockGenerator
from botfluencer.platform.ctn import CTNPlatform
from botfluencer.platform.mock import MockPlatform
from botfluencer.reporting.health import create_app
from botfluencer.runtime.worker import Worker
from botfluencer.storage.database import Database
from botfluencer.storage.repository import Repository


class RuntimeServer(uvicorn.Server):
    @contextmanager
    def capture_signals(self) -> Iterator[None]:
        # The supervisor owns signals so Uvicorn cannot re-raise SIGTERM before
        # the worker drains and releases its database ownership connection.
        yield


def build_worker(database: Database) -> Worker:
    repo = Repository(database)
    if database.settings.platform_backend == "ctn":
        transport = CompetitionTransport(database.settings, TeamPacer(database))
        return Worker(repo, CTNPlatform(transport), MockGenerator())
    return Worker(repo, MockPlatform(repo, database.settings.fixture_path), MockGenerator())


async def close_worker(worker: Worker) -> None:
    if isinstance(worker.platform, CTNPlatform):
        await worker.platform.transport.close()


async def run_worker(settings: Settings, ticks: int | None = None) -> None:
    database = Database(settings)
    worker = None
    try:
        worker = build_worker(database)
        await worker.run(ticks)
    finally:
        if worker is not None:
            await close_worker(worker)
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
            if not worker.stop.is_set():
                raise RuntimeError("Worker stopped unexpectedly")
        if server_task in done:
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
    worker = None
    try:
        worker = build_worker(database)
        server = RuntimeServer(
            uvicorn.Config(
                create_app(worker),
                host="0.0.0.0",
                port=settings.port,
                access_log=False,
                log_config=None,
                timeout_graceful_shutdown=int(settings.operation_timeout_seconds) + 2,
            )
        )
        loop = asyncio.get_running_loop()

        def stop() -> None:
            assert worker is not None
            worker.stop.set()
            server.should_exit = True

        signals = (signal.SIGTERM, signal.SIGINT)
        for item in signals:
            loop.add_signal_handler(item, stop)
        try:
            await supervise(worker, server)
        finally:
            for item in signals:
                loop.remove_signal_handler(item)
    finally:
        if worker is not None:
            await close_worker(worker)
        await database.close()
