import hashlib
import math

from sqlalchemy import text

from botfluencer.competition.errors import RequestDeferred
from botfluencer.competition.standards import INITIAL_INTERVAL_SECONDS
from botfluencer.storage.database import Database


class TeamPacer:
    def __init__(self, database: Database) -> None:
        self.database = database
        code = database.settings.ctn_team_code
        if code is None:
            raise ValueError("A team code is required for a shared competition budget")
        self.scope = "ctn-" + hashlib.sha256(code.get_secret_value().encode()).hexdigest()[:48]

    async def reserve(self, bucket: str) -> None:
        async with self.database.sessions.begin() as session:
            parameters = {
                "scope": self.scope,
                "bucket": bucket,
                "initial_interval": INITIAL_INTERVAL_SECONDS[bucket],
            }
            granted = await session.scalar(
                text("""
                    INSERT INTO request_budgets
                        (scope, bucket, interval_seconds, next_allowed_at)
                    VALUES
                        (:scope, :bucket, :initial_interval,
                         clock_timestamp() + :initial_interval * INTERVAL '1 second')
                    ON CONFLICT (scope, bucket) DO UPDATE
                    SET next_allowed_at = clock_timestamp()
                        + request_budgets.interval_seconds * INTERVAL '1 second'
                    WHERE request_budgets.next_allowed_at <= clock_timestamp()
                    RETURNING interval_seconds
                """),
                parameters,
            )
            if granted is None:
                # A denied conflicting row remains locked in this transaction.
                # Evaluate the wait after that lock, using the current database clock.
                wait = await session.scalar(
                    text("""
                        SELECT GREATEST(
                            0, EXTRACT(EPOCH FROM (next_allowed_at - clock_timestamp()))
                        )
                        FROM request_budgets WHERE scope = :scope AND bucket = :bucket
                    """),
                    parameters,
                )
                assert wait is not None
                raise RequestDeferred(float(wait))

    async def observe(
        self,
        bucket: str,
        *,
        limit: int | None = None,
        reset: float = 0,
        remaining: int | None = None,
        retry_after: float = 0,
    ) -> None:
        interval: float | None = None
        if limit is not None and limit > 0 and math.isfinite(reset) and reset >= 0:
            # Respect both the documented per-minute window and refill header.
            candidate = max(60 / limit, reset / limit)
            if math.isfinite(candidate) and candidate > 0:
                interval = candidate
        retry_delay = max(0, retry_after) if math.isfinite(retry_after) else 0
        depleted_delay = (
            reset
            if remaining is not None and remaining <= 0 and math.isfinite(reset) and reset > 0
            else 0
        )
        async with self.database.sessions.begin() as session:
            observed = await session.scalar(
                text("""
                    UPDATE request_budgets
                    SET interval_seconds = COALESCE(:new_interval, interval_seconds),
                        next_allowed_at = GREATEST(
                            next_allowed_at,
                            clock_timestamp() + GREATEST(
                                :retry_delay,
                                :depleted_delay,
                                COALESCE(:new_interval, interval_seconds)
                            ) * INTERVAL '1 second'
                        )
                    WHERE scope = :scope AND bucket = :bucket
                    RETURNING bucket
                """),
                {
                    "scope": self.scope,
                    "bucket": bucket,
                    "new_interval": interval,
                    "retry_delay": retry_delay,
                    "depleted_delay": depleted_delay,
                },
            )
            assert observed is not None
