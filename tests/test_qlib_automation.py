"""Controlled authorization/scheduler/engine replay, not live financial evidence."""

import asyncio
import copy
import uuid
from datetime import timedelta
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.modules.qlib_research import (
    api,
    automation,
    service,
    paper_plans,
    universe,
    opportunity_context,
)
from src.platform.persistence.models import PaperTradingAccount, PaperTradingPosition
from src.platform.persistence.qlib_models import (
    QlibControl,
    QlibRecord,
    QlibUniverseJob,
)
from tests.test_qlib_paper_plans import (
    storage,
    selection,
    execution_clock,
    engine_module,
)
from tests.test_qlib_service import storage as qlib_storage


@pytest.fixture
def ready(storage, monkeypatch):
    ident = selection(storage)
    with storage() as db:
        state = db.get(QlibControl, 1)
        state.settings = {
            **state.settings,
            "universe_generation": "controlled-generation",
            "universe_config": {"license_acknowledged": True, "automatic": False},
        }
        model_id = state.champion_id
        db.commit()
    monkeypatch.setattr(universe, "scheduled_tick", lambda: {"status": "idle"})
    monkeypatch.setattr(
        opportunity_context,
        "enrich",
        lambda payload, rows: [
            {**r, "observed_context": {"return_20_observations": 0.05}} for r in rows
        ],
    )
    return ident, model_id


def authorize(model_id, **overrides):
    return api.AutomationInput(
        mode="automatic",
        model_id=model_id,
        capital_config_hash=automation.status()["capital"]["capital_config_hash"],
        acknowledge_research=True,
        request_key=uuid.uuid4().hex,
        **overrides
    )


def source_failure(storage):
    with storage() as db:
        db.add(
            QlibUniverseJob(
                id=uuid.uuid4().hex,
                request_key=uuid.uuid4().hex,
                kind="history",
                market="CN",
                config={},
                status="failed",
                error_code="qlib_source_unavailable_resume_sync",
            )
        )
        db.commit()


def test_default_automatic_is_not_implicit_upgrade_or_read_side_effect(storage, ready):
    assert automation.status()["mode"] == "automatic"
    assert not automation.status()["authorized"]
    assert automation.tick()["phase"] == "needs_authorization"
    with storage() as db:
        assert db.query(QlibRecord).filter_by(kind="paper_authorization").count() == 0
        assert db.query(QlibRecord).filter_by(kind="decision").count() == 0


def test_one_time_grant_is_idempotent_and_creates_one_bounded_daily_plan(
    storage, ready
):
    ident, model_id = ready
    body = authorize(model_id)
    first = automation.configure(body, "qa")
    assert first["authorized"] and first["account_enabled"]
    assert automation.configure(body, "qa")["grant_id"] == first["grant_id"]
    a, b = automation.tick(), automation.tick()
    assert a["decision_id"] == b["decision_id"]
    decision = service.get_record(a["decision_id"], "decision")
    assert (
        decision["approval_origin"] == "automatic"
        and decision["automation_grant_id"] == first["grant_id"]
    )
    assert decision["cash_budget"] == 90000
    assert sum(o["cash_limit"] for o in decision["orders"]) == 90000
    with pytest.raises(ValueError, match="manual_confirmation_disabled"):
        paper_plans.confirm(ident, "qa", acknowledge_research=True)
    with storage() as db:
        assert db.query(QlibRecord).filter_by(kind="decision").count() == 1
        row = db.get(QlibRecord, ident)
        another = uuid.uuid4().hex
        db.add(
            QlibRecord(
                id=another,
                kind="selection",
                parent_id=row.parent_id,
                name="same day replay",
                payload=copy.deepcopy(row.payload),
            )
        )
        db.commit()
    assert automation.tick()["decision_id"] == a["decision_id"]
    paper_plans.cancel(a["decision_id"], "qa")
    assert automation.tick()["decision_id"] == a["decision_id"]
    assert paper_plans.list_plans()["items"][0]["status"] == "cancelled"


@pytest.mark.parametrize(
    "change,expected",
    [
        ("paused", "account_paused"),
        ("funds", "funds_changed"),
        ("model", "model_changed"),
        ("source", "source_configuration_changed"),
        ("expired", "authorization_expired"),
    ],
)
def test_authorization_revalidated_on_every_tick(
    storage, ready, monkeypatch, change, expected
):
    ident, model_id = ready
    result = automation.configure(authorize(model_id), "qa")
    if change == "expired":
        stamp = service.now() + timedelta(days=31)
        monkeypatch.setattr(service, "now", lambda: stamp)
    else:
        with storage() as db:
            account, state = db.query(PaperTradingAccount).one(), db.get(QlibControl, 1)
            if change == "paused":
                account.enabled = False
            if change == "funds":
                account.initial_capital += 100
            if change == "model":
                state.champion_id = uuid.uuid4().hex
            if change == "source":
                state.settings = {**state.settings, "universe_generation": "new"}
            db.commit()
    assert expected in automation.tick()["reason"]
    assert service.list_records("decision") == []


