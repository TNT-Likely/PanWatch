"""Controlled protocol/ledger replay, not observed next-open market fills."""

from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import copy
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.modules.qlib_research import api, service, paper_plans
from src.modules.qlib_research.contracts import ResearchConfig
from src.modules.paper_trading import paper_trading_engine as engine_module
from src.platform.persistence.models import (
    PaperTradingAccount,
    PaperTradingPosition,
    PaperTradingTrade,
)
from src.platform.persistence.qlib_models import QlibRecord, QlibAudit, QlibExecution
from tests.test_qlib_service import storage as qlib_storage, model


@pytest.fixture
def storage(qlib_storage, monkeypatch):
    monkeypatch.setattr(engine_module, "SessionLocal", qlib_storage)
    return qlib_storage


def selection(storage, **overrides):
    candidate = model(storage, data_kind="market")
    service.publish(candidate, 0, "Controlled research only", "research", "qa")
    ident = uuid.uuid4().hex
    rows = [
        {
            "symbol": "SH600000",
            "market": "CN",
            "rank": 1,
            "raw_score": 3,
            "tradable": True,
            "reference_close": 10,
            "paper_eligible": True,
        },
        {
            "symbol": "SH688001",
            "market": "CN",
            "rank": 2,
            "raw_score": 2,
            "tradable": True,
            "reference_close": 10,
            "paper_eligible": False,
        },
        {
            "symbol": "SH600001",
            "market": "CN",
            "rank": 3,
            "raw_score": 1,
            "tradable": True,
            "reference_close": 10,
            "paper_eligible": True,
        },
        {
            "symbol": "SH600002",
            "market": "CN",
            "rank": 4,
            "raw_score": 0,
            "tradable": True,
            "reference_close": 10,
            "paper_eligible": True,
        },
    ]
    payload = {
        "model_id": candidate,
        "dataset_id": "c" * 32,
        "market": "CN",
        "data_kind": "market",
        "asof_date": "2026-10-13",
        "price_basis": "raw_execution",
        "monitor": {"fraction": 1},
        "predictions": rows,
        "config": ResearchConfig(topk=2).model_dump(),
        "names": {"SH600000": "QA one"},
        **overrides,
    }
    with storage() as db:
        db.add(
            QlibRecord(
                id=ident,
                kind="selection",
                parent_id=candidate,
                name="controlled",
                payload=payload,
            )
        )
        db.add(
            PaperTradingAccount(
                initial_capital=100000,
                current_capital=100000,
                enabled=True,
                market_allocations={"CN": 1, "HK": 0, "US": 0},
            )
        )
        db.commit()
    return ident


def execution_clock(monkeypatch):
    instant = datetime(2026, 10, 14, 9, 31, tzinfo=ZoneInfo("Asia/Shanghai"))
    monkeypatch.setattr(
        engine_module, "_utc_now", lambda: instant.astimezone(timezone.utc)
    )
    monkeypatch.setattr(service, "now", lambda: instant.astimezone(timezone.utc))
    monkeypatch.setattr(engine_module, "_is_trading_time", lambda market: True)
    monkeypatch.setattr(
        engine_module,
        "quote_date_is_current",
        lambda market, quote: quote.get("quote_date") == "2026-10-14",
    )


def test_preview_reads_without_creating_decision_or_changing_cash(storage):
    ident = selection(storage)
    preview = paper_plans.preview(ident)
    assert preview["ready"] and len(preview["preview_hash"]) == 64
    assert [o["symbol"] for o in preview["orders"]] == ["SH600000", "SH600001"]
    assert preview["cash_budget"] == 90000 and preview["estimated_fees"] > 0
    assert preview["excluded"][0]["symbol"] == "SH688001"
    with storage() as db:
        assert db.query(QlibRecord).filter_by(kind="decision").count() == 0
        assert db.query(PaperTradingAccount).one().current_capital == 100000
        assert db.query(PaperTradingPosition).count() == 0


@pytest.mark.parametrize("change", ["cash", "holding", "allocation", "paused"])
def test_changed_account_preview_cannot_be_confirmed(storage, change):
    ident = selection(storage)
    before = paper_plans.preview(ident)
    with storage() as db:
        account = db.query(PaperTradingAccount).one()
        if change == "cash":
            account.current_capital -= 100
        if change == "allocation":
            account.market_allocations = {"CN": 0.5, "HK": 0.5, "US": 0}
        if change == "paused":
            account.enabled = False
        if change == "holding":
            db.add(
                PaperTradingPosition(
                    stock_symbol="600003", quantity=100, entry_price=10, status="open"
                )
            )
        db.commit()
    with pytest.raises(ValueError, match="preview_changed_conflict"):
        paper_plans.confirm(ident, "qa", before["preview_hash"], True)
    assert service.list_records("decision") == []


