import hashlib
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import ValidationError

from botfluencer.blue.config import BlueSettings
from botfluencer.blue.model import Detector, Manifest
from botfluencer.blue.report import Reporter
from botfluencer.blue.training import metrics
from botfluencer.competition.errors import CompetitionHTTPError
from botfluencer.competition.transport import CompetitionTransport
from botfluencer.config import Settings


def real_validation(**overrides):
    values = metrics([1] * 100 + [0] * 400, [0.99] * 100 + [0.01] * 400, 0.95)
    values.update(
        schema_version="blue-real-v2",
        model_version="a" * 64,
        threshold=0.95,
        training_provenance_sha256="b" * 64,
        dataset_hash="c" * 64,
        real_labels_only=True,
        author_disjoint=True,
        own_positive_sources_excluded=True,
        label_source_counts={"team_review": 500},
        positive_author_count=100,
        negative_author_count=400,
    )
    values.update(overrides)
    return values


def sealed_detector(tmp_path, *, provenance_overrides=None):
    provenance = {
        "schema_version": "blue-training-v2",
        "own_author_ids": [10],
        "used_groups": ["author:1"],
        "used_text_hashes": ["d" * 64],
        "dataset_hash": "c" * 64,
        "base_model": "synthetic-model",
        "seed": 42,
    }
    provenance.update(provenance_overrides or {})
    (tmp_path / "training-provenance.json").write_text(json.dumps(provenance))
    for name in ("model.safetensors", "config.json", "tokenizer_config.json"):
        (tmp_path / name).write_bytes(b"synthetic model fixture")
    files = {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in tmp_path.iterdir()
    }
    identity = dict(
        files=files,
        threshold=0.95,
        temperature=1.0,
        max_length=256,
        task="competitor_vs_npc",
        positive_label=1,
    )
    version = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    detector = Detector.__new__(Detector)
    detector.root = tmp_path
    detector.manifest = Manifest(version=version, **identity)
    detector.manifest.verify(tmp_path)
    report = real_validation(
        model_version=version, training_provenance_sha256=files["training-provenance.json"]
    )
    return detector, report


def test_independent_real_validation_can_authorize_only_matching_model(tmp_path) -> None:
    detector, report = sealed_detector(tmp_path)
    path = tmp_path / "real-validation.json"
    assert not detector.automatic_reporting_validated()
    path.write_text(json.dumps(report))
    assert detector.automatic_reporting_validated()


@pytest.mark.parametrize(
    "overrides",
    [
        {"model_version": "b" * 64},
        {"real_labels_only": False},
        {"author_disjoint": False},
        {"real_labels_only": "true"},
        {"positive_count": 99},
        {"negative_count": 399},
        {"positive_count": 100.5},
        {"negative_count": "400"},
        {"positive_count": True},
        {"precision_lower_95": 2},
        {"precision_lower_95": -1},
        {"precision_lower_95": float("nan")},
        {"precision_lower_95": float("inf")},
        {"fpr_upper_95": -1},
        {"fpr_upper_95": float("nan")},
        {"fpr_upper_95": float("inf")},
        {"precision_lower_95": 0.94},
        {"fpr_upper_95": 0.02},
    ],
)
def test_malformed_synthetic_or_insufficient_validation_never_authorizes_reports(
    tmp_path, overrides: dict
) -> None:
    detector, report = sealed_detector(tmp_path)
    report.update(overrides)
    (tmp_path / "real-validation.json").write_text(json.dumps(report))
    assert detector.automatic_reporting_validated() is False


@pytest.mark.parametrize("content", ["not JSON", "[]", "null", "{}"])
def test_invalid_real_validation_file_fails_closed(tmp_path, content: str) -> None:
    detector, _ = sealed_detector(tmp_path)
    (tmp_path / "real-validation.json").write_text(content)
    assert detector.automatic_reporting_validated() is False


@pytest.mark.parametrize("own_ids", [[True], [0], [-1], [10, 10], ["10"]])
def test_blue_own_account_ids_are_strict_positive_unique_ids(own_ids: list) -> None:
    with pytest.raises(ValidationError):
        BlueSettings(own_author_ids=own_ids)


