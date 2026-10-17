"""A bounded async Postgres pool and dedicated singleton connection."""

import asyncio
import hashlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from botfluencer.config import Settings


class LeadershipBusy(RuntimeError):
    pass


class Database:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.engine = create_async_engine(
            settings.sqlalchemy_url(),
            pool_size=5,
            max_overflow=0,
            pool_timeout=settings.operation_timeout_seconds,
            pool_pre_ping=True,
            connect_args={"connect_timeout": 5, "options": "-c statement_timeout=10000"},
        )
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False)
        # Advisory locks must remain on one physical connection, never a transaction pool.
        self.lock_engine = create_async_engine(
            settings.sqlalchemy_url(),
            poolclass=NullPool,
            connect_args={"connect_timeout": 5, "options": "-c statement_timeout=10000"},
        )

    async def ping(self) -> None:
        async with asyncio.timeout(self.settings.operation_timeout_seconds):
            async with self.engine.connect() as connection:
                await connection.execute(text("SELECT 1"))

    @asynccontextmanager
    async def leadership(self, scope: str | None = None) -> AsyncIterator[AsyncConnection]:
        lock_key = int.from_bytes(
            hashlib.sha256((scope or self.settings.namespace).encode()).digest()[:8], signed=True
        )
        async with self.lock_engine.connect() as connection:
            acquired = await connection.scalar(
                text("SELECT pg_try_advisory_lock(:key)"), {"key": lock_key}
            )
            await connection.commit()
            if not acquired:
                raise LeadershipBusy("Another worker owns this namespace; stop it before starting")
            try:
                yield connection
            finally:
                # Closing this NullPool connection releases the session lock even on failure.
                await connection.close()

    async def close(self) -> None:
        await self.engine.dispose()
        await self.lock_engine.dispose()
