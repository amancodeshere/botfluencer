import ast
import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from sqlalchemy import select

from botfluencer.blue.config import BlueSettings
from botfluencer.blue.data import group_split, prepare_rows, private_directory, reviewed_rows
from botfluencer.blue.model import Manifest
from botfluencer.blue.report import Reporter
from botfluencer.blue.store import BlueStore
from botfluencer.blue.training import metrics, select_threshold
from botfluencer.blue.worker import collect_incremental
from botfluencer.competition.errors import CompetitionHTTPError, RequestDeferred
from botfluencer.competition.transport import CompetitionTransport
from botfluencer.config import Settings
from botfluencer.storage.models import BlueReport, BlueScore
from botfluencer.storage.npc_posts import PostArchive, PostRecord
from botfluencer.storage.repository import Repository


def test_data_dedup_grouping_and_no_false_origin_labels() -> None:
    rows = prepare_rows(
        [
            dict(text="Example", label=0, group="author:1"),
            dict(text=" example ", label=0, group="author:2"),
            dict(text="Conflicting", label=0, group="author:1"),
            dict(text="conflicting", label=1, group="author:3"),
            dict(text="Synthetic Yes", label=1, group="author:1"),
        ]
    )
    assert len(rows) == 2
    assert {r["split"] for r in rows} == {group_split("author:1")}
    assert (
        metrics([0] * 400 + [1] * 100, [0.1] * 400 + [0.99] * 100, 0.95)["precision_lower_95"]
        > 0.95
    )
    assert select_threshold([0, 1], [0.99, 0.99]) == 1
    with pytest.raises(ValueError):
        metrics([0], [float("nan")], 0.9)
    with pytest.raises(ValueError):
        private_directory(Path.cwd() / "data" / "unsafe")
    reviewed = reviewed_rows([dict(author_id=7, source="team_review", label=1, text="Reviewed")])
    assert reviewed[0]["group"] == "author:7"
    with pytest.raises(ValueError, match="actual author"):
        reviewed_rows(
            [dict(author_id=7, group="fake-author", source="moderator", label=1, text="Reviewed")]
        )


def test_manifest_rejects_tampering(tmp_path: Path) -> None:
    files = {}
    for name in ["model.safetensors", "config.json", "tokenizer_config.json"]:
        (tmp_path / name).write_bytes(b"synthetic")
        files[name] = hashlib.sha256(b"synthetic").hexdigest()
    fields = dict(
        files=files,
        threshold=0.99,
        temperature=1.0,
        max_length=256,
        task="competitor_vs_npc",
        positive_label=1,
    )
    version = hashlib.sha256(json.dumps(fields, sort_keys=True).encode()).hexdigest()
    manifest = Manifest(version=version, **fields)
    manifest.verify(tmp_path)
    (tmp_path / "model.safetensors").write_bytes(b"changed")
    with pytest.raises(ValueError, match="checksum"):
        manifest.verify(tmp_path)


def test_notebooks_have_no_outputs_and_valid_python() -> None:
    for path in Path("notebooks").glob("*.ipynb"):
        notebook = json.loads(path.read_text())
        assert notebook["metadata"]["kernelspec"]["name"] == "botfluencer"
        for cell in notebook["cells"]:
            if cell["cell_type"] == "code":
                assert cell["execution_count"] is None and cell["outputs"] == []
                ast.parse("".join(cell["source"]))


