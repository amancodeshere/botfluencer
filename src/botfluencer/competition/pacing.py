import hashlib
import math
from datetime import datetime, timedelta
from typing import cast

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from botfluencer.competition.errors import RequestDeferred
from botfluencer.competition.standards import INITIAL_INTERVAL_SECONDS
from botfluencer.storage.database import Database
from botfluencer.storage.models import RequestBudget


class TeamPacer:
    def __init__(self, database: Database) -> None:
        self.database = database
        code = database.settings.ctn_team_code
        if code is None:
            raise ValueError("A team code is required for a shared competition budget")
        self.scope = "ctn-" + hashlib.sha256(code.get_secret_value().encode()).hexdigest()[:48]

    async def reserve(self, bucket: str) -> None:
        async with self.database.sessions.begin() as session:
            await session.execute(
                insert(RequestBudget)
                .values(
                    scope=self.scope,
                    bucket=bucket,
                    interval_seconds=INITIAL_INTERVAL_SECONDS[bucket],
                )
                .on_conflict_do_nothing()
            )
            budget = await session.scalar(
                select(RequestBudget)
                .where(RequestBudget.scope == self.scope, RequestBudget.bucket == bucket)
                .with_for_update()
            )
            assert budget is not None
            # clock_timestamp() is evaluated after lock acquisition, not at transaction start.
            now = cast(datetime, await session.scalar(select(func.clock_timestamp())))
            wait = (budget.next_allowed_at - now).total_seconds()
            if wait > 0:
                raise RequestDeferred(wait)
            budget.next_allowed_at = now + timedelta(seconds=budget.interval_seconds)

    async def observe(
        self,
        bucket: str,
        *,
        limit: int | None = None,
        reset: float = 0,
        remaining: int | None = None,
        retry_after: float = 0,
    ) -> None:
        async with self.database.sessions.begin() as session:
            budget = await session.scalar(
                select(RequestBudget)
                .where(RequestBudget.scope == self.scope, RequestBudget.bucket == bucket)
                .with_for_update()
            )
            assert budget is not None
            now = cast(datetime, await session.scalar(select(func.clock_timestamp())))
            if limit is not None and limit > 0 and math.isfinite(reset) and reset >= 0:
                # Conservatively respect both the documented per-minute window and refill header.
                budget.interval_seconds = max(60 / limit, reset / limit)
            delay = max(retry_after, budget.interval_seconds)
            if remaining is not None and remaining <= 0 and math.isfinite(reset) and reset > 0:
                delay = max(delay, reset)
            if delay > 0 and math.isfinite(delay):
                budget.next_allowed_at = max(budget.next_allowed_at, now + timedelta(seconds=delay))
