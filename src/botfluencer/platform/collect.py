"""One-shot, read-only archive of every available recent-feed page."""

import asyncio
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

import httpx

from botfluencer.competition.errors import CompetitionHTTPError, RequestDeferred
from botfluencer.competition.pacing import TeamPacer
from botfluencer.competition.standards import require_open
from botfluencer.competition.transport import CompetitionTransport
from botfluencer.config import Settings
from botfluencer.runtime.events import event
from botfluencer.storage.database import Database
from botfluencer.storage.npc_posts import PostArchive, PostRecord


class PostCollector:
    def __init__(
        self,
        transport: CompetitionTransport,
        archive: PostArchive,
        *,
        npc_before: datetime | None = None,
    ) -> None:
        self.transport = transport
        self.archive = archive
        if npc_before is not None and npc_before.utcoffset() is None:
            raise ValueError("NPC cutoff requires an explicit timezone")
        self.npc_before = npc_before

    async def read(self, path: str, bucket: str, params: dict[str, str | int]) -> Any:
        failures = 0
        async with asyncio.timeout(180):
            while True:
                try:
                    return await self.transport.request("social", path, bucket, params=params)
                except RequestDeferred as deferred:
                    await asyncio.sleep(min(30, deferred.seconds))
                except (httpx.TransportError, CompetitionHTTPError) as error:
                    if isinstance(error, CompetitionHTTPError) and error.status not in {
                        502,
                        503,
                        504,
                    }:
                        raise
                    failures += 1
                    if failures >= 3:
                        raise
                    await asyncio.sleep(2**failures)

    async def pages(
        self, path: str, bucket: str, max_pages: int
    ) -> AsyncIterator[list[dict[str, Any]]]:
        cursor: str | None = None
        seen: set[str] = set()
        for _ in range(max_pages):
            params: dict[str, str | int] = {"limit": 200 if path == "/posts" else 50}
            if path == "/posts":
                params["sort"] = "recent"
            if cursor is not None:
                params["cursor"] = cursor
            data = await self.read(path, bucket, params)
            if not isinstance(data, dict) or not isinstance(data.get("items"), list):
                raise ValueError("Expected a cursor-paginated competition response")
            items = data["items"]
            if any(not isinstance(item, dict) for item in items):
                raise ValueError("Unexpected competition item")
            yield items
            cursor = data.get("next_cursor")
            if cursor is None:
                return
            if not isinstance(cursor, str) or not cursor or cursor in seen or not items:
                raise ValueError("Invalid or repeated pagination cursor; archive incomplete")
            seen.add(cursor)
        raise RuntimeError("Page safety limit reached; rerun with a larger limit")

    async def collect(self, max_pages: int) -> dict[str, int | bool | str]:
        if max_pages < 1:
            raise ValueError("Page limit must be positive")
        by_author: dict[int, list[PostRecord]] = {}
        seen = inserted = page_count = 0
        async for page in self.pages("/posts", "feed", max_pages):
            records = [
                PostRecord.model_validate(
                    {
                        "post_id": post.get("post_id"),
                        "author_id": post.get("author_id"),
                        "user_name": None,
                        "name": None,
                        "post": post.get("body"),
                        "time_stamp": post.get("created_at"),
                        "kind": post.get("kind"),
                        "npc_status": "unverified",
                    }
                )
                for post in page
            ]
            if self.npc_before is not None:
                for record in records:
                    if record.time_stamp < self.npc_before:
                        record.npc_status = "npc"
            inserted += await self.archive.save(records)
            for record in records:
                by_author.setdefault(record.author_id, []).append(record)
            seen += len(records)
            page_count += 1
            event("npc_posts_page", pages=page_count, seen=seen, inserted=inserted)

        if self.npc_before is not None:
            await self.archive.label_npc_before(self.npc_before)

        def resolve(author_id: int, profile: dict[str, Any]) -> list[PostRecord]:
            records = by_author.pop(author_id)
            enriched = [
                PostRecord.model_validate(
                    record.model_dump()
                    | {
                        "user_name": profile.get("username"),
                        "name": profile.get("display_name"),
                    }
                )
                for record in records
            ]
            return enriched

        # Only resolve authors who actually posted, after their content is durable.
        if by_author:
            async for page in self.pages("/accounts", "other_read", max_pages):
                enriched = []
                for account in page:
                    account_id = account.get("account_id")
                    if (
                        not isinstance(account_id, int)
                        or isinstance(account_id, bool)
                        or account_id < 1
                    ):
                        raise ValueError("Invalid competition account ID")
                    if account_id in by_author:
                        enriched.extend(resolve(account_id, account))
                await self.archive.save(enriched, fill_names=True)
                event("npc_post_authors", remaining=len(by_author))
                if not by_author:
                    break
        unavailable = 0
        for author_id in list(by_author):
            try:
                profile = await self.read(f"/accounts/{author_id}", "other_read", {})
            except CompetitionHTTPError as error:
                if error.status not in {403, 404}:
                    raise
                unavailable += 1
                continue
            if not isinstance(profile, dict):
                raise ValueError("Unexpected competition account response")
            await self.archive.save(resolve(author_id, profile), fill_names=True)
        return {
            "namespace": self.archive.namespace,
            "pages": page_count,
            "seen": seen,
            "inserted": inserted,
            "complete": True,
            "npc_before": self.npc_before.isoformat() if self.npc_before else "",
            "authors_without_profile": unavailable,
        }


async def collect_posts(
    settings: Settings,
    namespace: str,
    max_pages: int,
    deadline_seconds: float,
    npc_before: datetime | None = None,
) -> dict[str, int | bool | str]:
    if not settings.ctn_network_enabled:
        raise ValueError("Enable network access explicitly for this read-only collection")
    require_open()
    database = Database(settings)
    transport = CompetitionTransport(settings, TeamPacer(database))
    try:
        async with asyncio.timeout(deadline_seconds):
            async with database.leadership("npc-posts:" + namespace):
                return await PostCollector(
                    transport, PostArchive(database, namespace), npc_before=npc_before
                ).collect(max_pages)
    finally:
        await transport.close()
        await database.close()
