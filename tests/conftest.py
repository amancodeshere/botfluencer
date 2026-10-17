import os
from collections.abc import AsyncIterator
from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy.engine import make_url

from botfluencer.config import Settings
from botfluencer.storage.database import Database
from botfluencer.storage.migrations import migration_config, upgrade
from botfluencer.storage.repository import Repository


@pytest.fixture(scope="session")
def test_database_url() -> str:
    url = os.environ.get("TEST_DATABASE_URL")
    if not url:
        if os.environ.get("CI"):
            pytest.fail("CI requires TEST_DATABASE_URL; integration tests must not be skipped")
        pytest.skip("Set TEST_DATABASE_URL to a dedicated Postgres database ending in _test")
    if not (make_url(url).database or "").endswith("_test"):
        pytest.fail("Integration database name must end in _test; refusing to modify other data")
    settings = Settings(_env_file=None, DATABASE_URL=url)
    upgrade(settings)
    command.check(migration_config(settings))
    return url


@pytest.fixture
async def repo(test_database_url: str) -> AsyncIterator[Repository]:
    settings = Settings(
        _env_file=None,
        DATABASE_URL=test_database_url,
        namespace=f"test-{uuid4().hex}",
        strategy="synthetic",
        account_cooldown_seconds=0,
        poll_interval_seconds=0.01,
        operation_timeout_seconds=60,
        heartbeat_timeout_seconds=90,
    )
    database = Database(settings)
    repository = Repository(database)
    await repository.initialize()
    try:
        yield repository
    finally:
        await database.close()
