from types import SimpleNamespace
from unittest.mock import Mock
from datetime import date
import time
import uuid
import json
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from src.modules.qlib_research import service, universe
from src.modules.qlib_research.contracts import DatasetInput, UniverseConfigInput
from src.platform.persistence.qlib_models import (
    QlibRecord,
    QlibUniverseMember,
    QlibUniverseJob,
)
from tests.test_qlib_service import storage, model
from tests.qlib_fixture import panel
from marketdata.research import ResearchHistory


@pytest.mark.parametrize("market", ["CN", "HK", "US"])
def test_market_contracts_and_currency_lots(market):
    data = DatasetInput.model_validate(panel(market=market))
    assert data.market == market
    if market != "CN":
        assert all(m.price_limit is None for m in data.members)
    if market == "US":
        assert all(m.lot == 1 for m in data.members)


def test_three_market_approval_cas_rollback_retirement_and_execution_boundary(storage):
    cn = model(storage)
    hk = model(storage)
    us = model(storage)
    with storage() as db:
        for ident, market in [(cn, "CN"), (hk, "HK"), (us, "US")]:
            row = db.get(QlibRecord, ident)
            row.payload = {**row.payload, "market": market}
        db.commit()
    service.publish(cn, 0, "CN reviewed research", "research", "test")
    service.publish(hk, 1, "HK reviewed research", "research", "test")
    state = service.publish(us, 2, "US reviewed research", "research", "test")
    assert (
        state["champions"] == {"CN": cn, "HK": hk, "US": us}
        and state["champion_id"] == cn
    )
    for ident in (cn, hk, us):
        with pytest.raises(ValueError, match="retire_champion"):
            service.retire(ident, 3, "still active", "test")
    with pytest.raises(ValueError, match="market_research_only"):
        service.publish(hk, 3, "not formally validated", "validated", "test")
    run = uuid.uuid4().hex
    with storage() as db:
        db.add(
            QlibRecord(
                id=run,
                kind="selection",
                parent_id=hk,
                name="test",
                payload={"market": "HK", "model_id": hk},
            )
        )
        db.commit()
    with pytest.raises(ValueError, match="market_execution_not_supported"):
        service.make_decision(run, "test", True)


def test_mixed_currency_capital_is_not_backtested_without_fx():
    data = panel(market="HK")
    data["members"][0]["currency"] = "USD"
    with pytest.raises(ValueError, match="currency_mismatch"):
        DatasetInput.model_validate(data)


def test_pagination_search_and_top_filter_use_full_market_rank(storage):
    ident = model(storage)
    service.publish(ident, 0, "approved research model", "research", "test")
    predictions = [
        {"symbol": f"SH{600000+i}", "market": "CN", "rank": i + 1, "tradable": True}
        for i in range(1000)
    ]
    run = uuid.uuid4().hex
    with storage() as db:
        db.add(
            QlibRecord(
                id=run,
                kind="selection",
                parent_id=ident,
                name="test",
                payload={
                    "market": "CN",
                    "model_id": ident,
                    "asof_date": "2026-10-13",
                    "config": {"topk": 10},
                    "data_kind": "market",
                    "predictions": predictions,
                    "names": {
                        r["symbol"]: f"Company {i}" for i, r in enumerate(predictions)
                    },
                },
            )
        )
        db.commit()
    result = service.opportunities(limit=50, page=3, top=20)
    assert result["total"] == 200 and result["prediction_count"] == 1000
    assert result["items"][0]["rank"] == 101 and result["items"][-1]["rank"] == 150
    assert len(result["run"]["names"]) == 50
    assert (
        service.opportunities(limit=50, search="Company 999")["items"][0]["rank"]
        == 1000
    )
    assert "predictions" not in service.list_records("selection", True, "CN")[0]
    with pytest.raises(ValueError, match="market_mismatch"):
        service.opportunities("HK", run_id=run)


def test_rank_filter_accepts_serialized_browser_query_integers(storage):
    from src.modules.qlib_research import api

    app = FastAPI()
    app.include_router(api.router, prefix="/qlib")
    app.dependency_overrides[api.current_actor] = lambda: "qa-user"
    with TestClient(app) as client:
        for top in (0, 10, 20, 50):
            response = client.get(
                f"/qlib/opportunities?market=US&page=1&limit=50&top={top}"
            )
            assert response.status_code == 200
        assert client.get("/qlib/opportunities?top=11").status_code == 422
        assert client.get("/qlib/opportunities?top=invalid").status_code == 422


