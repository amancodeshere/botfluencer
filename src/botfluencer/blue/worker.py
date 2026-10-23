"""Resident feed collector and GPU scorer, with bounded cursor catch-up."""

import asyncio
import gc
import json
import time
from typing import Any

from sqlalchemy import text

from botfluencer.blue.config import BlueSettings
from botfluencer.blue.model import Detector
from botfluencer.blue.report import Reporter
from botfluencer.blue.store import BlueStore
from botfluencer.competition.errors import RequestDeferred
from botfluencer.competition.pacing import TeamPacer
from botfluencer.competition.standards import require_open
from botfluencer.competition.transport import CompetitionTransport
from botfluencer.config import Settings
from botfluencer.platform.collect import PostCollector
from botfluencer.storage.database import Database
from botfluencer.storage.npc_posts import PostArchive, PostRecord


async def collect_incremental(transport: CompetitionTransport, store: BlueStore) -> int:
    state = await store.state()
    archive = PostArchive(store.database, store.namespace)
    collector = PostCollector(transport, archive)
    cursor, anchor, next_anchor = state["cursor"], state["anchor"], state["next_anchor"]
    inserted = 0
    seen: set[str] = set()
    for _ in range(store.config.max_pages):
        params: dict[str, str | int] = {"sort": "recent", "limit": 200}
        if cursor:
            params["cursor"] = cursor
        data = await collector.read("/posts", "feed", params)
        if not isinstance(data, dict) or not isinstance(data.get("items"), list):
            raise ValueError("Invalid feed response")
        records = [
            PostRecord.model_validate(
                dict(
                    post_id=p.get("post_id"),
                    author_id=p.get("author_id"),
                    user_name=None,
                    name=None,
                    post=p.get("body"),
                    time_stamp=p.get("created_at"),
                    kind=p.get("kind"),
                )
            )
            for p in data["items"]
        ]
        if not cursor and records:
            next_anchor = records[0].post_id
        inserted += await archive.save(records)
        new_cursor = data.get("next_cursor")
        done = new_cursor is None or any(p.post_id == anchor for p in records)
        if done:
            await store.update_state(cursor=None, anchor=next_anchor or anchor, next_anchor=None)
            break
        if (
            not isinstance(new_cursor, str)
            or not new_cursor
            or new_cursor == cursor
            or new_cursor in seen
            or not records
        ):
            raise ValueError("Invalid cursor; refusing to claim feed completion")
        seen.add(new_cursor)
        cursor = new_cursor
        # Posts commit before cursor: a crash can replay a page but cannot skip its data.
        await store.update_state(cursor=cursor, next_anchor=next_anchor)
    return inserted


async def watch(settings: Settings, config: BlueSettings, *, once: bool = False) -> None:
    if not settings.ctn_network_enabled:
        raise ValueError("Blue feed collection requires explicit network opt-in")
    require_open()
    database = Database(settings)
    transport = CompetitionTransport(settings, TeamPacer(database))
    store = BlueStore(database, config, transport.pacer.scope)
    detector: Detector | None = None
    try:
        await store.initialize()
        async with database.leadership("blue-worker:" + config.namespace) as owner:
            while True:
                started = time.monotonic()
                await owner.execute(text("SELECT 1"))
                await owner.commit()
                state = await store.state()
                # Free CUDA for training when paused, but retain read-only feed coverage.
                async with asyncio.timeout(180):
                    await collect_incremental(transport, store)
                if state["paused"]:
                    if detector is not None:
                        torch = detector.torch
                        detector = None
                        gc.collect()
                        torch.cuda.empty_cache()
                    await store.heartbeat("paused")
                else:
                    manifest_path = config.artifact / "manifest.json"
                    if manifest_path.exists():
                        version = json.loads(manifest_path.read_text())["version"]
                        if detector is None or detector.manifest.version != version:
                            # One resident CUDA model; no external model execution or remote code.
                            detector = Detector(config.artifact, config.batch_size)
                        posts = await store.pending_posts(detector.manifest.version)
                        probabilities = detector.predict([p["text"] for p in posts])
                        await owner.execute(text("SELECT 1"))
                        await owner.commit()
                        await store.save_scores(
                            posts,
                            probabilities,
                            detector.manifest.version,
                            detector.manifest.threshold,
                        )
                        try:
                            async with asyncio.timeout(settings.operation_timeout_seconds + 10):
                                await Reporter(config, transport, store).dispatch(
                                    detector.manifest.version,
                                    validated=detector.automatic_reporting_validated(),
                                )
                        except RequestDeferred:
                            pass
                        await store.heartbeat("scored", detector.manifest.version)
                    else:
                        await store.heartbeat("waiting_for_model")
                if once:
                    print(json.dumps(await store.summary()))
                    return
                await asyncio.sleep(
                    max(0.1, config.interval_seconds - (time.monotonic() - started))
                )
    finally:
        await transport.close()
        await database.close()


async def operator(
    settings: Settings,
    config: BlueSettings,
    operation: str,
    post_id: int | None = None,
    ruling: str | None = None,
) -> dict[str, Any]:
    database = Database(settings)
    try:
        store = BlueStore(database, config, TeamPacer(database).scope)
        await store.initialize()
        if operation in {"pause", "resume"}:
            await store.update_state(paused=operation == "pause")
        elif operation == "approve" and post_id:
            await store.approve(post_id)
        elif operation == "outcome" and post_id and ruling:
            await store.outcome(post_id, ruling)
        return await store.summary()
    finally:
        await database.close()
