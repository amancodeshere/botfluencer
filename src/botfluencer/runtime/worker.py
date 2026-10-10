import asyncio
import time
from dataclasses import dataclass
from uuid import uuid4

from sqlalchemy import text

from botfluencer.domain import ActionIntent, ActionStatus, ObservationInput
from botfluencer.llm.mock import PROMPT_VERSION, Generator
from botfluencer.platform.base import PermanentRejection, Platform, SafeRetry
from botfluencer.runtime.events import event
from botfluencer.storage.database import LeadershipBusy
from botfluencer.storage.repository import Repository
from botfluencer.strategies.baseline import STRATEGY_VERSION, action_key


@dataclass
class WorkerState:
    initialized: bool = False
    role: str = "starting"
    heartbeat_monotonic: float | None = None
    paused: bool = False


class Worker:
    def __init__(self, repo: Repository, platform: Platform, generator: Generator) -> None:
        self.repo = repo
        self.platform = platform
        self.generator = generator
        self.settings = repo.settings
        self.id = uuid4()
        self.state = WorkerState()
        self.stop = asyncio.Event()

    def retry_delay(self, attempts: int, retry_after: float = 0) -> float:
        return float(
            max(retry_after, min(120, self.settings.retry_base_seconds * 2 ** min(attempts - 1, 8)))
        )

    async def reconcile(self) -> None:
        for intent in await self.repo.unknown():
            result = await self.platform.reconcile(intent)
            if result.state == "applied":
                if result.receipt is None:
                    raise RuntimeError("Applied reconciliation must include a receipt")
                await self.repo.finish(
                    intent, ActionStatus.SUCCEEDED, "reconciled", result.receipt.model_dump()
                )
                event("action_reconciled", action_id=str(intent.id))
            elif result.state == "not_applied":
                await self.repo.finish(
                    intent,
                    ActionStatus.QUEUED,
                    "confirmed_not_applied",
                    {},
                    self.retry_delay(intent.attempts),
                )
            # Unresolved stays unknown. No side-effecting retry is safe.

    async def execute(self, intent: ActionIntent) -> None:
        try:
            async with asyncio.timeout(self.settings.operation_timeout_seconds):
                receipt = await self.platform.execute(intent)
        except SafeRetry as error:
            await self.repo.finish(
                intent,
                ActionStatus.QUEUED,
                "safe_retry",
                {},
                self.retry_delay(intent.attempts, error.retry_after_seconds),
            )
            event("action_retry_scheduled", action_id=str(intent.id), attempt=intent.attempts)
        except PermanentRejection:
            await self.repo.finish(intent, ActionStatus.FAILED, "rejected", {})
            event("action_rejected", action_id=str(intent.id))
        except Exception as error:
            # A timeout or unexpected error does not prove the remote side rejected it.
            await self.repo.finish(
                intent, ActionStatus.UNKNOWN, "ambiguous", {"error_type": type(error).__name__}
            )
            event("action_unknown", action_id=str(intent.id), error_type=type(error).__name__)
        else:
            await self.repo.finish(intent, ActionStatus.SUCCEEDED, "accepted", receipt.model_dump())
            event("action_succeeded", action_id=str(intent.id), attempt=intent.attempts)

    async def tick(self) -> None:
        self.state.paused = await self.repo.is_paused()
        if not self.state.paused:
            await self.repo.observe(await self.platform.observe())
            if self.settings.mode == "replay":
                await self.reconcile()
                for account in self.settings.accounts:
                    observation = await self.repo.unplanned(
                        account, STRATEGY_VERSION, PROMPT_VERSION
                    )
                    if observation is None or not await self.repo.reserve_generation(account):
                        continue
                    draft = await self.generator.generate(
                        ObservationInput.model_validate(observation.payload)
                    )
                    if draft.target_id != observation.external_id:
                        raise RuntimeError("Generated draft must target its source observation")
                    await self.repo.plan(
                        observation.id,
                        account,
                        action_key(
                            self.repo.namespace, account, observation.external_id, PROMPT_VERSION
                        ),
                        STRATEGY_VERSION,
                        PROMPT_VERSION,
                        draft,
                    )
                intent = await self.repo.claim()
                if intent is not None:
                    await self.execute(intent)
        await self.repo.heartbeat(self.id)
        self.state.heartbeat_monotonic = time.monotonic()
        event("worker_heartbeat", worker_id=str(self.id), paused=self.state.paused)

    async def sleep(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self.stop.wait(), seconds)
        except TimeoutError:
            pass

    async def run(self, ticks: int | None = None) -> None:
        try:
            async with asyncio.timeout(self.settings.operation_timeout_seconds):
                await self.repo.initialize()
            self.state.initialized = True
            self.state.role = "standby"
            while not self.stop.is_set():
                # Standby must serve deployment readiness during Railway overlap.
                # Only the owner of the dedicated session lock can execute ticks.
                try:
                    async with self.repo.db.leadership() as connection:
                        self.state.role = "active"
                        owner_pid = await connection.scalar(text("SELECT pg_backend_pid()"))
                        await connection.commit()
                        async with asyncio.timeout(self.settings.operation_timeout_seconds):
                            await self.repo.recover()
                        completed = 0
                        read_failures = 0
                        while not self.stop.is_set():
                            async with asyncio.timeout(self.settings.operation_timeout_seconds):
                                if connection.closed or connection.invalidated:
                                    raise RuntimeError("Worker ownership connection was lost")
                                current_pid = await connection.scalar(
                                    text("SELECT pg_backend_pid()")
                                )
                                if current_pid != owner_pid:
                                    raise RuntimeError("Worker ownership connection changed")
                                await connection.commit()
                                try:
                                    await self.tick()
                                except SafeRetry as error:
                                    read_failures += 1
                                    if read_failures >= self.settings.max_action_attempts:
                                        raise RuntimeError(
                                            "Observation retries exhausted"
                                        ) from None
                                    delay = self.retry_delay(
                                        read_failures, error.retry_after_seconds
                                    )
                                    event("observation_retry_scheduled", attempt=read_failures)
                                else:
                                    read_failures = 0
                                    delay = self.settings.poll_interval_seconds
                                    completed += 1
                            if ticks is not None and completed >= ticks:
                                return
                            await self.sleep(delay)
                except LeadershipBusy:
                    await self.sleep(self.settings.poll_interval_seconds)
        except BaseException:
            self.state.role = "failed"
            raise
        finally:
            if self.state.role != "failed":
                self.state.role = "stopped"
