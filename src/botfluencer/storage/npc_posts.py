"""Validated, append-only public-post snapshots, separate from action state."""

import re
from datetime import UTC, datetime
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import func
from sqlalchemy.dialects.postgresql import insert

from botfluencer.storage.database import Database
from botfluencer.storage.models import NPCPost


class PostRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")
    post_id: int = Field(gt=0, strict=True)
    author_id: int = Field(gt=0, strict=True)
    user_name: str | None
    name: str | None
    post: str | None
    time_stamp: AwareDatetime
    kind: str = Field(min_length=1, max_length=32)
    npc_status: Literal["unverified", "npc", "player"] = "unverified"

    @field_validator("time_stamp")
    @classmethod
    def utc_timestamp(cls, value: datetime) -> datetime:
        return value.astimezone(UTC)


class PostArchive:
    def __init__(self, database: Database, namespace: str) -> None:
        if not re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", namespace):
            raise ValueError("Invalid archive namespace")
        self.database = database
        self.namespace = namespace

    async def save(self, posts: list[PostRecord], *, fill_names: bool = False) -> int:
        if not posts:
            return 0
        async with self.database.sessions.begin() as session:
            if fill_names:
                posts = list({post.post_id: post for post in posts}.values())
            statement = insert(NPCPost).values(
                [dict(namespace=self.namespace, **post.model_dump()) for post in posts]
            )
            if fill_names:
                statement = statement.on_conflict_do_update(
                    index_elements=["namespace", "post_id"],
                    set_={
                        "user_name": func.coalesce(NPCPost.user_name, statement.excluded.user_name),
                        "name": func.coalesce(NPCPost.name, statement.excluded.name),
                    },
                )
            else:
                statement = statement.on_conflict_do_nothing(
                    index_elements=["namespace", "post_id"]
                )
            ids = await session.scalars(statement.returning(NPCPost.post_id))
            return len(list(ids))