@asynccontextmanager
async def reporter_for(handler, *, claim=None, config=None, owner=None):
    settings = Settings(
        _env_file=None,
        DATABASE_URL="postgresql://test@localhost/botfluencer_test",
        CTN_NETWORK_ENABLED=True,
    )
    pacer = SimpleNamespace(reserve=AsyncMock(), observe=AsyncMock())
    transport = CompetitionTransport(settings, pacer, http_transport=httpx.MockTransport(handler))

    if owner is None:
        owner = SimpleNamespace(
            execute=AsyncMock(),
            scalar=AsyncMock(return_value=101),
            commit=AsyncMock(),
            closed=False,
            invalidated=False,
        )

    @asynccontextmanager
    async def leadership(_scope):
        yield owner

    store = SimpleNamespace(
        database=SimpleNamespace(leadership=leadership),
        scope="synthetic-team",
        claim=AsyncMock(return_value=claim),
        finish=AsyncMock(),
    )
    if config is None:
        config = BlueSettings(
            enable_reports=True,
            own_author_ids=[10],
            session_token="synthetic-blue-session",
            reporter_account_id=10,
            expected_team_id=99,
        )
    try:
        yield Reporter(config, transport, store), store, transport
    finally:
        await transport.close()


async def test_blue_reporter_identity_verified_once_before_idle_claims() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.method == "GET" and request.url.path == "/api/auth/me"
        assert request.headers["Authorization"] == "Bearer synthetic-blue-session"
        return httpx.Response(200, json={"account": {"account_id": 10}, "team": {"team_id": 99}})

    async with reporter_for(handler) as (reporter, store, _):
        assert await reporter.dispatch("synthetic-model", validated=False) == "idle"
        assert await reporter.dispatch("synthetic-model", validated=False) == "idle"
        assert len(calls) == 1
        assert store.claim.await_count == 2
        store.finish.assert_not_awaited()


@pytest.mark.parametrize("account_id,team_id", [(11, 99), (10, 98), (True, 99), (10, False)])
async def test_blue_identity_mismatch_prevents_claim_and_report_budget(account_id, team_id) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        return httpx.Response(
            200, json={"account": {"account_id": account_id}, "team": {"team_id": team_id}}
        )

    async with reporter_for(handler) as (reporter, store, transport):
        with pytest.raises(ValueError):
            await reporter.dispatch("synthetic-model", validated=False)
        store.claim.assert_not_awaited()
        store.finish.assert_not_awaited()
        assert "post_report" not in [
            call.args[0] for call in transport.pacer.reserve.await_args_list
        ]


@pytest.mark.parametrize("status", [408, 500, 302])
async def test_ambiguous_report_responses_remain_unknown(status: int) -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.method == "GET":
            return httpx.Response(
                200, json={"account": {"account_id": 10}, "team": {"team_id": 99}}
            )
        assert request.url.path == "/api/posts/7/report"
        return httpx.Response(status)

    claim = {
        "post_id": 7,
        "detail": "Synthetic statistical evidence",
        "correlation_id": "synthetic",
    }
    async with reporter_for(handler, claim=claim) as (reporter, store, _):
        with pytest.raises(CompetitionHTTPError):
            await reporter.dispatch("synthetic-model", validated=False)
        assert store.finish.await_args.args[:2] == (7, "unknown")
        assert sum(request.method == "POST" for request in calls) == 1


@pytest.mark.parametrize(
    "overrides",
    [
        {"threshold": 0.9},
        {"training_provenance_sha256": "f" * 64},
        {"own_positive_sources_excluded": False},
        {"positive_author_count": 101},
        {"label_source_counts": {"synthetic": 500}},
        {"label_source_counts": {"team_review": 499}},
        {"true_positives": 101},
        {"precision_lower_95": 0.99},
    ],
)
def test_real_validation_is_bound_to_release_provenance_and_confusion_counts(
    tmp_path, overrides: dict
) -> None:
    detector, report = sealed_detector(tmp_path)
    report.update(overrides)
    (tmp_path / "real-validation.json").write_text(json.dumps(report))
    assert not detector.automatic_reporting_validated()


def test_tampered_or_unsealed_training_provenance_cannot_authorize_reports(tmp_path) -> None:
    detector, report = sealed_detector(tmp_path)
    (tmp_path / "real-validation.json").write_text(json.dumps(report))
    assert detector.automatic_reporting_validated()
    provenance = tmp_path / "training-provenance.json"
    provenance.write_text("Synthetic tampered provenance")
    assert not detector.automatic_reporting_validated()
    detector.manifest = detector.manifest.model_copy(update={"files": {}})
    assert not detector.automatic_reporting_validated()


def test_legacy_validation_attestation_cannot_authorize_reports(tmp_path) -> None:
    detector, _ = sealed_detector(tmp_path)
    (tmp_path / "real-validation.json").write_text(
        json.dumps(
            dict(
                model_version=detector.manifest.version,
                real_labels_only=True,
                author_disjoint=True,
                positive_count=100,
                negative_count=400,
                precision_lower_95=0.99,
                fpr_upper_95=0.001,
            )
        )
    )
    assert not detector.automatic_reporting_validated()