def test_source_failure_stops_plans_and_execution_even_after_confirmation(
    storage, ready, monkeypatch
):
    ident, model_id = ready
    automation.configure(authorize(model_id), "qa")
    decision = automation.tick()["decision_id"]
    source_failure(storage)
    assert "source_failed" in automation.tick()["reason"]
    execution_clock(monkeypatch)
    engine = engine_module.PaperTradingEngine()
    monkeypatch.setattr(
        engine,
        "_fetch_quotes_map",
        lambda pairs: (
            {}
            if not pairs
            else pytest.fail("bad source must stop before fetching execution quotes")
        ),
    )
    assert engine._scan_sync()["opened"] == 0
    assert paper_plans.list_plans()["items"][0]["status"] == "automation_blocked"
    assert len(service.list_records("decision")) == 1


def test_missing_or_anomalous_frozen_inputs_never_auto_trade(
    storage, ready, monkeypatch
):
    _, model_id = ready
    automation.configure(authorize(model_id), "qa")
    monkeypatch.setattr(
        opportunity_context,
        "enrich",
        lambda payload, rows: [{**r, "observed_context": None} for r in rows],
    )
    assert "inputs_unverified" in automation.tick()["reason"]
    monkeypatch.setattr(
        opportunity_context,
        "enrich",
        lambda payload, rows: [
            {**r, "observed_context": {"return_20_observations": 8.12}} for r in rows
        ],
    )
    assert "input_anomaly" in automation.tick()["reason"]
    assert service.list_records("decision") == []


def test_automatic_actual_engine_replay_fills_only_frozen_orders_and_budget(
    storage, ready, monkeypatch
):
    _, model_id = ready
    automation.configure(authorize(model_id), "qa")
    decision = automation.tick()["decision_id"]
    execution_clock(monkeypatch)
    engine = engine_module.PaperTradingEngine()
    monkeypatch.setattr(
        engine,
        "_fetch_quotes_map",
        lambda pairs: {
            ("CN", code): {
                "quote_date": "2026-10-14",
                "open_price": 10,
                "prev_close": 10,
                "volume": 0 if code == "600000" else 10000,
                "current_price": 10,
            }
            for code, market in pairs
        },
    )
    assert engine._scan_sync()["opened"] == 1
    with storage() as db:
        position = db.query(PaperTradingPosition).one()
        assert position.stock_symbol == "600001"
        assert position.research_meta["approval_origin"] == "automatic"
        assert position.research_meta["buy_outlay"] <= 45000
    assert engine._scan_sync()["opened"] == 0
    assert paper_plans.list_plans()["items"][0]["status"] == "partially_filled"


def test_revoke_cancels_pending_preserves_holdings_and_old_request_cannot_restore(
    storage, ready
):
    _, model_id = ready
    body = authorize(model_id)
    automation.configure(body, "qa")
    automation.tick()
    automation.configure(api.AutomationInput(mode="manual"), "qa")
    assert automation.status()["mode"] == "manual"
    assert paper_plans.list_plans()["items"][0]["status"] == "cancelled"
    with pytest.raises(ValueError, match="authorization_conflict"):
        automation.configure(body, "qa")
    with storage() as db:
        assert not db.query(PaperTradingAccount).one().enabled


def test_actual_authorization_protocol_auth_budget_and_consent(storage, ready):
    _, model_id = ready
    app = FastAPI()
    app.include_router(api.router, prefix="/qlib")
    app.dependency_overrides[api.current_actor] = lambda: None
    with TestClient(app) as client:
        assert client.get("/qlib/paper-automation").status_code == 401
    app.dependency_overrides[api.current_actor] = lambda: "qa"
    with TestClient(app) as client:
        body = authorize(model_id).model_dump()
        assert (
            client.put(
                "/qlib/paper-automation", json={**body, "acknowledge_research": False}
            ).status_code
            == 422
        )
        assert (
            client.put(
                "/qlib/paper-automation", json={**body, "capital_config_hash": "f" * 64}
            ).status_code
            == 422
        )
        assert (
            client.put(
                "/qlib/paper-automation", json={**body, "orders": []}
            ).status_code
            == 422
        )
        assert client.put("/qlib/paper-automation", json=body).status_code == 200
        assert client.get("/qlib/paper-automation").json()["authorized"]


def test_stop_keeps_automatic_preference_without_active_authority(storage, ready):
    _, model_id = ready
    automation.configure(authorize(model_id), "qa")
    automation.tick()
    result = automation.configure(
        api.AutomationInput(mode="automatic", enabled=False), "qa"
    )
    assert result["mode"] == "automatic" and not result["authorized"]
    assert not result["account_enabled"]
    assert paper_plans.list_plans()["items"][0]["status"] == "cancelled"


def test_scheduler_plans_even_when_all_exchanges_closed(monkeypatch):
    from src.modules.paper_trading import paper_trading_scheduler as scheduler_module

    calls = []
    monkeypatch.setattr(automation, "tick", lambda: calls.append("plan"))
    monkeypatch.setattr(scheduler_module, "_any_market_trading", lambda: False)
    monkeypatch.setattr(scheduler_module.ENGINE, "scan_once", AsyncMock())
    asyncio.run(scheduler_module.PaperTradingScheduler()._scan_job())
    assert calls == ["plan"]
    scheduler_module.ENGINE.scan_once.assert_not_called()