@pytest.mark.parametrize("failure", ["timeout", "invalid_ack", "success", "throttle", "forbidden"])
async def test_report_wire_contract_and_ambiguity(failure: str) -> None:
    config = BlueSettings(
        enable_reports=True,
        own_author_ids=[10],
        session_token="test-account-token",
        reporter_account_id=10,
        expected_team_id=99,
    )

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/auth/me":
            assert request.method == "GET"
            assert request.headers["Authorization"] == "Bearer test-account-token"
            return httpx.Response(
                200, json={"account": {"account_id": 10}, "team": {"team_id": 99}}
            )
        assert str(request.url) == "https://social.legitreal.com/api/posts/7/report"
        assert request.headers["Authorization"] == "Bearer test-account-token"
        assert json.loads(request.content) == {
            "reason": "inauthentic",
            "detail": "Statistical evidence",
        }
        if failure == "timeout":
            raise httpx.ReadTimeout("synthetic timeout")
        if failure == "invalid_ack":
            return httpx.Response(200, json=[])
        if failure == "throttle":
            return httpx.Response(429, headers={"Retry-After": "30"})
        if failure == "forbidden":
            return httpx.Response(403, json={})
        return httpx.Response(200, json={"ok": True})

    settings = Settings(
        _env_file=None,
        DATABASE_URL="postgresql://test:test@example.invalid/test",
        CTN_NETWORK_ENABLED=True,
    )
    pacer = SimpleNamespace(reserve=AsyncMock(), observe=AsyncMock())
    transport = CompetitionTransport(settings, pacer, http_transport=httpx.MockTransport(handler))

    class FakeDB:
        def leadership(self, scope: str):
            from contextlib import asynccontextmanager

            @asynccontextmanager
            async def owner():
                yield SimpleNamespace(
                    execute=AsyncMock(),
                    scalar=AsyncMock(return_value=101),
                    commit=AsyncMock(),
                    closed=False,
                    invalidated=False,
                )

            return owner()

    store = SimpleNamespace(
        database=FakeDB(),
        scope="test-team",
        claim=AsyncMock(
            return_value={"post_id": 7, "detail": "Statistical evidence", "correlation_id": "id"}
        ),
        finish=AsyncMock(),
    )
    try:
        if failure == "success":
            assert (
                await Reporter(config, transport, store).dispatch("v1", validated=False)
                == "accepted"
            )
        else:
            with pytest.raises(
                (httpx.TransportError, ValueError, RequestDeferred, CompetitionHTTPError)
            ):
                await Reporter(config, transport, store).dispatch("v1", validated=False)
        result = store.finish.call_args.args[1]
        assert (
            result
            == {
                "success": "accepted",
                "timeout": "unknown",
                "invalid_ack": "unknown",
                "throttle": "approved",
                "forbidden": "rejected",
            }[failure]
        )
        if failure == "forbidden":
            assert store.finish.call_args.kwargs["block"]
    finally:
        await transport.close()


async def test_auto_reporting_requires_real_evaluation() -> None:
    reporter = Reporter(
        BlueSettings(
            enable_reports=True,
            auto_report=True,
            own_author_ids=[1],
            session_token="test",
            reporter_account_id=1,
            expected_team_id=99,
        ),
        SimpleNamespace(settings=SimpleNamespace(ctn_network_enabled=True)),
        None,
    )
    with pytest.raises(ValueError, match="real author-disjoint"):
        await reporter.dispatch("v1", validated=False)


@pytest.mark.integration
async def test_blue_recovery_pause_budgets_and_npc_exclusion(repo: Repository) -> None:
    namespace = repo.db.settings.namespace
    config = BlueSettings(namespace=namespace, own_author_ids=[22])
    store = BlueStore(repo.db, config, namespace)
    await store.initialize()
    archive = PostArchive(repo.db, namespace)

    def post(identity: int, author: int, npc: bool = False) -> PostRecord:
        return PostRecord(
            post_id=identity,
            author_id=author,
            user_name=None,
            name=None,
            post=f"Synthetic fixture observation {identity}",
            time_stamp=datetime.now(UTC),
            kind="post",
            npc_status="npc" if npc else "unverified",
        )

    await archive.save([post(1, 11, True), post(2, 11), post(3, 22), post(4, 33), post(5, 33)])
    posts = await store.pending_posts("v1")
    assert {p["post_id"] for p in posts} == {4, 5}
    await store.save_scores(posts, [0.999, 0.999], "v1", 0.99)
    await store.save_scores(posts, [0.999, 0.999], "v1", 0.99)
    async with repo.db.sessions() as session:
        scores = list(
            await session.scalars(select(BlueScore).where(BlueScore.namespace == namespace))
        )
        assert len(scores) == 2
    assert await store.claim("v1", auto=False) is None
    await store.approve(4)
    await store.update_state(paused=True)
    assert await store.claim("v1", auto=False) is None
    await store.update_state(paused=False)
    assert (await store.claim("v1", auto=False))["post_id"] == 4
    # A restart must preserve ambiguity and block another pending report.
    restarted = BlueStore(repo.db, config, namespace)
    assert await restarted.claim("v1", auto=True) is None
    async with repo.db.sessions() as session:
        report = await session.get(BlueReport, (namespace, 4))
        assert report.status == "unknown"
    await restarted.outcome(4, "genuine")
    await restarted.approve(5)
    assert await restarted.claim("v1", auto=False) is None
    assert (await restarted.summary())["blocked_until"] is not None