def test_training_result_persists_snapshot_lineage_after_database_session_closes(
    storage, monkeypatch
):
    payload = panel(end=date(2026, 10, 13))
    payload["provenance"] = {"universe_snapshot_id": "a" * 32}
    dataset = service.import_dataset(DatasetInput.model_validate(payload), "qa")
    from src.modules.qlib_research.contracts import ResearchConfig

    job = service.enqueue(
        dataset["id"], ResearchConfig().model_dump(), "snapshot-lineage-test", "qa"
    )
    output = {
        "predictions": [{"symbol": payload["members"][0]["symbol"], "rank": 1}],
        "feature_set": "Alpha158",
        "runtime": {"pyqlib": "0.9.7"},
        "segments": {},
        "metrics": {},
        "baseline": {},
        "rolling": [],
        "replay_fingerprint": "controlled-not-a-real-model",
        "data_kind": "synthetic",
        "universe_kind": "fixed_research",
        "adjustment": "snapshot_adjusted",
        "asof_date": "2026-10-13",
        "label": "controlled",
        "monitor": {},
        "prediction_scale": "controlled test",
        "market": "CN",
        "price_basis": "raw_execution",
    }

    def launch(*args, **kwargs):
        (Path(kwargs["cwd"]) / "result.json").write_text(json.dumps(output))
        return SimpleNamespace(wait=lambda **kw: 0, poll=lambda: 0)

    monkeypatch.setattr(service.subprocess, "Popen", launch)
    service.execute_job(job["id"])
    assert service.jobs()[0]["status"] == "succeeded"
    assert service.list_records("model")[0]["universe_snapshot_id"] == "a" * 32
    assert service.list_records("selection")[0]["selection_scope"] == "training_cohort"


def test_durable_jobs_idempotency_cancel_restart_resume_generation(
    storage, monkeypatch
):
    monkeypatch.setattr(
        universe,
        "get_market_data",
        lambda: SimpleNamespace(research_sources=lambda m: ["yahoo"]),
    )
    monkeypatch.setattr(universe, "EXECUTOR", SimpleNamespace(submit=Mock()))
    universe.configure(
        UniverseConfigInput(license="CC0 controlled", license_acknowledged=True), "test"
    )
    job = universe.enqueue("history", "full-market-key", "test", market="HK")
    assert (
        universe.enqueue("history", "full-market-key", "test", market="HK")["id"]
        == job["id"]
    )
    universe.EXECUTOR.submit.assert_called_once()
    with pytest.raises(ValueError, match="request_key_conflict"):
        universe.enqueue("history", "full-market-key", "test", market="US")
    universe.cancel(job["id"], "test")
    assert universe.resume(job["id"], "test")["status"] == "queued"
    universe.recover()
    with storage() as db:
        assert db.get(QlibUniverseJob, job["id"]).status == "interrupted"
    universe.configure(
        UniverseConfigInput(license="CC0 changed", license_acknowledged=True), "test"
    )
    with pytest.raises(ValueError, match="configuration_changed"):
        universe.resume(job["id"], "test")


def test_immutable_history_rejects_traversal_tamper_and_price_revisions(storage):
    history = {
        "vendor": "yahoo",
        "adjustment_method": "snapshot",
        "bars": [{"date": "2026-10-12", "close": 10}],
    }
    ident, path = universe.write_immutable("history", history)
    assert (
        universe.read_history(ident) == history and path.stat().st_mode & 0o777 == 0o600
    )
    with pytest.raises(ValueError):
        universe.read_history("../" + "a" * 61)
    revised = {**history, "bars": [{"date": "2026-10-12", "close": 11}]}
    with pytest.raises(ValueError, match="revision_requires_review"):
        universe.append_history(history, revised)
    path.write_text("{}")
    with pytest.raises(ValueError, match="hash_mismatch"):
        universe.read_history(ident)


