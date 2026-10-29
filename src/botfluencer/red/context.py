"""Refresh a private, bounded snapshot of public organiser front-page summaries."""

import asyncio
import hashlib
import os
import re
import tempfile
from collections.abc import Awaitable, Callable
from datetime import datetime
from functools import partial
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from botfluencer.competition.errors import RequestDeferred
from botfluencer.competition.pacing import TeamPacer
from botfluencer.competition.standards import NEWS_URL, require_open
from botfluencer.competition.transport import CompetitionTransport
from botfluencer.config import Settings
from botfluencer.platform.ctn import CTNNewsReader
from botfluencer.red.world import WorldContext, WorldSource
from botfluencer.storage.database import Database

_TOPIC = re.compile(r"h2d2|vaccin|referendum|mandate|poll", re.IGNORECASE)


async def _read[T](request: Callable[[], Awaitable[T]]) -> T:
    while True:
        try:
            return await request()
        except RequestDeferred as error:
            # These are read-only calls; the shared durable news bucket controls
            # both pre-call deferral and a server's explicit rate-limit response.
            await asyncio.sleep(min(30, error.seconds))


def _private_path(output: Path) -> Path:
    path = output.expanduser().absolute()
    if path.is_symlink():
        raise ValueError("World context output cannot be a symlink")
    path = path.resolve()
    if path.is_relative_to(Path(__file__).resolve().parents[3]) or any(
        component.casefold().startswith("onedrive") for component in path.parts
    ):
        raise ValueError("World context must remain outside the checkout and OneDrive")
    if path.exists() and not path.is_file():
        raise ValueError("World context output must name a private JSON file")
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path


def _save(path: Path, world: WorldContext) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=".red-world-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            os.fchmod(handle.fileno(), 0o600)
            handle.write(world.model_dump_json(indent=2) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _source(article: Any, site: str, publication: str) -> tuple[datetime, WorldSource]:
    if not isinstance(article, dict):
        raise ValueError("Unexpected public article entry")
    relative = article.get("url")
    title = article.get("title")
    summary = article.get("summary")
    published = article.get("published_at")
    if (
        not isinstance(relative, str)
        or not isinstance(title, str)
        or not title.strip()
        or len(title) > 300
        or not isinstance(summary, str)
        or not summary.strip()
        or not isinstance(published, str)
    ):
        raise ValueError("Public article is missing its reviewed summary fields")
    parsed = urlsplit(relative)
    match = re.fullmatch(r"/" + re.escape(site) + r"/articles/([a-z0-9-]+)/?", relative)
    if parsed.scheme or parsed.netloc or parsed.query or parsed.fragment or match is None:
        raise ValueError("Public article URL does not match its observed publication")
    if "slug" in article and article["slug"] != match.group(1):
        raise ValueError("Public article slug does not match its observed URL")
    published_at = datetime.fromisoformat(published.replace("Z", "+00:00"))
    if published_at.utcoffset() is None:
        raise ValueError("Public article publication time requires an explicit timezone")
    url = NEWS_URL + relative
    # A publicly listed article can carry an in-world future publication time.
    # Preserve its supplied date without inferring that the article is private.
    attribution = f"{publication}; front-page summary; published {published}.\n"
    excerpt = summary.strip()
    remaining = 1000 - len(attribution)
    if remaining < 100:
        raise ValueError("Public article attribution exceeds the context bound")
    if len(excerpt) > remaining:
        excerpt = excerpt[: remaining - 3].rsplit(" ", 1)[0] + "..."
    return published_at, WorldSource(
        id=hashlib.sha256(url.encode()).hexdigest()[:16],
        title=title,
        url=url,
        text=attribution + excerpt,
    )


async def refresh_world(settings: Settings, output: Path) -> dict[str, Any]:
    """Replace only a fully validated snapshot; never return or log source content."""
    if not settings.ctn_network_enabled or settings.ctn_team_code is None:
        raise ValueError("Context refresh requires explicit network opt-in and a team code")
    require_open()
    path = _private_path(output)
    database: Database | None = None
    transport: CompetitionTransport | None = None
    try:
        async with asyncio.timeout(120):
            database = Database(settings)
            transport = CompetitionTransport(settings, TeamPacer(database))
            reader = CTNNewsReader(transport)
            index = await _read(reader.sites)
            publications: dict[str, str] = {}
            for site in index:
                slug, name = site.get("sub_url"), site.get("name")
                if (
                    not isinstance(slug, str)
                    or re.fullmatch(r"[a-z0-9-]+", slug) is None
                    or not isinstance(name, str)
                    or not name.strip()
                    or len(name) > 200
                ):
                    raise ValueError("Unexpected organiser publication index")
                publications.setdefault(slug, name)
                if len(publications) == 2:
                    break
            if len(publications) != 2:
                raise ValueError("Two public organiser publications are required")
            articles: dict[str, tuple[datetime, WorldSource]] = {}
            for slug, name in publications.items():
                page = await _read(partial(reader.front_page, slug))
                publication = page.get("site")
                entries = page.get("articles")
                if (
                    not isinstance(publication, dict)
                    or publication.get("sub_url") != slug
                    or not isinstance(entries, list)
                    or len(entries) > 200
                ):
                    raise ValueError("Unexpected organiser publication front page")
                for entry in entries:
                    published, source = _source(entry, slug, name)
                    articles[source.url] = (published, source)
            newest = sorted(
                articles.values(), key=lambda item: (item[0], item[1].url), reverse=True
            )
            relevant = [item for item in newest if _TOPIC.search(item[1].title)][:12]
            community = [item for item in newest if not _TOPIC.search(item[1].title)][:2]
            if not relevant:
                raise ValueError("No current public referendum context is available")
            world = WorldContext(
                version="ctn-frontpage-summary-v1",
                collected_at=transport.clock(),
                sources=[source for _, source in relevant + community],
            )
            _save(path, world)
            return {
                "source_count": len(world.sources),
                "digest": world.digest,
                "collected_at": world.collected_at.isoformat(),
            }
    except asyncio.CancelledError:
        raise
    except Exception:
        raise RuntimeError(
            "World context refresh failed; inspect the private configuration"
        ) from None
    finally:
        try:
            async with asyncio.timeout(settings.operation_timeout_seconds):
                if transport is not None:
                    await transport.close()
                if database is not None:
                    await database.close()
        except Exception:
            raise RuntimeError("World context refresh cleanup failed") from None
