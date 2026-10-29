"""Single durable coordinator for a logical network of simulation accounts."""

import asyncio
import hashlib
import time

from botfluencer.competition.errors import RequestDeferred
from botfluencer.domain import ObservationInput
from botfluencer.llm.mock import MockGenerator
from botfluencer.red.config import RedSettings
from botfluencer.red.executor import ReplyExecutor
from botfluencer.red.policy import draft_for, eligible
from botfluencer.red.store import DISPATCHER, PROMPT_VERSION, STRATEGY_VERSION, RedStore
from botfluencer.runtime.events import event
from botfluencer.runtime.worker import Worker


class RedWorker(Worker):
    def __init__(self, repo: RedStore, executor: ReplyExecutor, config: RedSettings) -> None:
        super().__init__(repo, executor, MockGenerator())
        self.store = repo
        self.executor = executor
        self.config = config

    async def run(self, ticks: int | None = None) -> None:
        async with asyncio.timeout(self.settings.operation_timeout_seconds):
            await self.store.initialize()
            self.executor.npc_author_ids = await self.store.npc_ids()
            if not self.executor.npc_author_ids:
                raise ValueError("Reviewed NPC source archive is empty")
            if self.executor.transport.clock() < self.config.ends_at:
                await self.executor.verify_sessions()
        await super().run(ticks)

    async def tick(self) -> None:
        self.state.paused = await self.store.is_paused()
        now = self.executor.transport.clock()
        active = self.config.starts_at <= now < self.config.ends_at
        if not self.state.paused and active:
            if time.monotonic() >= self.next_observation_at:
                try:
                    await self.store.observe(await self.executor.observe())
                except RequestDeferred as error:
                    self.next_observation_at = time.monotonic() + error.seconds
            for _ in range(8):
                observation = await self.store.unplanned(
                    DISPATCHER, STRATEGY_VERSION, PROMPT_VERSION
                )
                if observation is None:
                    break
                source = ObservationInput.model_validate(observation.payload)
                if not eligible(source, self.executor.npc_author_ids, now):
                    await self.store.skip(
                        observation.id, source.external_id, "Ineligible NPC/top-level/topic/age"
                    )
                    continue
                accounts = sorted(self.executor.sessions)
                index = int(hashlib.sha256(source.external_id.encode()).hexdigest(), 16) % len(
                    accounts
                )
                planned = await self.store.plan_reply(
                    observation.id,
                    int(source.metadata["author_id"]),
                    accounts[index],
                    draft_for(source),
                )
                if not planned:
                    await self.store.skip(
                        observation.id,
                        source.external_id,
                        "Team target/contact limit or campaign bound",
                    )
                    continue
                break
            intent = await self.store.claim()
            if intent is not None:
                await self.execute(intent)
        await self.store.heartbeat(self.id)
        self.state.heartbeat_monotonic = time.monotonic()
        event(
            "red_worker_heartbeat",
            worker_id=str(self.id),
            paused=self.state.paused,
            campaign_active=active,
        )
