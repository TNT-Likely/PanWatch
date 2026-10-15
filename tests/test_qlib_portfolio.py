"""Controlled market-pool sizing and actual engine replay, not live alpha proof."""

import copy
import uuid
from datetime import datetime, timezone, timedelta

import pytest

from src.modules.qlib_research import service, paper_plans, automation, api
from src.modules.qlib_research.portfolio import capital_config, pool_values
from src.platform.persistence.models import (
    PaperTradingAccount,
    PaperTradingPosition,
    PaperTradingTrade,
)
from src.platform.persistence.qlib_models import QlibRecord, QlibControl, QlibExecution
from tests.test_qlib_paper_plans import (
    storage,
    selection,
    execution_clock,
    engine_module,
)
from tests.test_qlib_service import storage as qlib_storage
from tests.test_qlib_automation import ready, authorize


def held(db, symbol, quantity, *, managed=True):
    config = service.latest_selection(db.get(QlibControl, 1).champion_id, db).payload[
        "config"
    ]
    p = PaperTradingPosition(
        stock_symbol=symbol[2:],
        stock_market="CN",
        quantity=quantity,
        entry_price=10,
        current_price=10,
        status="open",
        opened_at=datetime(2026, 10, 12, tzinfo=timezone.utc),
        research_meta={
            "buy_outlay": quantity * 10,
            **(
                {"instrument": symbol, "decision_id": "prior", "config": config}
                if managed
                else {}
            ),
        },
    )
    db.add(p)
    db.query(PaperTradingAccount).one().current_capital -= quantity * 10
    db.commit()
    return p.id


def quotes(pairs):
    return {
        ("CN", code): {
            "quote_date": "2026-10-14",
            "open_price": 10,
            "prev_close": 10,
            "current_price": 10,
            "volume": 10000,
        }
        for code, market in pairs
    }


def confirm(ident):
    plan = paper_plans.preview(ident)
    assert plan["ready"], plan
    return plan, paper_plans.confirm(ident, "qa", plan["preview_hash"], True)


def test_pool_counts_existing_holdings_and_does_not_borrow_other_markets(storage):
    ident = selection(storage)
    with storage() as db:
        db.query(PaperTradingAccount).one().market_allocations = {
            "CN": 0.5,
            "HK": 0.3,
            "US": 0.2,
        }
        db.commit()
        held(db, "SH600000", 4000)
    plan = paper_plans.preview(ident)
    assert plan["market_equity"] == 50000
    assert plan["current_exposure"] == 40000
    assert plan["target_exposure"] == 45000
    assert any(
        o["side"] == "sell" and o["estimated_quantity"] < 4000 for o in plan["orders"]
    )
    # Net new capital, not gross rotation, fills only the 5k gap less fees.
    assert -sum(o["estimated_cash_delta"] for o in plan["orders"]) <= 5000
    assert plan["capital"]["market_principal"] == 50000


def test_existing_target_lots_require_no_daily_topup(storage):
    ident = selection(storage)
    with storage() as db:
        held(db, "SH600000", 4400)
        held(db, "SH600001", 4400)
    plan = paper_plans.preview(ident)
    assert not plan["ready"] and plan["blocking_reason"] == "qlib_no_changes_required"
    assert plan["orders"] == [] and plan["cash_budget"] == 0


def test_next_day_same_ranking_does_not_spend_reserve_again(storage, monkeypatch):
    ident = selection(storage)
    confirm(ident)
    execution_clock(monkeypatch)
    engine = engine_module.PaperTradingEngine()
    monkeypatch.setattr(engine, "_fetch_quotes_map", quotes)
    assert engine._scan_sync()["opened"] == 2
    with storage() as db:
        old = db.get(QlibRecord, ident)
        payload = copy.deepcopy(old.payload)
        payload["asof_date"] = "2026-10-14"
        next_id = uuid.uuid4().hex
        db.add(
            QlibRecord(
                id=next_id,
                kind="selection",
                parent_id=old.parent_id,
                name="next close",
                payload=payload,
            )
        )
        db.commit()
        before = db.query(PaperTradingAccount).one().current_capital
    monkeypatch.setattr(
        service, "now", lambda: datetime(2026, 10, 14, 12, tzinfo=timezone.utc)
    )
    plan = paper_plans.preview(next_id)
    assert not plan["ready"] and plan["blocking_reason"] == "qlib_no_changes_required"
    assert plan["cash_budget"] == 0
    with storage() as db:
        assert db.query(PaperTradingAccount).one().current_capital == before