def test_manual_confirmation_idempotency_cancellation_and_restart_read(storage):
    ident = selection(storage)
    before = paper_plans.preview(ident)
    with pytest.raises(ValueError, match="research_ack_required"):
        paper_plans.confirm(ident, "qa", before["preview_hash"], False)
    first = paper_plans.confirm(ident, "qa", before["preview_hash"], True)
    assert (
        paper_plans.confirm(ident, "qa", before["preview_hash"], True)["id"]
        == first["id"]
    )
    assert first["confirmation_kind"] == "explicit_frozen_orders_v1"
    assert paper_plans.list_plans()["items"][0]["status"] == "waiting_open"
    original = copy.deepcopy(service.get_record(first["id"], "decision"))
    paper_plans.cancel(first["id"], "qa")
    paper_plans.cancel(first["id"], "qa")
    assert paper_plans.list_plans()["items"][0]["status"] == "cancelled"
    assert service.get_record(first["id"], "decision") == original
    with storage() as db:
        assert db.query(QlibAudit).filter_by(action="decision_created").count() == 1
        assert db.query(QlibAudit).filter_by(action="decision_cancelled").count() == 1
        assert db.query(QlibRecord).filter_by(kind="decision_cancel").count() == 1


@pytest.mark.parametrize(
    "overrides,reason",
    [
        ({"market": "HK"}, "qlib_market_execution_not_supported"),
        ({"price_basis": "provider_snapshot"}, "qlib_unverified_execution_prices"),
        ({"data_kind": "synthetic"}, "qlib_synthetic_execution_not_supported"),
        (
            {"selection_scope": "training_cohort"},
            "qlib_training_cohort_not_full_prediction",
        ),
        ({"asof_date": "2026-09-30"}, "qlib_prediction_stale"),
        ({"monitor": {"fraction": 0.5}}, "qlib_prediction_coverage_insufficient"),
    ],
)
def test_preview_reports_unsupported_goal_not_fake_success(storage, overrides, reason):
    ident = selection(storage, **overrides)
    preview = paper_plans.preview(ident)
    assert not preview["ready"] and preview["blocking_reason"] == reason
    assert service.list_records("decision") == []


def test_actual_engine_only_executes_confirmed_orders_without_substitution(
    storage, monkeypatch
):
    ident = selection(storage)
    preview = paper_plans.preview(ident)
    decision = paper_plans.confirm(ident, "qa", preview["preview_hash"], True)
    execution_clock(monkeypatch)
    engine = engine_module.PaperTradingEngine()
    fetched = []

    def quotes(pairs):
        fetched.extend(pairs)
        return {
            ("CN", code): {
                "quote_date": "2026-10-14",
                "open_price": 10,
                "prev_close": 10,
                "volume": 0 if code == "600000" else 10000,
                "current_price": 10,
            }
            for code, market in pairs
        }

    monkeypatch.setattr(engine, "_fetch_quotes_map", quotes)
    result = engine._scan_sync()
    assert result["opened"] == 1
    assert not any(code in ("688001", "600002") for code, market in fetched)
    with storage() as db:
        position = db.query(PaperTradingPosition).one()
        assert position.stock_symbol == "600001" and position.quantity % 100 == 0
        cost = position.research_meta["buy_outlay"]
        assert cost <= preview["orders"][1]["cash_limit"]
        assert db.query(PaperTradingAccount).one().current_capital == pytest.approx(
            100000 - cost
        )
        events = db.query(QlibExecution).filter_by(decision_id=decision["id"]).all()
        assert {(e.symbol, e.status) for e in events} == {
            ("SH600000", "rejected"),
            ("SH600001", "filled"),
        }
    assert paper_plans.list_plans()["items"][0]["status"] == "partially_filled"
    assert engine._scan_sync()["opened"] == 0
    with pytest.raises(ValueError, match="already_executed_conflict"):
        paper_plans.cancel(decision["id"], "qa")


def test_cancelled_plan_never_resumes_at_open(storage, monkeypatch):
    ident = selection(storage)
    preview = paper_plans.preview(ident)
    decision = paper_plans.confirm(ident, "qa", preview["preview_hash"], True)
    paper_plans.cancel(decision["id"], "qa")
    execution_clock(monkeypatch)
    engine = engine_module.PaperTradingEngine()
    monkeypatch.setattr(engine, "_fetch_quotes_map", lambda pairs: {})
    assert engine._scan_sync()["opened"] == 0
    with storage() as db:
        assert db.query(QlibExecution).count() == 0
        assert db.query(PaperTradingPosition).count() == 0


def test_actual_authenticated_plan_protocol_rejects_custom_orders(storage):
    ident = selection(storage)
    app = FastAPI()
    app.include_router(api.router, prefix="/qlib")
    app.dependency_overrides[api.current_actor] = lambda: None
    with TestClient(app) as client:
        assert client.get(f"/qlib/selections/{ident}/plan").status_code == 401
    app.dependency_overrides[api.current_actor] = lambda: "qa"
    with TestClient(app) as client:
        preview = client.get(f"/qlib/selections/{ident}/plan").json()
        body = {"preview_hash": preview["preview_hash"], "acknowledge_research": True}
        assert (
            client.post(
                f"/qlib/selections/{ident}/decision",
                json={"acknowledge_research": True},
            ).status_code
            == 422
        )
        assert (
            client.post(
                f"/qlib/selections/{ident}/plan", json={**body, "orders": []}
            ).status_code
            == 422
        )
        assert (
            client.post(f"/qlib/selections/{ident}/plan", json=body).status_code == 200
        )
        assert (
            client.get("/qlib/paper-plans").json()["items"][0]["status"]
            == "waiting_open"
        )
