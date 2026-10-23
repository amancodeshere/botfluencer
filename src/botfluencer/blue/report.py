"""Narrow SDK 0.5.0 post-report contract; no publishing/account creation."""

from typing import Any

from sqlalchemy import text

from botfluencer.blue.config import BlueSettings
from botfluencer.blue.store import BlueStore
from botfluencer.competition.errors import CompetitionHTTPError, RequestDeferred
from botfluencer.competition.standards import SOCIAL_URL, require_open
from botfluencer.competition.transport import CompetitionTransport


class Reporter:
    def __init__(
        self, config: BlueSettings, transport: CompetitionTransport, store: BlueStore
    ) -> None:
        self.config, self.transport, self.store = config, transport, store

    async def dispatch(self, version: str, *, validated: bool) -> str:
        if not self.config.enable_reports:
            return "shadow"
        if not self.transport.settings.ctn_network_enabled:
            raise ValueError("Explicit competition network opt-in required")
        if not self.config.session_token or not self.config.own_author_ids:
            raise ValueError("Reporting requires an account session token and own author IDs")
        if self.config.auto_report and not validated:
            raise ValueError("Automatic reporting requires a real author-disjoint evaluation")
        require_open()
        async with self.store.database.leadership("blue-report:" + self.store.scope) as connection:
            # Reserve before persisting execution intent: no request has happened on deferral.
            await self.transport.pacer.reserve("post_report")
            report = await self.store.claim(version, auto=self.config.auto_report)
            if report is None:
                return "idle"
            post_id = int(report["post_id"])
            try:
                await connection.execute(text("SELECT 1"))
                async with self.transport.client.stream(
                    "POST",
                    SOCIAL_URL + f"/posts/{post_id}/report",
                    headers={
                        "Authorization": "Bearer " + self.config.session_token.get_secret_value(),
                        "Accept": "application/json",
                    },
                    json={"reason": "inauthentic", "detail": report["detail"]},
                ) as response:
                    await response.aread()
                    delay = self.transport.retry_after(
                        response.headers.get("Retry-After"), "post_report"
                    )
                    try:
                        limit = int(response.headers["X-RateLimit-Limit"])
                        remaining = int(response.headers["X-RateLimit-Remaining"])
                        reset = float(response.headers["X-RateLimit-Reset"])
                    except (KeyError, ValueError):
                        limit, remaining, reset = None, None, 0.0
                    await self.transport.pacer.observe(
                        "post_report",
                        limit=limit,
                        remaining=remaining,
                        reset=reset,
                        retry_after=delay if response.status_code == 429 else 0,
                    )
                    if response.status_code == 429:
                        await self.store.finish(post_id, "approved")
                        raise RequestDeferred(delay)
                    if not 200 <= response.status_code < 300:
                        status = "rejected" if 400 <= response.status_code < 500 else "unknown"
                        await self.store.finish(post_id, status, block=response.status_code == 403)
                        raise CompetitionHTTPError(response.status_code)
                    data: Any = response.json()
                    if not isinstance(data, dict):
                        raise ValueError("Unexpected report acknowledgement")
                await connection.execute(text("SELECT 1"))
                await self.store.finish(post_id, "accepted")
                return "accepted"
            except (RequestDeferred, CompetitionHTTPError):
                raise
            except BaseException:
                # A timeout, cancellation, lost leadership, or invalid acknowledgement is ambiguous.
                # If recording fails, startup will conservatively recover in_flight to unknown.
                await self.store.finish(post_id, "unknown")
                raise
