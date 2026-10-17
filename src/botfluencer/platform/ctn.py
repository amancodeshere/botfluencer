import re

from botfluencer.competition.transport import CompetitionTransport
from botfluencer.domain import ActionIntent, ObservationInput, Receipt, Reconciliation


class CTNPlatform:
    def __init__(self, transport: CompetitionTransport) -> None:
        self.transport = transport

    async def observe(self) -> list[ObservationInput]:
        settings = self.transport.settings
        data = await self.transport.request(
            "social",
            "/posts",
            "feed",
            params={
                "sort": "trending" if settings.ctn_feed == "trending" else "recent",
                "limit": settings.ctn_feed_limit,
            },
        )
        posts = data.get("items", []) if isinstance(data, dict) else data
        if not isinstance(posts, list):
            raise ValueError("Unexpected competition feed response")
        observations = []
        for post in posts:
            if not isinstance(post, dict):
                raise ValueError("Unexpected competition post response")
            body = post.get("body")
            if not isinstance(body, str) or not body.strip():
                continue
            post_id = post.get("post_id")
            if not isinstance(post_id, int) or isinstance(post_id, bool) or post_id < 1:
                raise ValueError("Competition post is missing a valid ID")
            observations.append(
                ObservationInput(
                    external_id=str(post_id),
                    text=body,
                    source="social",
                    metadata={
                        key: post[key]
                        for key in (
                            "post_id",
                            "author_id",
                            "root_post_id",
                            "reply_to_post_id",
                            "kind",
                            "created_at",
                        )
                        if key in post
                    },
                )
            )
        return observations

    async def execute(self, intent: ActionIntent) -> Receipt:
        raise RuntimeError("Competition publishing is not enabled in this baseline")

    async def reconcile(self, intent: ActionIntent) -> Reconciliation:
        # No documented idempotency key or authoritative lookup of an unacknowledged write.
        return Reconciliation(state="unresolved")


class CTNNewsReader:
    def __init__(self, transport: CompetitionTransport) -> None:
        self.transport = transport

    async def sites(self) -> list[dict[str, object]]:
        data = await self.transport.request("news", "/index.json", "news")
        if not isinstance(data, dict) or not isinstance(data.get("sites"), list):
            raise ValueError("Unexpected competition news response")
        return [dict(site) for site in data["sites"] if isinstance(site, dict)]

    async def front_page(self, site: str) -> dict[str, object]:
        if not re.fullmatch(r"[a-z0-9-]+", site):
            raise ValueError("Use a publication sub_url from the news index")
        data = await self.transport.request("news", f"/{site}.json", "news")
        if not isinstance(data, dict):
            raise ValueError("Unexpected publication response")
        return dict(data)