@pytest.mark.integration
async def test_feed_cursor_restart_and_boundary(repo: Repository) -> None:
    config = BlueSettings(namespace=repo.db.settings.namespace, max_pages=1)
    store = BlueStore(repo.db, config, "synthetic-team")
    await store.initialize()
    calls = []

    async def request(service, path, bucket, *, params):
        calls.append(dict(params))
        identity = 2 if "cursor" not in params else 1
        return {
            "items": [
                dict(
                    post_id=identity,
                    author_id=50,
                    body="Fixture",
                    created_at=datetime.now(UTC).isoformat(),
                    kind="post",
                )
            ],
            "next_cursor": "older" if identity == 2 else None,
        }

    transport = SimpleNamespace(request=request)
    assert await collect_incremental(transport, store) == 1
    assert (await store.state())["cursor"] == "older"
    restarted = BlueStore(repo.db, config, "synthetic-team")
    assert await collect_incremental(transport, restarted) == 1
    assert calls[-1]["cursor"] == "older"
    assert (await store.state())["anchor"] == 2
    # New poll encounters the committed anchor and stops without another traversal.
    assert await collect_incremental(transport, restarted) == 0
    assert (await store.state())["cursor"] is None


async def test_training_pause_keeps_collecting_without_loading_model(monkeypatch) -> None:
    from contextlib import asynccontextmanager

    import botfluencer.blue.worker as worker

    @asynccontextmanager
    async def leadership(_scope):
        yield SimpleNamespace(
            execute=AsyncMock(),
            commit=AsyncMock(),
            scalar=AsyncMock(return_value=101),
            closed=False,
            invalidated=False,
        )

    database = SimpleNamespace(leadership=leadership, close=AsyncMock())
    store = SimpleNamespace(
        initialize=AsyncMock(),
        state=AsyncMock(return_value={"paused": True}),
        heartbeat=AsyncMock(),
        summary=AsyncMock(return_value={}),
    )
    collector = AsyncMock(return_value=0)
    monkeypatch.setattr(worker, "Database", lambda _settings: database)
    monkeypatch.setattr(worker, "TeamPacer", lambda _database: SimpleNamespace(scope="test"))
    monkeypatch.setattr(
        worker,
        "CompetitionTransport",
        lambda *_args: SimpleNamespace(pacer=SimpleNamespace(scope="test"), close=AsyncMock()),
    )
    monkeypatch.setattr(worker, "BlueStore", lambda *_args: store)
    monkeypatch.setattr(worker, "collect_incremental", collector)

    def forbidden(*_args):
        raise AssertionError("A paused worker must not load a GPU model")

    monkeypatch.setattr(worker, "Detector", forbidden)
    settings = Settings(
        _env_file=None,
        DATABASE_URL="postgresql://test:test@example.invalid/test",
        CTN_NETWORK_ENABLED=True,
    )
    await worker.watch(settings, BlueSettings(), once=True)
    collector.assert_awaited_once()
    store.heartbeat.assert_awaited_once_with("paused")