def test_missing_held_valuation_blocks_whole_target_plan(storage):
    ident = selection(storage)
    with storage() as db:
        held(db, "SH600099", 1000, managed=False)
    plan = paper_plans.preview(ident)
    assert (
        not plan["ready"]
        and plan["blocking_reason"] == "qlib_portfolio_valuation_unavailable"
    )


def test_allocator_does_not_clamp_negative_input_cash_into_new_equity():
    from src.modules.qlib_research.allocation import allocate_targets
    from src.modules.qlib_research.contracts import ResearchConfig
    plan = allocate_targets(-1000, {"SH600000": 9000}, {"SH600000"}, {"SH600000": 10},
        [], [], {"SH600000"}, ResearchConfig().model_dump())
    assert plan["market_equity"] == 89000
    assert plan["target_exposure"] == 80100
    assert any(order["side"] == "sell" for order in plan["orders"])


def test_legacy_exposure_consumes_pool_but_no_takeover(storage):
    ident = selection(storage)
    with storage() as db:
        held(db, "SH600002", 8000, managed=False)
    plan = paper_plans.preview(ident)
    assert plan["target_exposure"] == 90000
    assert plan["cash_budget"] <= 10000
    assert all(o["symbol"] != "SH600002" for o in plan["orders"])


def test_actual_engine_partial_reduce_preserves_cost_and_market_cash(
    storage, monkeypatch
):
    ident = selection(storage)
    with storage() as db:
        held_id = held(db, "SH600000", 8000)
    plan, decision = confirm(ident)
    execution_clock(monkeypatch)
    engine = engine_module.PaperTradingEngine()
    monkeypatch.setattr(engine, "_fetch_quotes_map", quotes)
    result = engine._scan_sync()
    assert result["closed"] == 1 and result["opened"] == 1
    with storage() as db:
        remaining = db.get(PaperTradingPosition, held_id)
        trade = db.query(PaperTradingTrade).one()
        assert (
            remaining.status == "open" and remaining.quantity + trade.quantity == 8000
        )
        assert (
            remaining.research_meta["buy_outlay"] + trade.meta["research"]["buy_outlay"]
            == 80000
        )
        positions = db.query(PaperTradingPosition).filter_by(status="open").all()
        pool = pool_values(
            db,
            db.query(PaperTradingAccount).one(),
            positions,
            {"SH600000": 10, "SH600001": 10},
        )
        assert (
            pool["exposure"] <= pool["target_exposure"] + 0.01 and pool["cash"] >= 9000
        )
        assert sum(p.quantity * 10 for p in positions) + db.query(
            PaperTradingAccount
        ).one().current_capital == pytest.approx(
            100000 - sum(o["estimated_fee"] for o in plan["orders"]), abs=1
        )
    assert engine._scan_sync()["opened"] == 0


def test_actual_engine_topup_keeps_one_position_and_lineage(storage, monkeypatch):
    ident = selection(storage)
    with storage() as db:
        held_id = held(db, "SH600000", 1000)
    plan, decision = confirm(ident)
    execution_clock(monkeypatch)
    engine = engine_module.PaperTradingEngine()
    monkeypatch.setattr(engine, "_fetch_quotes_map", quotes)
    assert engine._scan_sync()["opened"] == 2
    with storage() as db:
        positions = db.query(PaperTradingPosition).filter_by(status="open").all()
        assert len(positions) == 2
        p = db.get(PaperTradingPosition, held_id)
        assert p.quantity > 1000 and p.research_meta["entry_decisions"] == [
            "prior",
            decision["id"],
        ]
        assert (
            engine_module.sell_block_reason(p, now=engine_module._utc_now())
            == "paper_trading_t1_locked"
        )
        assert p.research_meta["buy_outlay"] > 10000


def test_rejected_sale_never_releases_capital_and_quote_failure_no_buys(
    storage, monkeypatch
):
    ident = selection(storage)
    with storage() as db:
        held(db, "SH600000", 9000)
    confirm(ident)
    execution_clock(monkeypatch)
    engine = engine_module.PaperTradingEngine()

    def failed_sale(pairs):
        data = quotes(pairs)
        data[("CN", "600000")]["volume"] = 0
        return data

    monkeypatch.setattr(engine, "_fetch_quotes_map", failed_sale)
    assert engine._scan_sync()["opened"] == 0
    with storage() as db:
        assert db.query(PaperTradingTrade).count() == 0
        assert db.query(PaperTradingPosition).filter_by(status="open").count() == 1
        assert all(e.status == "rejected" for e in db.query(QlibExecution))


