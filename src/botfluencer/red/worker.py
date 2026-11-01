"""Single durable coordinator for a logical network of simulation accounts."""

import asyncio
import hashlib
import time
from contextlib import nullcontext
from typing import Literal

import httpx

from botfluencer.competition.errors import CompetitionHTTPError, InvalidGeneration, RequestDeferred
from botfluencer.domain import ActionStatus, ObservationInput
from botfluencer.llm.mock import MockGenerator
from botfluencer.platform.base import PermanentRejection, SafeRetry
from botfluencer.red.config import RedSettings
from botfluencer.red.conversation import Continuation
from botfluencer.red.discovery import ConversationDiscovery, decorate
from botfluencer.red.engagement_planner import EngagementPlanner
from botfluencer.red.executor import ReplyExecutor
from botfluencer.red.generator import ContextualGenerator
from botfluencer.red.policy import draft_for, eligible, role_for
from botfluencer.red.store import DISPATCHER, PROMPT_VERSION, STRATEGY_VERSION, RedStore
from botfluencer.red.world import load_world
from botfluencer.runtime.events import event
from botfluencer.runtime.worker import Worker
from botfluencer.storage.models import Observation


class RedWorker(Worker):
    _prefer_ordinary = False

    def __init__(
        self,
        repo: RedStore,
        executor: ReplyExecutor,
        config: RedSettings,
        *,
        engagement_planner: EngagementPlanner | None = None,
    ) -> None:
        super().__init__(repo, executor, MockGenerator())
        self.store = repo
        self.executor = executor
        self.config = config
        self._planner: asyncio.Task[None] | None = None
        self._prefer_ordinary = False
        self._conversation_targets: frozenset[str] = frozenset()
        self.engagement_planner = engagement_planner
        self._next_engagement_reconciliation = 0.0
        self.discovery = ConversationDiscovery(repo)
        self._next_discovery = 0.0
        self._parallel_tasks: dict[str, asyncio.Task[None]] = {}
        self._parallel_planners: dict[str, asyncio.Task[None]] = {}
        self._parallel_publication_prefers_boost = False
        self._parallel_plan_lock = asyncio.Lock()

    async def run(self, ticks: int | None = None) -> None:
        async with asyncio.timeout(self.config.identity_timeout_seconds):
            await self.store.initialize()
            self.executor.disabled_accounts = await self.store.disabled_accounts()
            self.executor.quarantine = self.store.quarantine
            self.executor.accepted_parent = self.store.accepted_parent
            self.executor.continuation_for_action = self.store.continuation_for_action
            self.executor._own_author_ids = frozenset(
                int(account)
                for account in await self.store.historical_accounts() | set(self.settings.accounts)
            )
            self.executor.npc_author_ids = await self.store.npc_ids()
            if self.engagement_planner is not None:
                self.engagement_planner.npc_ids = self.executor.npc_author_ids
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
            await self._stop_background_work()

    async def before_leadership_release(self) -> None:
        """Stop all side-effecting tasks before the namespace lock is released."""
        await self._stop_background_work()

    async def _stop_background_work(self) -> None:
        tasks = [*self._parallel_tasks.values(), *self._parallel_planners.values()]
        if self._planner is not None:
            tasks.append(self._planner)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._parallel_tasks.clear()
        self._parallel_planners.clear()
        self._planner = None

    async def _conversation_reply(
        self,
    ) -> tuple[Observation, ObservationInput, Continuation, str] | None:
        if not self.config.allow_threads:
            return None
        for archived in await self.store.continuation_candidates(self.executor.npc_author_ids):
            try:
                target = await self.executor._read_preflight(
                    f"/posts/{archived.external_id}", wait_for_pacing=True
                )
                own_parent = await self.executor._has_own_direct_parent(target)
                if own_parent:
                    self._conversation_targets = getattr(
                        self, "_conversation_targets", frozenset()
                    ) | {archived.external_id}
                    if await self.store.accepted_parent(target["reply_to_post_id"]) is None:
                        # A visible own publication may precede its outcome commit.
                        # Keep pending/unknown parents in this lane until proof exists.
                        continue
                elif eligible(
                    archived,
                    self.executor.npc_author_ids,
                    self.executor.transport.clock(),
                    allow_threads=True,
                ):
                    # An ordinary NPC discussion remains available to the ordinary planner.
                    continue
                proof, account = await self.executor._continuation_coordinates(
                    target, archived.external_id
                )
                # Fresh target data governs age, identity and the conversational opening.
                item = ObservationInput(
                    external_id=archived.external_id,
                    text=target["body"],
                    source="social",
                    metadata={
                        "conversation_candidate": True,
                        "post_id": target["post_id"],
                        "author_id": target["author_id"],
                        "kind": target["kind"],
                        "created_at": target["created_at"],
                        "reply_to_post_id": target["reply_to_post_id"],
                        "root_post_id": target["root_post_id"],
                    },
                )
                if not eligible(
                    item,
                    self.executor.npc_author_ids,
                    self.executor.transport.clock(),
                    allow_threads=True,
                    continuation=True,
                ):
                    raise PermanentRejection("Ineligible fresh continuation")
            except (PermanentRejection, ValueError, KeyError, TypeError):
                await self.store.observe([archived])
                observation = await self.store.find_observation(archived.external_id)
                if observation is not None:
                    await self.store.skip(
                        observation.id, archived.external_id, "No verified continuation authority"
                    )
                continue
            self._conversation_targets = getattr(self, "_conversation_targets", frozenset()) | {
                item.external_id
            }
            if not await self.store.continuation_available(int(item.metadata["author_id"]), proof):
                continue
            await self.store.observe([item])
            observation = await self.store.find_observation(item.external_id)
            if observation is not None:
                return observation, item, proof, account
        return None

    async def _contextual_reply(
        self,
    ) -> tuple[Observation, ObservationInput, Continuation | None, str | None] | None:
        if not self._prefer_ordinary:
            conversation = await self._conversation_reply()
            if conversation is not None:
                self._prefer_ordinary = True
                return conversation
        ordinary = await self._ordinary_reply()
        self._prefer_ordinary = False
        if ordinary is not None:
            return ordinary[0], ordinary[1], None, None
        return await self._conversation_reply()

    async def _ordinary_reply(self) -> tuple[Observation, ObservationInput] | None:
        await self.store.observe(await self.store.candidates(self.executor.npc_author_ids))
        excluded: frozenset[str] = getattr(self, "_conversation_targets", frozenset())
        for _ in range(8):
            observation = await self.store.unplanned(
                DISPATCHER, STRATEGY_VERSION, PROMPT_VERSION, exclude_external_ids=excluded
            )
            if observation is None:
                break
            item = ObservationInput.model_validate(observation.payload)
            if not eligible(
                item,
                self.executor.npc_author_ids,
                self.executor.transport.clock(),
                allow_threads=self.config.allow_threads,
            ):
                if item.metadata.get("kind") == "reply":
                    excluded = excluded | {item.external_id}
                    self._conversation_targets = excluded
                else:
                    await self.store.skip(
                        observation.id, item.external_id, "Ineligible contextual target"
                    )
                continue
            target = None
            if self.config.allow_threads and item.metadata.get("kind") == "reply":
                try:
                    target = await self.executor._read_preflight(
                        f"/posts/{item.external_id}", wait_for_pacing=True
                    )
                    if await self.executor._has_own_direct_parent(target):
                        excluded = excluded | {item.external_id}
                        self._conversation_targets = excluded
                        # Own-parent responses must acquire continuation proof first,
                        # even when ordinary planning runs before discovery.
                        continue
                except PermanentRejection:
                    await self.store.skip(
                        observation.id, item.external_id, "Ineligible conversation parent"
                    )
                    continue
            if item.metadata.get("kind") == "reply":
                try:
                    if target is None:
                        target = await self.executor._read_preflight(
                            f"/posts/{item.external_id}", wait_for_pacing=True
                        )
                    await self.executor._reply_coordinates(target, item.external_id)
                except PermanentRejection:
                    await self.store.skip(
                        observation.id, item.external_id, "Ineligible conversation ancestry"
                    )
                    continue
            if not await self.store.contact_available(int(item.metadata["author_id"])):
                await self.store.skip(
                    observation.id, item.external_id, "Team contact already reserved"
                )
                continue
            return observation, item
        return None

    async def plan_contextual(self) -> None:
        planner = getattr(self, "engagement_planner", None)
        if planner is not None:
            try:
                async with asyncio.timeout(self.settings.operation_timeout_seconds):
                    if time.monotonic() >= self._next_engagement_reconciliation:
                        self._next_engagement_reconciliation = time.monotonic() + 120
                        for intent in await planner.store.unknown_engagements():
                            result = await self.executor.reconcile(intent)
                            if result.state == "applied" and result.receipt is not None:
                                await self.store.finish(
                                    intent,
                                    ActionStatus.SUCCEEDED,
                                    "reconciled",
                                    result.receipt.model_dump(),
                                )
                                event("red_engagement_reconciled", action_id=str(intent.id))
                    if await planner.prepare(own_only=True):
                        return
            except (RequestDeferred, SafeRetry, TimeoutError, ValueError):
                # A cancelled database commit may already have reserved an intent.
                # End this planning turn rather than create a second intent.
                event("red_engagement_preparation_deferred")
                return
        if await self._plan_editorial() or planner is None:
            return
        try:
            async with asyncio.timeout(self.settings.operation_timeout_seconds):
                await planner.prepare(own_only=False)
        except (RequestDeferred, SafeRetry, TimeoutError, ValueError):
            event("red_support_preparation_deferred")

    async def _plan_editorial(self) -> bool:
        try:
            async with asyncio.timeout(self.settings.operation_timeout_seconds):
                path = self.config.world_context_file
                if path is None or await self.store.is_paused():
                    return False
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
                    grounded=getattr(self.config, "grounded_content", False),
                    recent_texts=(
                        await self.store.recent_publication_texts()
                        if getattr(self.config, "grounded_content", False)
                        else ()
                    ),
                )
                account = await self.store.next_account()
                original_due, latest_kind = await self.store.planning_preference()
                reply: tuple[Observation, ObservationInput, Continuation | None, str | None] | None
                reply = None
                discovery_seconds = min(30.0, self.settings.operation_timeout_seconds / 3)
                discovery_timeout = asyncio.timeout(discovery_seconds)
                try:
                    async with discovery_timeout:
                        if getattr(self.config, "prioritize_conversations", False):
                            reply = await self._conversation_reply()
                        originals_per_reply = getattr(self.config, "originals_per_reply", 1)
                        if reply is not None:
                            pass
                        elif originals_per_reply > 1:
                            reply_turn = (
                                not original_due
                                or await self.store.original_streak() >= originals_per_reply
                            )
                            if reply_turn:
                                ordinary_first = self._prefer_ordinary
                                # Flip before waiting so a timed-out lane yields the next turn.
                                self._prefer_ordinary = not ordinary_first
                                if ordinary_first:
                                    ordinary = await self._ordinary_reply()
                                    if ordinary is not None:
                                        reply = ordinary[0], ordinary[1], None, None
                                    else:
                                        reply = await self._conversation_reply()
                                else:
                                    reply = await self._conversation_reply()
                                    if reply is None:
                                        ordinary = await self._ordinary_reply()
                                        if ordinary is not None:
                                            reply = ordinary[0], ordinary[1], None, None
                        else:
                            # Preserve the existing one-original/one-reply default.
                            reply = (
                                await self._contextual_reply()
                                if not original_due or latest_kind == "post"
                                else None
                            )
                except TimeoutError:
                    if not discovery_timeout.expired():
                        raise
                    # Discovery is optional; preserve time to plan a due original.
                    # No skip or reservation is created merely because reads timed out.
                    reply = None
                    event("red_reply_discovery_timeout")
                now = self.executor.transport.clock()
                kind: Literal["post", "reply"]
                continuation = None
                if reply is None:
                    if not original_due:
                        return False
                    slot = int(now.timestamp() // self.config.post_interval_seconds)
                    source = world.sources[slot % len(world.sources)]
                    item = ObservationInput(
                        external_id=f"post:{account}:{slot}",
                        text=source.title,
                        source="news",
                        metadata={
                            "world_digest": world.digest,
                            **(
                                {"source_id": source.id}
                                if getattr(self.config, "grounded_content", False)
                                else {}
                            ),
                        },
                    )
                    await self.store.observe([item])
                    # Look up this exact internal planning observation, not an unrelated oldest row.
                    observation = await self.store.find_observation(item.external_id)
                    kind = "post"
                else:
                    observation, item, continuation, parent_account = reply
                    if parent_account is not None:
                        account = parent_account
                    kind = "reply"
                if observation is None:
                    return False
                if (
                    getattr(self.config, "selector_mode", "llm") == "llm"
                    and continuation is None
                    and not getattr(self.config, "grounded_content", False)
                    and not await self.store.reserve_generation(account)
                ):
                    return False
                if continuation is not None and getattr(self.config, "grounded_content", False):
                    parent_text = await self.store.parent_publication_text(
                        continuation.parent_action_id
                    )
                    item = item.model_copy(
                        update={
                            "metadata": dict(item.metadata, conversation_parent_text=parent_text)
                        }
                    )
                order = sorted(self.settings.accounts).index(account)
                draft = await generator.generate(
                    item,
                    account=account,
                    kind=kind,
                    role=role_for(account, order=order),
                    conversation=continuation is not None,
                )
                tags: tuple[str, ...] = ()
                discovery = getattr(self, "discovery", None)
                if (
                    getattr(self.config, "hashtags_enabled", False)
                    and discovery is not None
                    and time.monotonic() < discovery.tags_until
                ):
                    tags = discovery.tags
                username = None
                if getattr(self.config, "mentions_enabled", False) and kind == "reply":
                    try:
                        username = await self.executor.mention_username(
                            int(item.metadata["author_id"])
                        )
                    except PermanentRejection:
                        # Optional mentions never authorise or block a valid reply.
                        username = None
                if tags or username:
                    draft = decorate(draft, tags, username)
                    generator.prompt_version = hashlib.sha256(
                        (
                            generator.prompt_version + ":discovery-v1:" + str(tags) + str(username)
                        ).encode()
                    ).hexdigest()
                if (
                    await self.store.is_paused()
                    or self.executor.transport.clock() >= self.config.ends_at
                ):
                    return False
                guard = (
                    self._parallel_plan_lock
                    if getattr(self.config, "parallel_lanes", False)
                    else nullcontext()
                )
                async with guard:
                    planned = await self.store.plan_reply(
                        observation.id,
                        int(item.metadata.get("author_id", 0)),
                        account,
                        draft,
                        prompt_version=generator.prompt_version,
                        continuation=continuation,
                    )
                if not planned and continuation is None:
                    await self.store.skip(
                        observation.id,
                        item.external_id,
                        "Team target/contact limit or campaign bound",
                    )
                return planned
        except RequestDeferred as error:
            event("red_generation_deferred")
            await asyncio.sleep(min(error.seconds, 60))
        except SafeRetry as error:
            event("red_target_deferred")
            await asyncio.sleep(min(error.retry_after_seconds, 60))
        except (InvalidGeneration, ValueError, TimeoutError):
            event("red_generation_skipped")
        return False

    async def contextual_tick(self) -> None:
        if getattr(self.config, "parallel_lanes", False):
            await self._parallel_contextual_tick()
            return
        if self._planner is not None and self._planner.done():
            self._planner.result()
            self._planner = None
        self.state.paused = await self.store.is_paused()
        active = self.config.starts_at <= self.executor.transport.clock() < self.config.ends_at
        if not self.state.paused and active:
            intent = await self.store.claim()
            if self._planner is not None and self._planner.done():
                self._planner.result()
                self._planner = None
            if self._planner is None:
                pending = await self.store.pending_count()
                if pending < self.config.max_pending_actions:
                    self._planner = asyncio.create_task(self.plan_contextual(), name="red-planner")
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

    async def _poll_discovery(self) -> None:
        try:
            async with asyncio.timeout(self.settings.operation_timeout_seconds):
                count = await self.discovery.poll(
                    self.executor.transport,
                    self.executor.npc_author_ids,
                    hashtags=getattr(self.config, "hashtags_enabled", False),
                )
                event("red_own_responses_observed", count=count)
        except (
            RequestDeferred,
            SafeRetry,
            TimeoutError,
            ValueError,
            CompetitionHTTPError,
            httpx.TransportError,
        ):
            event("red_discovery_deferred")

    async def _parallel_prepare_lane(self, lane: str) -> None:
        planner = self.engagement_planner
        if planner is None:
            if lane == "publication":
                await self._plan_editorial()
            return
        try:
            async with asyncio.timeout(self.settings.operation_timeout_seconds):
                if lane == "engagement":
                    if time.monotonic() >= self._next_engagement_reconciliation:
                        self._next_engagement_reconciliation = time.monotonic() + 120
                        for intent in await planner.store.unknown_engagements():
                            result = await self.executor.reconcile(intent)
                            if result.state == "applied" and result.receipt is not None:
                                await self.store.finish(
                                    intent,
                                    ActionStatus.SUCCEEDED,
                                    "reconciled",
                                    result.receipt.model_dump(),
                                )
                                event("red_engagement_reconciled", action_id=str(intent.id))
                    capacity = max(
                        0,
                        self.config.max_pending_actions
                        - await self.store.pending_count(kinds=("like",)),
                    )
                    for _ in range(min(getattr(self.config, "like_concurrency", 1), capacity)):
                        async with self._parallel_plan_lock:
                            reserved = await planner.prepare(own_only=True, kinds=("like",))
                        if not reserved:
                            break
                else:
                    self._parallel_publication_prefers_boost = (
                        not self._parallel_publication_prefers_boost
                    )
                    if self._parallel_publication_prefers_boost:
                        async with self._parallel_plan_lock:
                            await planner.prepare(own_only=True, kinds=("repost", "team_reply"))
                        await self._plan_editorial()
                    else:
                        await self._plan_editorial()
                        async with self._parallel_plan_lock:
                            await planner.prepare(own_only=True, kinds=("repost", "team_reply"))
        except (RequestDeferred, SafeRetry, TimeoutError, ValueError):
            event("red_parallel_planning_deferred", lane=lane)

    def _check_parallel_tasks(self, tasks: dict[str, asyncio.Task[None]]) -> None:
        failure: BaseException | None = None
        for lane, task in tuple(tasks.items()):
            if task.done():
                error = task.exception()
                del tasks[lane]
                if error is not None and failure is None:
                    failure = error
        if failure is not None:
            state = getattr(getattr(failure, "orig", None), "sqlstate", None)
            event(
                "red_background_failed",
                error_type=type(failure).__name__,
                sqlstate=state
                if isinstance(state, str) and len(state) == 5 and state.isalnum()
                else None,
            )
            siblings = [*self._parallel_tasks.values(), *self._parallel_planners.values()]
            for task in siblings:
                task.cancel()
                task.add_done_callback(
                    lambda completed: None if completed.cancelled() else completed.exception()
                )
            raise failure

    async def _dispatch_lane(self, lane: str, kinds: tuple[str, ...]) -> None:
        intent = await self.store.claim(kinds=kinds)
        if intent is not None:
            await self.execute(intent)

    async def _parallel_contextual_tick(self) -> None:
        self._check_parallel_tasks(self._parallel_tasks)
        self._check_parallel_tasks(self._parallel_planners)
        self.state.paused = await self.store.is_paused()
        active = self.config.starts_at <= self.executor.transport.clock() < self.config.ends_at
        if not self.state.paused and active:
            for lane, kinds in (
                ("engagement", ("like",)),
                ("publication", ("post", "reply", "repost", "team_reply")),
            ):
                pending = await self.store.pending_count(kinds=kinds)
                if (
                    lane not in self._parallel_planners
                    and pending < self.config.max_pending_actions
                ):
                    self._parallel_planners[lane] = asyncio.create_task(
                        self._parallel_prepare_lane(lane), name=f"red-{lane}-planner"
                    )
                concurrency = (
                    min(getattr(self.config, "like_concurrency", 1), 8)
                    if lane == "engagement"
                    else 1
                )
                for index in range(concurrency):
                    slot = f"{lane}:{index}"
                    if slot not in self._parallel_tasks:
                        self._parallel_tasks[slot] = asyncio.create_task(
                            self._dispatch_lane(slot, kinds), name=f"red-{slot}-dispatcher"
                        )
        if (
            not self.state.paused
            and active
            and (
                getattr(self.config, "hashtags_enabled", False)
                or getattr(self.config, "prioritize_conversations", False)
            )
            and "discovery" not in self._parallel_planners
            and time.monotonic() >= self._next_discovery
        ):
            self._next_discovery = time.monotonic() + 60
            self._parallel_planners["discovery"] = asyncio.create_task(
                self._poll_discovery(), name="red-own-responses-discovery"
            )
        await self.store.heartbeat(self.id)
        self.state.heartbeat_monotonic = time.monotonic()
        event(
            "red_worker_heartbeat",
            worker_id=str(self.id),
            paused=self.state.paused,
            campaign_active=active,
        )

    async def tick(self) -> None:
        if (
            getattr(self.config, "use_model", False)
            or getattr(self, "engagement_planner", None)
            or getattr(self.config, "parallel_lanes", False)
        ):
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
