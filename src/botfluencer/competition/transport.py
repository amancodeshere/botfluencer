"""Cancelable async transport matching the pinned official SDK's wire contracts.

The official SDK is synchronous. HTTPX keeps calls cancelable inside this async
runtime instead of leaving a background thread running after an operation deadline.
Only anonymous GETs and competition model requests are exposed in this baseline.
"""

import math
from collections.abc import Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, Literal

import httpx

from botfluencer.competition.errors import CompetitionHTTPError, RequestDeferred
from botfluencer.competition.pacing import TeamPacer
from botfluencer.competition.standards import (
    INITIAL_INTERVAL_SECONDS,
    LLM_URL,
    NEWS_URL,
    SOCIAL_URL,
    require_open,
)
from botfluencer.config import Settings


class CompetitionTransport:
    def __init__(
        self,
        settings: Settings,
        pacer: TeamPacer,
        *,
        http_transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.settings = settings
        self.pacer = pacer
        self.clock = clock
        self.client = httpx.AsyncClient(
            timeout=settings.operation_timeout_seconds,
            follow_redirects=False,
            trust_env=False,
            transport=http_transport,
        )

    async def request(
        self,
        service: Literal["social", "news", "llm"],
        path: str,
        bucket: str,
        *,
        params: dict[str, str | int] | None = None,
        payload: dict[str, Any] | None = None,
    ) -> Any:
        if not self.settings.ctn_network_enabled:
            raise RuntimeError("Competition network access is disabled")
        require_open(self.clock())
        # Restrict credential-bearing requests to the one documented model endpoint.
        if service == "llm" and path != "/chat/completions":
            raise ValueError("Unsupported model route")
        if not path.startswith("/") or path.startswith("//") or ".." in path:
            raise ValueError("Invalid competition route")
        if service != "llm" and payload is not None:
            raise ValueError("This baseline exposes read-only platform/news access")
        await self.pacer.reserve(bucket)
        base = {"social": SOCIAL_URL, "news": NEWS_URL, "llm": LLM_URL}[service]
        headers = {"Accept": "application/json"}
        if service == "llm":
            code = self.settings.ctn_team_code
            if code is None:
                raise ValueError("Competition model requires the team code")
            headers["Authorization"] = "Bearer " + code.get_secret_value()
        response = await self.client.request(
            "POST" if service == "llm" else "GET",
            base + path,
            params=params,
            json=payload,
            headers=headers,
        )
        retry_after = self.retry_after(response.headers.get("Retry-After"), bucket)
        try:
            limit = int(response.headers["X-RateLimit-Limit"])
            remaining = int(response.headers["X-RateLimit-Remaining"])
            reset = float(response.headers["X-RateLimit-Reset"])
        except (KeyError, ValueError):
            limit, remaining, reset = None, None, 0.0
        await self.pacer.observe(
            bucket,
            limit=limit,
            remaining=remaining,
            reset=reset,
            retry_after=retry_after if response.status_code == 429 else 0,
        )
        if response.status_code == 429:
            raise RequestDeferred(retry_after)
        if not 200 <= response.status_code < 300:
            raise CompetitionHTTPError(response.status_code)
        return response.json()

    def retry_after(self, header: str | None, bucket: str) -> float:
        if header:
            try:
                seconds = float(header)
            except ValueError:
                try:
                    seconds = (parsedate_to_datetime(header) - self.clock()).total_seconds()
                except (ValueError, TypeError, OverflowError):
                    seconds = INITIAL_INTERVAL_SECONDS[bucket]
            if math.isfinite(seconds):
                return max(1, seconds)
        return INITIAL_INTERVAL_SECONDS[bucket]

    async def close(self) -> None:
        await self.client.aclose()