def test_model_release_diagnostics_verify_artifacts_without_loading_ml(
    tmp_path, monkeypatch
) -> None:
    import botfluencer.blue.model as module

    def forbidden(_name):
        raise AssertionError("Operator diagnostics must not load ML runtimes")

    monkeypatch.setattr(module.importlib, "import_module", forbidden)
    assert module.inspect_release(tmp_path)["status"] == "missing"
    detector, report = sealed_detector(tmp_path)
    (tmp_path / "manifest.json").write_text(detector.manifest.model_dump_json())
    (tmp_path / "real-validation.json").write_text(json.dumps(report))
    summary = module.inspect_release(tmp_path)
    assert summary["status"] == "verified" and summary["automatic_reporting_validated"] is True
    assert summary["model_version"] == detector.manifest.version
    (tmp_path / "model.safetensors").write_bytes(b"Synthetic tampering")
    assert module.inspect_release(tmp_path) == {
        "status": "invalid",
        "automatic_reporting_validated": False,
    }


@pytest.mark.parametrize(
    "overrides",
    [
        {"reporter_account_id": None},
        {"expected_team_id": None},
        {"session_token": None},
        {"session_token": "  "},
        {"own_author_ids": [11]},
    ],
)
def test_enabled_blue_reporting_requires_complete_own_identity(overrides: dict) -> None:
    values = dict(
        enable_reports=True,
        own_author_ids=[10],
        session_token="synthetic-blue-session",
        reporter_account_id=10,
        expected_team_id=99,
    )
    values.update(overrides)
    with pytest.raises(ValidationError):
        BlueSettings(**values)


@pytest.mark.parametrize("after_submission", [False, True])
async def test_report_owner_change_never_retries_or_marks_acceptance(
    after_submission: bool,
) -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.method == "GET":
            return httpx.Response(
                200, json={"account": {"account_id": 10}, "team": {"team_id": 99}}
            )
        return httpx.Response(200, json={"ok": True})

    owner = SimpleNamespace(
        closed=False,
        invalidated=False,
        commit=AsyncMock(),
        scalar=AsyncMock(side_effect=[101, 101, 102] if after_submission else [101, 102]),
    )
    claim = {"post_id": 7, "detail": "Synthetic evidence", "correlation_id": "synthetic"}
    async with reporter_for(handler, claim=claim, owner=owner) as (reporter, store, _):
        with pytest.raises(RuntimeError, match="ownership connection changed"):
            await reporter.dispatch("synthetic-model", validated=False)
        assert store.finish.await_args.args == (7, "unknown")
        assert sum(request.method == "POST" for request in calls) == int(after_submission)


async def test_synthetic_validation_gate_precedes_identity_budget_and_claim() -> None:
    calls = []
    config = BlueSettings(
        enable_reports=True,
        auto_report=True,
        own_author_ids=[10],
        session_token="synthetic-blue-session",
        reporter_account_id=10,
        expected_team_id=99,
    )
    async with reporter_for(lambda request: calls.append(request), config=config) as (
        reporter,
        store,
        transport,
    ):
        with pytest.raises(ValueError, match="real author-disjoint"):
            await reporter.dispatch("synthetic-model", validated=False)
        assert not calls
        transport.pacer.reserve.assert_not_awaited()
        store.claim.assert_not_awaited()


async def test_rotated_reporter_token_requires_new_identity_verification() -> None:
    from pydantic import SecretStr

    headers = []

    def handler(request: httpx.Request) -> httpx.Response:
        headers.append(request.headers["Authorization"])
        return httpx.Response(200, json={"account": {"account_id": 10}, "team": {"team_id": 99}})

    async with reporter_for(handler) as (reporter, _, _):
        assert await reporter.dispatch("synthetic-model", validated=False) == "idle"
        reporter.config.session_token = SecretStr("synthetic-rotated-session")
        assert await reporter.dispatch("synthetic-model", validated=False) == "idle"
    assert headers == ["Bearer synthetic-blue-session", "Bearer synthetic-rotated-session"]


@pytest.mark.parametrize(
    "overrides",
    [
        {"source": "synthetic"},
        {"label": 0},
        {"label": True},
        {"author_id": True},
        {"post_id": "123"},
        {"author_id": -1},
        {"group": "author:99"},
    ],
)
def test_own_publication_training_examples_require_controlled_strict_provenance(
    overrides: dict,
) -> None:
    from botfluencer.blue.data import own_published_rows

    row = dict(
        author_id=10,
        post_id=123,
        label=1,
        source="own_published_controlled",
        text="Synthetic acknowledged own publication",
    )
    row.update(overrides)
    with pytest.raises(ValueError):
        own_published_rows([row])


