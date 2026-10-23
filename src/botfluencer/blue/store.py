"""Postgres state for collection, model versions, review, and report recovery."""

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import exists, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import aliased

from botfluencer.blue.config import BlueSettings
from botfluencer.storage.database import Database
from botfluencer.storage.models import BlueReport, BlueScore, BlueState, BlueTeamControl, NPCPost


class BlueStore:
    def __init__(self, database: Database, config: BlueSettings, scope: str) -> None:
        self.database, self.config, self.scope = database, config, scope
        self.namespace = config.namespace

    async def initialize(self) -> None:
        async with self.database.sessions.begin() as session:
            await session.execute(
                insert(BlueState).values(namespace=self.namespace).on_conflict_do_nothing()
            )
            await session.execute(
                insert(BlueTeamControl).values(scope=self.scope).on_conflict_do_nothing()
            )

    async def state(self) -> dict[str, Any]:
        async with self.database.sessions() as session:
            state = await session.get(BlueState, self.namespace)
            assert state is not None
            return {
                name: getattr(state, name)
                for name in (
                    "paused",
                    "cursor",
                    "anchor",
                    "next_anchor",
                    "heartbeat_at",
                    "model_version",
                    "last_event",
                )
            }

    async def update_state(self, **values: Any) -> None:
        async with self.database.sessions.begin() as session:
            await session.execute(
                update(BlueState).where(BlueState.namespace == self.namespace).values(**values)
            )

    async def heartbeat(self, event: str, version: str | None = None) -> None:
        await self.update_state(
            heartbeat_at=datetime.now(UTC), last_event=event, model_version=version
        )

    def npc_author(self, author_column: Any) -> Any:
        npc = aliased(NPCPost)
        return exists(
            select(npc.post_id).where(
                npc.namespace == self.namespace,
                npc.author_id == author_column,
                npc.npc_status == "npc",
            )
        )

    async def pending_posts(self, version: str) -> list[dict[str, Any]]:
        scored = exists(
            select(BlueScore.post_id).where(
                BlueScore.namespace == NPCPost.namespace,
                BlueScore.post_id == NPCPost.post_id,
                BlueScore.model_version == version,
            )
        )
        async with self.database.sessions() as session:
            posts = await session.scalars(
                select(NPCPost)
                .where(
                    NPCPost.namespace == self.namespace,
                    NPCPost.post.is_not(None),
                    NPCPost.post != "",
                    NPCPost.npc_status != "npc",
                    ~self.npc_author(NPCPost.author_id),
                    NPCPost.author_id.not_in(self.config.own_author_ids),
                    ~scored,
                    NPCPost.time_stamp
                    >= datetime.now(UTC) - timedelta(hours=self.config.max_age_hours),
                )
                .order_by(NPCPost.time_stamp.desc(), NPCPost.post_id.desc())
                .limit(self.config.score_limit)
            )
            return [
                {
                    "post_id": p.post_id,
                    "author_id": p.author_id,
                    "text": p.post,
                    "time_stamp": p.time_stamp,
                }
                for p in posts
            ]

    async def save_scores(
        self,
        posts: list[dict[str, Any]],
        probabilities: list[float],
        version: str,
        threshold: float,
    ) -> int:
        if len(posts) != len(probabilities):
            raise ValueError("Scorer returned the wrong batch size")
        if not posts:
            return 0
        import math

        if any(not math.isfinite(p) or not 0 <= p <= 1 for p in probabilities):
            raise ValueError("Invalid classifier probability")
        async with self.database.sessions.begin() as session:
            await session.execute(
                insert(BlueScore)
                .values(
                    [
                        dict(
                            namespace=self.namespace,
                            post_id=p["post_id"],
                            author_id=p["author_id"],
                            model_version=version,
                            probability=score,
                            evidence={"policy": "blue-v1", "threshold": threshold},
                        )
                        for p, score in zip(posts, probabilities, strict=True)
                    ]
                )
                .on_conflict_do_nothing()
            )
            hit_rows = (
                await session.execute(
                    select(BlueScore.author_id, func.count())
                    .where(
                        BlueScore.namespace == self.namespace,
                        BlueScore.model_version == version,
                        BlueScore.probability >= threshold,
                        BlueScore.created_at
                        >= datetime.now(UTC) - timedelta(hours=self.config.max_age_hours),
                    )
                    .group_by(BlueScore.author_id)
                )
            ).all()
            hits = {row[0]: row[1] for row in hit_rows}
            candidates = []
            for post, score in zip(posts, probabilities, strict=True):
                count = hits.get(post["author_id"], 0)
                if score >= threshold and count >= self.config.min_author_hits:
                    candidates.append(
                        dict(
                            scope=self.scope,
                            namespace=self.namespace,
                            post_id=post["post_id"],
                            author_id=post["author_id"],
                            model_version=version,
                            probability=score,
                            status="review",
                            detail=(
                                "I think that this content is inauthentic (bot) activity because "
                                f"our versioned competitor/NPC classifier scored it {score:.3f}, "
                                f"with {count} high-scoring posts from this author "
                                "in the observation window. "
                                "This is statistical evidence, not proof of origin."
                            ),
                        )
                    )
            if candidates:
                await session.execute(
                    insert(BlueReport).values(candidates).on_conflict_do_nothing()
                )
        return len(candidates)

    async def approve(self, post_id: int) -> None:
        async with self.database.sessions.begin() as session:
            result = await session.scalar(
                update(BlueReport)
                .where(
                    BlueReport.scope == self.scope,
                    BlueReport.post_id == post_id,
                    BlueReport.status == "review",
                )
                .values(status="approved")
                .returning(BlueReport.post_id)
            )
            if result is None:
                raise ValueError("Only an existing review candidate can be approved")

    async def claim(self, version: str, *, auto: bool) -> dict[str, Any] | None:
        """Caller holds the team report lock across claim and network execution."""
        async with self.database.sessions.begin() as session:
            await session.execute(
                update(BlueReport)
                .where(BlueReport.scope == self.scope, BlueReport.status == "in_flight")
                .values(status="unknown")
            )
            control = await session.get(BlueTeamControl, self.scope, with_for_update=True)
            assert control is not None
            now = datetime.now(UTC)
            if control.blocked_until and control.blocked_until > now:
                return None
            state = await session.get(BlueState, self.namespace)
            if state is None or state.paused:
                return None
            pending = await session.scalar(
                select(func.count())
                .select_from(BlueReport)
                .where(
                    BlueReport.scope == self.scope,
                    BlueReport.status.in_(["accepted", "unknown", "in_flight"]),
                    BlueReport.outcome.is_(None),
                )
            )
            hourly = await session.scalar(
                select(func.count())
                .select_from(BlueReport)
                .where(
                    BlueReport.scope == self.scope,
                    BlueReport.attempted_at >= now - timedelta(hours=1),
                )
            )
            if (pending or 0) >= self.config.max_pending_reports or (
                hourly or 0
            ) >= self.config.max_reports_per_hour:
                return None
            post_exists = exists(
                select(NPCPost.post_id).where(
                    NPCPost.namespace == self.namespace,
                    NPCPost.post_id == BlueReport.post_id,
                    NPCPost.author_id == BlueReport.author_id,
                    NPCPost.time_stamp >= now - timedelta(hours=self.config.max_age_hours),
                )
            )
            report = await session.scalar(
                select(BlueReport)
                .where(
                    BlueReport.scope == self.scope,
                    BlueReport.namespace == self.namespace,
                    BlueReport.model_version == version,
                    BlueReport.status.in_(["approved", "review"] if auto else ["approved"]),
                    BlueReport.author_id.not_in(self.config.own_author_ids),
                    ~self.npc_author(BlueReport.author_id),
                    post_exists,
                )
                .order_by(BlueReport.probability.desc(), BlueReport.created_at)
                .with_for_update()
                .limit(1)
            )
            if report is None:
                return None
            report.status, report.attempted_at = "in_flight", now
            return {
                "post_id": report.post_id,
                "detail": report.detail,
                "correlation_id": str(report.correlation_id),
            }

    async def finish(self, post_id: int, status: str, *, block: bool = False) -> None:
        if status not in {"approved", "accepted", "unknown", "rejected"}:
            raise ValueError("Invalid dispatch result")
        async with self.database.sessions.begin() as session:
            await session.execute(
                update(BlueReport)
                .where(
                    BlueReport.scope == self.scope,
                    BlueReport.post_id == post_id,
                    BlueReport.status == "in_flight",
                )
                .values(status=status)
            )
            if block:
                await session.execute(
                    update(BlueTeamControl)
                    .where(BlueTeamControl.scope == self.scope)
                    .values(blocked_until=datetime.now(UTC) + timedelta(hours=6))
                )

    async def outcome(self, post_id: int, ruling: str) -> None:
        if ruling not in {"genuine", "inauthentic"}:
            raise ValueError("Use a verified moderator ruling")
        async with self.database.sessions.begin() as session:
            report = await session.get(BlueReport, (self.scope, post_id), with_for_update=True)
            if report is None or report.status not in {"accepted", "unknown"} or report.outcome:
                raise ValueError("No unresolved submitted report")
            report.outcome = ruling
            if ruling == "genuine":
                control = await session.get(BlueTeamControl, self.scope, with_for_update=True)
                assert control is not None
                control.blocked_until = datetime.now(UTC) + timedelta(hours=6)

    async def summary(self) -> dict[str, Any]:
        async with self.database.sessions() as session:
            count_rows = (
                await session.execute(
                    select(BlueReport.status, func.count())
                    .where(BlueReport.scope == self.scope)
                    .group_by(BlueReport.status)
                )
            ).all()
            counts = {row[0]: row[1] for row in count_rows}
            control = await session.get(BlueTeamControl, self.scope)
        state = await self.state()
        heartbeat = state["heartbeat_at"]
        return {
            "namespace": self.namespace,
            "paused": state["paused"],
            "model_version": state["model_version"],
            "last_event": state["last_event"],
            "heartbeat_age_seconds": (datetime.now(UTC) - heartbeat).total_seconds()
            if heartbeat
            else None,
            "catchup_pending": state["cursor"] is not None,
            "reports": counts,
            "blocked_until": str(control.blocked_until)
            if control and control.blocked_until
            else None,
        }
