import asyncio

import pytest
from pydantic import SecretStr

from botfluencer.competition.errors import RequestDeferred
from botfluencer.competition.pacing import TeamPacer
from botfluencer.storage.database import Database
from botfluencer.storage.repository import Repository

pytestmark = pytest.mark.integration


def pacer_for(repo: Repository) -> TeamPacer:
    # Unique synthetic team identity per test, never the actual team credential.
    code = repo.namespace.removeprefix("test-")[:16]
    database = Database(repo.settings.model_copy(update={"ctn_team_code": SecretStr(code)}))
    return TeamPacer(database)


async def test_team_bucket_is_shared_across_namespaces_and_persists(repo: Repository) -> None:
    pacer = pacer_for(repo)
    other_db = Database(
        pacer.database.settings.model_copy(update={"namespace": "other-test-scope"})
    )
    other = TeamPacer(other_db)
    try:
        await pacer.reserve("llm")
        with pytest.raises(RequestDeferred):
            await other.reserve("llm")
        await pacer.observe("llm", limit=1, remaining=0, reset=300, retry_after=180)
        with pytest.raises(RequestDeferred) as raised:
            await other.reserve("llm")
        assert raised.value.seconds > 290
        # Another bucket has its own allowance.
        await other.reserve("feed")
    finally:
        await pacer.database.close()
        await other_db.close()


async def test_parallel_reservations_grant_only_one_slot(repo: Repository) -> None:
    pacer = pacer_for(repo)
    try:
        results = await asyncio.gather(
            pacer.reserve("llm"), pacer.reserve("llm"), return_exceptions=True
        )
        assert sum(result is None for result in results) == 1
        assert sum(isinstance(result, RequestDeferred) for result in results) == 1
    finally:
        await pacer.database.close()
