"""Audited operator expansion; session identity checks precede the transaction."""

import asyncio

from botfluencer.competition.pacing import TeamPacer
from botfluencer.competition.transport import CompetitionTransport
from botfluencer.config import Settings
from botfluencer.red.config import RedSettings
from botfluencer.red.executor import ReplyExecutor
from botfluencer.red.store import RedStore
from botfluencer.storage.database import Database


async def amend(settings: Settings, config: RedSettings, reason: str) -> dict[str, int]:
    database = Database(settings)
    transport = CompetitionTransport(settings, TeamPacer(database))
    store = RedStore(database)
    store.config, store.scope = config, transport.pacer.scope
    try:
        if not await store.is_paused():
            raise ValueError("Pause and stop the existing service before expanding")
        executor = ReplyExecutor(
            transport,
            config.sessions(settings),
            npc_author_ids=frozenset(),
            expected_team_id=config.expected_team_id,
            ends_at=config.ends_at,
            enabled=config.enable_replies,
        )
        async with asyncio.timeout(config.identity_timeout_seconds):
            await executor.verify_sessions()
            return await store.amend(reason)
    finally:
        await transport.close()
        await database.close()
