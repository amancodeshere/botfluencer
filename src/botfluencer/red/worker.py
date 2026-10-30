"""Single durable coordinator for a logical network of simulation accounts."""

import asyncio
import hashlib
import time
from contextlib import suppress
from typing import Literal

from botfluencer.competition.errors import InvalidGeneration, RequestDeferred
from botfluencer.domain import ObservationInput
from botfluencer.llm.mock import MockGenerator
from botfluencer.platform.base import PermanentRejection, SafeRetry
from botfluencer.red.config import RedSettings
from botfluencer.red.executor import ReplyExecutor
from botfluencer.red.generator import ContextualGenerator
from botfluencer.red.policy import draft_for, eligible, role_for
from botfluencer.red.store import DISPATCHER, PROMPT_VERSION, STRATEGY_VERSION, RedStore
from botfluencer.red.world import load_world
from botfluencer.runtime.events import event
from botfluencer.runtime.worker import Worker


class RedWorker(Worker):
    def __init__(self, repo: RedStore, executor: ReplyExecutor, config: RedSettings) -> None:
        super().__init__(repo, executor, MockGenerator())
        self.store = repo
        self.executor = executor
        self.config = config
        self._planner: asyncio.Task[None] | None = None

    async def run(self, ticks: int | None = None) -> None:
        async with asyncio.timeout(self.config.identity_timeout_seconds):
            await self.store.initialize()
            self.executor.disabled_accounts = await self.store.disabled_accounts()
            self.executor.quarantine = self.store.quarantine
            self.executor.npc_author_ids = await self.store.npc_ids()
            if not self.executor.npc_author_ids:
                raise ValueError("Reviewed NPC source archive is empty")
            if self.executor.transport.clock() < self.config.ends_at:
                await self.executor.verify_sessions()
                if not self.executor._verified_accounts:
                    raise RuntimeError("No eligible account sessions are available")
                event(
                    "red_sessions_verified",
                    accounts=len(self.executor._verified_accounts),
                    npc_authors=len(self.executor.npc_author_ids),
                )
        try:
            await super().run(ticks)
        finally:
            if self._planner is not None:
                self._planner.cancel()
                with suppress(asyncio.CancelledError):
                    await self._planner

    async def plan_contextual(self) -> None:
        try:
            async with asyncio.timeout(self.settings.operation_timeout_seconds):
                path = self.config.world_context_file
                if path is None or await self.store.is_paused():
                    return
                world = await asyncio.to_thread(load_world, path)
                world.require_fresh(
                    self.executor.transport.clock(), self.config.max_world_age_hours
                )
                generator = ContextualGenerator(
                    self.executor.transport,
                    world,
                    max_world_age_hours=self.config.max_world_age_hours,
                    gif_enabled=self.config.gif_enabled,
                    use_llm=getattr(self.config, "selector_mode", "llm") == "llm",
                )
                account = await self.store.next_account()
                original = await self.store.original_due()
                now = self.executor.transport.clock()
                kind: Literal["post", "reply"]
                if original:
                    slot = int(now.timestamp() // self.config.post_interval_seconds)
                    source = world.sources[slot % len(world.sources)]
                    item = ObservationInput(
                        external_id=f"post:{account}:{slot}",
                        text=source.title,
                        source="news",
                        metadata={"world_digest": world.digest},
                    )
                    await self.store.observe([item])
                    # Look up this exact internal planning observation, not an unrelated oldest row.
                    observation = await self.store.find_observation(item.external_id)
                    kind = "post"
                else:
                    await self.store.observe(
                        await self.store.candidates(self.executor.npc_author_ids)
                    )
                    observation = await self.store.unplanned(
                        DISPATCHER, STRATEGY_VERSION, PROMPT_VERSION
                    )
                    if observation is None:
                        return
                    item = ObservationInput.model_validate(observation.payload)
                    if not eligible(
                        item,
                        self.executor.npc_author_ids,
                        now,
                        allow_threads=self.config.allow_threads,
                    ):
                        await self.store.skip(
                            observation.id, item.external_id, "Ineligible contextual target"
                        )
                        return
                    if item.metadata.get("kind") == "reply":
                        try:
                            target = await self.executor._read_preflight(
                                f"/posts/{item.external_id}", wait_for_pacing=True
                            )
                            await self.executor._reply_coordinates(target, item.external_id)
                        except PermanentRejection:
                            await self.store.skip(
                                observation.id, item.external_id, "Ineligible conversation ancestry"
                            )
                            return
                    kind = "reply"
                    if not await self.store.contact_available(int(item.metadata["author_id"])):
                        await self.store.skip(
                            observation.id, item.external_id, "Team contact already reserved"
                        )
                        return
                if observation is None:
                    return
                if getattr(
                    self.config, "selector_mode", "llm"
                ) == "llm" and not await self.store.reserve_generation(account):
                    return
                order = sorted(self.settings.accounts).index(account)
                draft = await generator.generate(
                    item, account=account, kind=kind, role=role_for(account, order=order)
                )
                if (
                    await self.store.is_paused()
                    or self.executor.transport.clock() >= self.config.ends_at
                ):
                    return
                planned = await self.store.plan_reply(
                    observation.id,
                    int(item.metadata.get("author_id", 0)),
                    account,
                    draft,
                    prompt_version=generator.prompt_version,
                )
                if not planned:
                    await self.store.skip(
                        observation.id,
                        item.external_id,
                        "Team target/contact limit or campaign bound",
                    )
        except RequestDeferred as error:
            event("red_generation_deferred")
            await asyncio.sleep(min(error.seconds, 60))
        except SafeRetry as error:
            event("red_target_deferred")
            await asyncio.sleep(min(error.retry_after_seconds, 60))
        except (InvalidGeneration, ValueError, TimeoutError):
            event("red_generation_skipped")

    async def contextual_tick(self) -> None:
        if self._planner is not None and self._planner.done():
            self._planner.result()
            self._planner = None
        self.state.paused = await self.store.is_paused()
        active = self.config.starts_at <= self.executor.transport.clock() < self.config.ends_at
        if not self.state.paused and active:
            intent = await self.store.claim()
            if intent is not None:
                await self.execute(intent)
            if (
                self._planner is None
                and await self.store.pending_count() < self.config.max_pending_actions
            ):
                self._planner = asyncio.create_task(self.plan_contextual(), name="red-planner")
        await self.store.heartbeat(self.id)
        self.state.heartbeat_monotonic = time.monotonic()
        event(
            "red_worker_heartbeat",
            worker_id=str(self.id),
            paused=self.state.paused,
            campaign_active=active,
        )

    async def tick(self) -> None:
        if getattr(self.config, "use_model", False):
            await self.contextual_tick()
            return
        self.state.paused = await self.store.is_paused()
        now = self.executor.transport.clock()
        active = self.config.starts_at <= now < self.config.ends_at
        if not self.state.paused and active:
            if not await self.store.has_pending():
                await self.store.observe(await self.store.candidates(self.executor.npc_author_ids))
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