def test_changed_any_holding_or_fund_config_rejects_frozen_plan(storage, monkeypatch):
    ident = selection(storage)
    confirm(ident)
    with storage() as db:
        db.query(PaperTradingAccount).one().market_allocations = {
            "CN": 0.5,
            "HK": 0.5,
            "US": 0,
        }
        db.commit()
    execution_clock(monkeypatch)
    engine = engine_module.PaperTradingEngine()
    monkeypatch.setattr(engine, "_fetch_quotes_map", quotes)
    assert engine._scan_sync()["opened"] == 0
    with storage() as db:
        assert {e.reason for e in db.query(QlibExecution)} == {
            "qlib_portfolio_state_changed"
        }


def test_daily_budget_client_and_old_grant_cannot_expand_authority(storage):
    ident = selection(storage)
    with pytest.raises(ValueError):
        api.AutomationInput(max_buy_cash=20000)
    with storage() as db:
        state = db.get(QlibControl, 1)
        grant_id = uuid.uuid4().hex
        db.add(
            QlibRecord(
                id=grant_id,
                kind="paper_authorization",
                name="old cap grant",
                payload={"max_buy_cash": 20000},
            )
        )
        state.settings = {
            **state.settings,
            "paper_automation": {"mode": "automatic", "grant_id": grant_id},
        }
        db.commit()
        assert (
            automation.grant_error(db, grant_id)
            == "qlib_automation_capital_policy_changed"
        )


def test_config_hash_prevents_consent_to_changed_funds(storage):
    selection(storage)
    with storage() as db:
        account = db.query(PaperTradingAccount).one()
        first = capital_config(account)
        account.market_allocations = {"CN": 0.5, "HK": 0.5, "US": 0}
        assert (
            capital_config(account)["capital_config_hash"]
            != first["capital_config_hash"]
        )


def test_consent_rejects_a_configuration_changed_while_dialog_open(storage, ready):
    _, model_id = ready
    body = authorize(model_id)
    with storage() as db:
        db.query(PaperTradingAccount).one().market_allocations = {
            "CN": 0.5,
            "HK": 0.5,
            "US": 0,
        }
        db.commit()
    with pytest.raises(ValueError, match="funds_changed"):
        automation.configure(body, "qa")
    assert not automation.status()["authorized"]


def test_zero_cn_allocation_cannot_authorize(storage, ready):
    _, model_id = ready
    with storage() as db:
        db.query(PaperTradingAccount).one().market_allocations = {
            "CN": 0,
            "HK": 0.5,
            "US": 0.5,
        }
        db.commit()
    with pytest.raises(ValueError, match="market_unallocated"):
        automation.configure(authorize(model_id), "qa")


def test_missing_runtime_legacy_price_blocks_buys_not_uses_old_mark(
    storage, monkeypatch
):
    ident = selection(storage)
    with storage() as db:
        held(db, "SH600002", 1000, managed=False)
    confirm(ident)
    execution_clock(monkeypatch)
    engine = engine_module.PaperTradingEngine()
    monkeypatch.setattr(
        engine,
        "_fetch_quotes_map",
        lambda pairs: {k: v for k, v in quotes(pairs).items() if k[1] != "600002"},
    )
    assert engine._scan_sync()["opened"] == 0
    with storage() as db:
        assert {e.reason for e in db.query(QlibExecution)} == {
            "qlib_portfolio_valuation_unavailable"
        }


def test_price_move_and_rejection_never_donate_frozen_item_cap(storage, monkeypatch):
    ident = selection(storage)
    plan, _ = confirm(ident)
    execution_clock(monkeypatch)
    engine = engine_module.PaperTradingEngine()

    def moving(pairs):
        data = quotes(pairs)
        if ("CN", "600000") in data:
            data[("CN", "600000")]["volume"] = 0
        if ("CN", "600001") in data:
            data[("CN", "600001")]["open_price"] = 9.5
        return data

    monkeypatch.setattr(engine, "_fetch_quotes_map", moving)
    assert engine._scan_sync()["opened"] == 1
    with storage() as db:
        p = db.query(PaperTradingPosition).one()
        order = next(o for o in plan["orders"] if o["symbol"] == "SH600001")
        assert p.quantity <= order["estimated_quantity"]
        assert p.research_meta["buy_outlay"] <= order["cash_limit"]