def test_resume_is_independent_between_markets(storage, monkeypatch):
    monkeypatch.setattr(
        universe,
        "get_market_data",
        lambda: SimpleNamespace(research_sources=lambda m: ["yahoo"]),
    )
    monkeypatch.setattr(universe, "EXECUTOR", SimpleNamespace(submit=Mock()))
    universe.configure(
        UniverseConfigInput(license="CC0 fixture", license_acknowledged=True), "test"
    )
    cn = universe.enqueue("history", "parallel-cn-key", "test", market="CN")
    hk = universe.enqueue("history", "parallel-hk-key", "test", market="HK")
    universe.cancel(hk["id"], "test")
    assert universe.resume(hk["id"], "test")["status"] == "queued"
    with pytest.raises(ValueError, match="universe_busy"):
        universe.enqueue("history", "duplicate-cn-active", "test", market="CN")


@pytest.mark.parametrize("frozen", [False, True])
def test_same_session_resume_uses_observed_calendar_when_upstream_is_offline(frozen):
    dates = panel(end=date(2026, 10, 13))["calendar"]
    md = SimpleNamespace(
        research_sources=lambda m: ["tencent"],
        research_calendar=Mock(side_effect=ConnectionError()),
    )
    calendars, origins = universe.history_calendars(
        md,
        ["CN"],
        {"CN": dates[-1]},
        5,
        {"CN": dates[-1]},
        {"CN": dates} if frozen else {},
        {"CN": dates},
    )
    assert calendars == {"CN": dates}
    assert "observed_calendar" in origins["CN"]
    if frozen:
        md.research_calendar.assert_not_called()


def test_rewound_cursor_zero_reuses_only_its_frozen_same_session_calendar():
    dates = panel(end=date(2026, 10, 13))["calendar"]
    md = SimpleNamespace(research_sources=lambda m: ["tencent"], research_calendar=Mock(side_effect=ConnectionError()))
    result, origins = universe.history_calendars(md, ["CN"], {"CN": dates[-1]}, 0,
        {"CN": dates[-1]}, {"CN": dates}, {})
    assert result["CN"] == dates and origins["CN"] == "job_frozen_observed_calendar"
    md.research_calendar.assert_not_called()


@pytest.mark.parametrize(
    "case", ["new_job", "new_session", "stale", "unsorted", "disabled"]
)
def test_resume_calendar_never_guesses_or_bypasses_disabled_source(case):
    dates = panel(end=date(2026, 10, 13))["calendar"]
    md = SimpleNamespace(
        research_sources=lambda m: [] if case == "disabled" else ["tencent"],
        research_calendar=Mock(side_effect=ConnectionError()),
    )
    cached = (
        dates[:-1]
        if case == "stale"
        else list(reversed(dates)) if case == "unsorted" else dates
    )
    with pytest.raises(ValueError, match="calendar_unavailable|source_disabled"):
        universe.history_calendars(
            md,
            ["CN"],
            {"CN": dates[-1]},
            0 if case == "new_job" else 5,
            {"CN": "2026-10-12" if case == "new_session" else dates[-1]},
            {},
            {"CN": cached},
        )


@pytest.mark.parametrize("cancelled", [False, True])
def test_parallel_history_commits_only_a_contiguous_durable_prefix(
    storage, monkeypatch, cancelled
):
    data = panel(end=date(2026, 10, 13), symbols=9)
    grouped = {}
    for bar in data["bars"]:
        grouped.setdefault(bar["symbol"], []).append(bar)
    job_id = None

    def history(symbol, **kw):
        index = int(symbol[-1])
        time.sleep((3 - index % 4) * 0.002)
        if cancelled and index == 3:
            universe.cancel(job_id, "controlled-test")
        return ResearchHistory(
            vendor="tencent", bars=grouped[symbol], calendar=[], currency="CNY"
        )

    md = SimpleNamespace(
        research_sources=lambda m: ["tencent"],
        research_calendar=lambda **kw: data["calendar"],
        research_history=history,
    )
    monkeypatch.setattr(universe, "get_market_data", lambda: md)
    monkeypatch.setattr(universe, "EXECUTOR", SimpleNamespace(submit=Mock()))
    universe.configure(
        UniverseConfigInput(license="CC0 fixture", license_acknowledged=True), "test"
    )
    with storage() as db:
        db.add(
            QlibRecord(
                id="a" * 32,
                kind="catalog",
                name="controlled catalog",
                payload={"market": "CN", "total": 9},
            )
        )
        for symbol in grouped:
            db.add(
                QlibUniverseMember(
                    symbol=symbol,
                    market="CN",
                    catalog_id="a" * 32,
                    status="pending",
                    metadata_={
                        "market": "CN",
                        "currency": "CNY",
                        "name": "QA",
                        "board": "MAIN",
                        "lot": 100,
                        "price_limit": 0.1,
                        "special_treatment": False,
                    },
                )
            )
        state = service.control(db)
        state.settings = {**state.settings, "universe_catalogs": {"CN": "a" * 32}}
        db.commit()
    job_id = universe.enqueue(
        "history", "parallel-history-receipt", "test", market="CN"
    )["id"]
    universe.execute(job_id)
    with storage() as db:
        job = db.get(QlibUniverseJob, job_id)
        ready = db.query(QlibUniverseMember).filter_by(status="ready").count()
        assert ready == job.cursor
        assert job.status == ("cancelled" if cancelled else "succeeded")
        assert job.cursor < 9 if cancelled else job.cursor == 9
        if not cancelled:
            assert job.progress["completed"] == job.progress["total"] == 9


