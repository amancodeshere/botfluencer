"""Bounded discovery of public tags and NPC responses to acknowledged own posts."""

import asyncio
import re
import time
from typing import Any

from sqlalchemy import select

from botfluencer.competition.errors import RequestDeferred
from botfluencer.domain import Draft, ObservationInput
from botfluencer.red.store import RedStore
from botfluencer.storage.models import Action, Outcome
from botfluencer.storage.npc_posts import PostArchive, PostRecord

# Reviewed public topic vocabulary, not arbitrary text obtained from the platform.
TOPIC_TAGS = frozenset(
    {
        "h2d2",
        "referendum",
        "voteyes",
        "kingstonia",
        "privacy",
        "exemptions",
        "publichealth",
        "vaccination",
        "vaccines",
        "publicspaces",
        "oversight",
    }
)
HANDLE = re.compile(r"[A-Za-z0-9_]{1,64}")


def select_tags(data: Any) -> tuple[str, ...]:
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise ValueError("Invalid popular-tag response")
    selected = []
    for item in data["items"][:15]:
        name = item.get("name") if isinstance(item, dict) else None
        if isinstance(name, str) and name.casefold() in TOPIC_TAGS:
            token = "#" + name.casefold()
            if token not in selected:
                selected.append(token)
    return tuple(selected[:2])


class ConversationDiscovery:
    def __init__(self, store: RedStore) -> None:
        self.store = store
        self.tags: tuple[str, ...] = ()
        self.tags_until = 0.0
        self.cursor = 0

    async def read(self, transport: Any, path: str, **kwargs: Any) -> Any:
        while True:
            try:
                return await transport.request("social", path, "other_read", **kwargs)
            except RequestDeferred as error:
                await asyncio.sleep(min(30, error.seconds))

    async def poll(self, transport: Any, npc_ids: frozenset[int], *, hashtags: bool) -> int:
        if hashtags:
            data = await self.read(transport, "/tags/popular")
            self.tags = select_tags(data)
            self.tags_until = time.monotonic() + 300
        async with self.store.db.sessions() as session:
            receipts = await session.scalars(
                select(Outcome.payload["external_id"].as_string())
                .join(Action, Action.id == Outcome.action_id)
                .where(
                    Action.namespace == self.store.namespace,
                    Action.status == "succeeded",
                    Action.payload["kind"].as_string().in_(("post", "reply")),
                    Outcome.kind == "accepted",
                    Action.account.in_(self.store.settings.accounts),
                )
                .order_by(Outcome.created_at.desc())
                .limit(16)
            )
            ids = list(dict.fromkeys(value for value in receipts if value and value.isdecimal()))
        if not ids:
            return 0
        count = 0
        for _ in range(min(2, len(ids))):
            post_id = ids[self.cursor % len(ids)]
            self.cursor += 1
            # Receipt lookup is revalidated by execution; this read only supplies candidates.
            data = await self.read(transport, f"/posts/{post_id}/replies", params={"limit": 50})
            if not isinstance(data, dict) or not isinstance(data.get("items"), list):
                raise ValueError("Invalid own-thread replies response")
            records = []
            observations = []
            for item in data["items"][:50]:
                if (
                    not isinstance(item, dict)
                    or item.get("author_id") not in npc_ids
                    or item.get("kind") != "reply"
                    or item.get("reply_to_post_id") != int(post_id)
                ):
                    continue
                record = PostRecord.model_validate(
                    {
                        "post_id": item.get("post_id"),
                        "author_id": item.get("author_id"),
                        "user_name": None,
                        "name": None,
                        "post": item.get("body"),
                        "time_stamp": item.get("created_at"),
                        "kind": "reply",
                    }
                )
                records.append(record)
                observations.append(
                    ObservationInput(
                        external_id=str(record.post_id),
                        text=record.post or "",
                        source="social",
                        metadata={
                            "author_id": record.author_id,
                            "kind": "reply",
                            "created_at": record.time_stamp.isoformat(),
                            "own_parent_candidate": True,
                        },
                    )
                )
            await PostArchive(self.store.db, self.store.config.archive_namespace).save(records)
            await self.store.observe(observations)
            # Separate diagnostic keys preserve first-observed payloads and recover after restart.
            await self.store.observe(
                [
                    ObservationInput(
                        external_id="own-response:" + item.external_id,
                        text="Own-response discovery hint",
                        source="fixture",
                        metadata={"own_parent_candidate": True},
                    )
                    for item in observations
                ]
            )
            count += len(observations)
        return count


def decorate(draft: Draft, tags: tuple[str, ...], username: str | None = None) -> Draft:
    body = draft.text
    if username is not None:
        if draft.kind != "reply" or HANDLE.fullmatch(username) is None or "@" in body:
            raise ValueError("Invalid reply mention")
        if len(body) + len(username) + 2 <= 500:
            body = "@" + username + " " + body
    for tag in tags[:2]:
        if tag.startswith("#") and tag[1:] in TOPIC_TAGS and len(body) + len(tag) + 1 <= 500:
            body += " " + tag
    return Draft(kind=draft.kind, target_id=draft.target_id, text=body)