def test_own_publication_training_summary_is_diagnostic_not_reporting_authority(tmp_path) -> None:
    from botfluencer.blue.data import save_jsonl
    from botfluencer.blue.training import training_data_summary

    save_jsonl(
        tmp_path / "npc.jsonl",
        [dict(text="Synthetic known NPC discussion", label=0, group="author:7")],
    )
    row = dict(
        author_id=10,
        post_id=123,
        label=1,
        source="own_published_controlled",
        text="Synthetic acknowledged own publication",
    )
    save_jsonl(tmp_path / "own-published.jsonl", [row, dict(row, post_id=124)])
    summary = training_data_summary(tmp_path)
    assert summary["deduplicated_examples"] == 2
    assert summary["positive_source_counts"] == {"own_published_controlled": 1}
    assert summary["own_heldout_diagnostic"] is True
    assert summary["automatic_reporting_validated"] is False
    assert summary["ready"] is False


@pytest.mark.parametrize(
    "case",
    [
        "own_author",
        "synthetic_source",
        "training_author",
        "training_text",
        "boolean_label",
        "conflicting_author",
    ],
)
def test_real_holdout_excludes_own_synthetic_and_training_leaks_before_loading_model(
    tmp_path, monkeypatch, case: str
) -> None:
    import botfluencer.blue.training as module
    from botfluencer.blue.data import save_jsonl, text_key

    provenance = {"used_text_hashes": [text_key("Synthetic trained text")]}
    detector, _ = sealed_detector(tmp_path, provenance_overrides=provenance)
    (tmp_path / "manifest.json").write_text(detector.manifest.model_dump_json())
    rows = [
        dict(author_id=20, label=1, source="team_review", text="Synthetic reviewed competitor"),
        dict(author_id=21, label=0, source="moderator", text="Synthetic reviewed NPC"),
    ]
    if case == "own_author":
        rows[0]["author_id"] = 10
    elif case == "synthetic_source":
        rows[0]["source"] = "synthetic"
    elif case == "training_author":
        rows[0]["author_id"] = 1
    elif case == "training_text":
        rows[0]["text"] = "Synthetic trained text"
    elif case == "boolean_label":
        rows[0]["label"] = True
    else:
        rows[1]["author_id"] = 20
    labels = tmp_path / "reviewed-holdout.jsonl"
    save_jsonl(labels, rows)

    def forbidden(_path):
        raise AssertionError("Invalid holdout must be rejected before loading CUDA model")

    monkeypatch.setattr(module, "Detector", forbidden)
    with pytest.raises(ValueError):
        module.validate_real(tmp_path, labels)
    assert not (tmp_path / "real-validation.json").exists()


async def test_feed_cursor_cycle_is_rejected_after_restarted_collection(monkeypatch) -> None:
    from datetime import UTC, datetime

    import botfluencer.blue.worker as module

    state = dict(cursor=None, anchor=None, next_anchor=None, seen_cursors=[])

    async def update_state(**values):
        state.update(values)

    store = SimpleNamespace(
        database=object(),
        namespace="synthetic-cursor",
        config=BlueSettings(max_pages=1),
        state=AsyncMock(side_effect=lambda: dict(state)),
        update_state=AsyncMock(side_effect=update_state),
    )
    monkeypatch.setattr(
        module, "PostArchive", lambda *_: SimpleNamespace(save=AsyncMock(return_value=1))
    )
    cursors = iter(["cursor-a", "cursor-b", "cursor-a"])

    async def request(*args, **kwargs):
        return {
            "items": [
                dict(
                    post_id=7,
                    author_id=99,
                    body="Synthetic cursor fixture",
                    created_at=datetime.now(UTC).isoformat(),
                    kind="post",
                )
            ],
            "next_cursor": next(cursors),
        }

    transport = SimpleNamespace(request=request)
    assert await module.collect_incremental(transport, store) == 1
    assert state["cursor"] == "cursor-a"
    assert await module.collect_incremental(transport, store) == 1
    assert state["cursor"] == "cursor-b"
    committed = dict(state)
    with pytest.raises(ValueError, match="Invalid cursor"):
        await module.collect_incremental(transport, store)
    assert state == committed
    assert len(state["seen_cursors"]) == 2
    assert all(len(cursor_hash) == 64 for cursor_hash in state["seen_cursors"])