@pytest.mark.parametrize("failure", ["empty_with_backup_offline", "transport"])
def test_partial_board_failure_is_not_a_global_outage_and_resume_is_durable(storage, monkeypatch, failure):
    data = panel(end=date(2026, 10, 13))
    codes = [f"BJ9200{i:02d}" for i in range(12)] + [f"SH6000{i:02d}" for i in range(12)]
    recovered = [False]
    calls = []
    template = [b for b in data["bars"] if b["symbol"] == data["members"][0]["symbol"]]
    def history(symbol, *, vendor, **kwargs):
        calls.append((symbol, vendor))
        if not recovered[0] and (failure == "transport" or symbol.startswith("BJ")):
            raise ValueError("qlib_source_transport_unavailable" if vendor == "eastmoney" or failure == "transport" else "qlib_source_history_unavailable")
        return ResearchHistory(vendor=vendor, bars=[{**b, "symbol": symbol} for b in template], calendar=[], currency="CNY")
    md = SimpleNamespace(research_sources=lambda m: ["tencent", "eastmoney"], research_calendar=lambda **kw: data["calendar"], research_history=history)
    monkeypatch.setattr(universe, "get_market_data", lambda: md)
    monkeypatch.setattr(universe, "EXECUTOR", SimpleNamespace(submit=Mock()))
    universe.configure(UniverseConfigInput(markets=["CN"], license="CC0 controlled", license_acknowledged=True), "test")
    with storage() as db:
        db.add(QlibRecord(id="a" * 32, kind="catalog", name="controlled", payload={"market": "CN", "total": len(codes)}))
        for symbol in codes:
            db.add(QlibUniverseMember(symbol=symbol, market="CN", catalog_id="a" * 32, status="pending",
                metadata_={"market": "CN", "currency": "CNY", "name": "QA", "lot": 100, "price_limit": .1, "special_treatment": False}))
        state = service.control(db)
        state.settings = {**state.settings, "universe_catalogs": {"CN": "a" * 32}}
        db.commit()
    ident = universe.enqueue("history", "bounded-board-recovery", "test", market="CN")["id"]
    universe.execute(ident)
    with storage() as db:
        job = db.get(QlibUniverseJob, ident)
        assert job.config["history_order"] == "striped_exchange_board_v2"
        if failure == "empty_with_backup_offline":
            assert job.status == "succeeded" and job.cursor == len(codes)
            assert db.query(QlibUniverseMember).filter_by(status="ready").count() == 12
            assert db.query(QlibUniverseMember).filter_by(error_code="qlib_source_history_unavailable").count() == 12
        else:
            assert job.status == "failed" and job.cursor == 0
            assert job.error_code == "qlib_source_unavailable_resume_sync"
    assert {s[:2] for s, _ in calls[:4]} == {"BJ", "SH"}
    if failure == "transport":
        recovered[0] = True
        universe.resume(ident, "test")
        universe.execute(ident)
        with storage() as db:
            job = db.get(QlibUniverseJob, ident)
            assert job.status == "succeeded" and job.cursor == len(codes)
            assert db.query(QlibUniverseMember).filter_by(status="ready").count() == len(codes)
