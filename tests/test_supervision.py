import asyncio
from types import SimpleNamespace

import pytest

from botfluencer.runtime.service import supervise


class ControlledWorker:
    def __init__(self, *, fail: bool = False) -> None:
        self.stop = asyncio.Event()
        self.entered = asyncio.Event()
        self.settings = SimpleNamespace(operation_timeout_seconds=0.2)
        self.fail = fail

    async def run(self) -> None:
        self.entered.set()
        if self.fail:
            raise RuntimeError("synthetic failure")
        await self.stop.wait()


class ControlledServer:
    started = True
    should_exit = False

    async def serve(self) -> None:
        while not self.should_exit:  # noqa: ASYNC110 - emulate Uvicorn shutdown
            await asyncio.sleep(0.01)


async def test_requested_worker_stop_drains_server_without_failure() -> None:
    worker = ControlledWorker()
    server = ControlledServer()
    task = asyncio.create_task(supervise(worker, server))  # type: ignore[arg-type]
    await worker.entered.wait()
    worker.stop.set()
    await asyncio.wait_for(task, timeout=1)
    assert server.should_exit


async def test_fatal_worker_failure_still_propagates() -> None:
    worker = ControlledWorker(fail=True)
    server = ControlledServer()
    with pytest.raises(RuntimeError, match="synthetic failure"):
        await supervise(worker, server)  # type: ignore[arg-type]
    assert server.should_exit
