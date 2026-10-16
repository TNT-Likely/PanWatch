"""Rule semantics and explicit paper-experiment authority, not alpha claims."""

import copy
import pytest

from src.modules.qlib_research import assessment, automation, service
from src.modules.qlib_research.allocation import target_membership
from src.modules.qlib_research.rules import VERSION, TEMPLATES, evaluate
from src.modules.qlib_research import universe, strategies
from src.platform.persistence.qlib_models import QlibControl, QlibRecord
from src.platform.persistence.qlib_models import QlibUniverseMember
from src.platform.persistence.models import PaperTradingPosition
from tests.qlib_fixture import panel
from datetime import date
from tests.test_qlib_paper_plans import execution_clock, engine_module
from tests.test_qlib_automation import ready, authorize, source_failure
from tests.test_qlib_paper_plans import storage
from tests.test_qlib_service import storage as qlib_storage


def bars():
    return [
        dict(
            close=100 + i * 0.4,
            open=100 + i * 0.4,
            high=(100 + i * 0.4) * 1.001,
            low=(100 + i * 0.4) * 0.999,
            volume=200 if i == 60 else 100,
            factor=1,
        )
        for i in range(61)
    ]


@pytest.mark.parametrize("algorithm", TEMPLATES)
def test_fixed_causal_rule_history_and_anomaly(algorithm):
    rows = bars()
    assert not evaluate(rows[:60], algorithm)["active"]
    result = evaluate(rows, algorithm)
    assert result["rule_version"] == VERSION
    assert result["active"] == all(result["conditions"].values())
    assert result["active"] == (result["score"] > 0)
    changed = copy.deepcopy(rows)
    changed[-1]["factor"] = 20
    assert evaluate(changed, algorithm)["issue"] == "qlib_automation_input_anomaly"
    assert evaluate(rows, algorithm) == result


def test_breakout_and_strength_match_but_oversold_does_not():
    assert evaluate(bars(), "trend_breakout")["active"]
    assert evaluate(bars(), "strength_continuation")["active"]
    assert not evaluate(bars(), "oversold_repair")["active"]
    assert evaluate(bars(), "trend_breakout")["values"]["volume_ratio"] == 2


def test_display_levels_use_latest_price_scale_without_changing_signals():
    original = evaluate(bars(), "trend_breakout")
    adjusted = bars()
    for row in adjusted:
        row["factor"] = 28.0
    result = evaluate(adjusted, "trend_breakout")
    assert result["conditions"] == original["conditions"]
    assert result["score"] == pytest.approx(original["score"])
    assert result["values"]["close"] == pytest.approx(adjusted[-1]["close"])
    assert result["values"]["ma20"] == pytest.approx(original["values"]["ma20"])


def test_fractional_adjusted_holdings_never_round_a_sale_above_position():
    from src.modules.qlib_research.target_backtest import bounded_lot_amount

    # Actual market replay regression: Qlib adds 0.1 raw shares before
    # rounding, turning an available 99.95 raw shares into a sale of 100.
    amount, factor = 1759.6862620455795, 100 / 1760.537850663279
    assert bounded_lot_amount(amount, factor, 100) == 0
    assert bounded_lot_amount(100 / factor, factor, 100) == pytest.approx(100 / factor)
    for lot in (1, 100, 500):
        for quantity in (0, 99.95, 100, 100.001, 999.999999999):
            assert (
                bounded_lot_amount(quantity / factor, factor, lot) <= quantity / factor
            )


def test_no_signal_means_exit_not_fill_target_with_nonmatches():
    rows = [
        dict(symbol="A", rank=1, signal_active=False),
        dict(symbol="B", rank=2, signal_active=True),
    ]
    assert target_membership(rows, {"A"}, {"A", "B"}, 10) == (["A"], ["B"])
    assert target_membership(rows[:1], {"A"}, {"A"}, 10) == (["A"], [])


def test_oversold_requires_actual_upward_cross_and_not_merely_falling():
    rows = bars()
    for i, row in enumerate(rows):
        price = 120 if i < 41 else 110 - (i - 41) * 1.0
        row.update(open=price, close=price, high=price * 1.001, low=price * 0.999)
    rows[-1].update(open=95, close=95, high=95.1, low=94.9)
    result = evaluate(rows, "oversold_repair")
    assert result["active"]
    rows[-1].update(open=90, close=90, high=90.1, low=89.9)
    assert not evaluate(rows, "oversold_repair")["active"]


def convert_rule(storage, model_id):
    with storage() as db:
        row = db.get(QlibRecord, model_id)
        payload = copy.deepcopy(row.payload)
        payload.update(
            rule_version=VERSION,
            feature_set=VERSION,
            runtime={"pyqlib": "0.9.7"},
            adjustment="snapshot_adjusted",
            universe_kind="fixed_research",
            experiment_id="f" * 32,
        )
        payload["config"]["algorithm"] = "strength_continuation"
        payload["metrics"]["backtest"].update(days=60, **{"return": -0.2})
        for report in [payload["baseline"]] + [
            f["metrics"] for f in payload["rolling"]
        ]:
            report["backtest"].update(days=60, **{"return": -0.1, "max_drawdown": 0.2})
        row.payload = payload
        state = db.get(QlibControl, 1)
        state.settings = {**state.settings, "champion_modes": {"CN": "research"}}
        db.commit()
    return payload


def rule_pool(storage):
    """Controlled synthetic history stored by the actual immutable cache path."""
    data = panel(end=date(2026, 10, 13), symbols=3)
    groups = {}
    for bar in data["bars"]:
        i = data["calendar"].index(bar["date"])
        price = 10 * 1.003**i
        bar.update(
            open=price,
            close=price,
            high=price * 1.001,
            low=price * 0.999,
            volume=1000000,
            factor=1,
        )
        groups.setdefault(bar["symbol"], []).append(bar)
    catalog_id = "e" * 32
    with storage() as db:
        db.add(
            QlibRecord(
                id=catalog_id,
                kind="catalog",
                name="controlled rules pool",
                payload={"total": 3},
            )
        )
        for symbol, bars in groups.items():
            ident, _ = universe.write_immutable(
                "history", {"symbol": symbol, "bars": bars}
            )
            db.add(
                QlibUniverseMember(
                    symbol=symbol,
                    market="CN",
                    catalog_id=catalog_id,
                    status="ready",
                    asof_date="2026-10-13",
                    history_id=ident,
                    metadata_={
                        "symbol": symbol,
                        "market": "CN",
                        "board": "MAIN",
                        "name": symbol,
                        "currency": "CNY",
                        "price_limit": 0.1,
                        "special_treatment": False,
                        "lot": 100,
                    },
                )
            )
        state = db.get(QlibControl, 1)
        state.settings = {
            **state.settings,
            "universe_catalogs": {"CN": catalog_id},
            "universe_calendars": {"CN": data["calendar"]},
            "universe_config": {
                **state.settings["universe_config"],
                "license": "Controlled synthetic QA only",
            },
        }
        db.commit()
    return data["bars"][-1]["close"]


def test_paper_experiment_explicitly_allows_negative_results_not_validated_release(
    storage, ready
):
    ident, model_id = ready
    payload = convert_rule(storage, model_id)
    rule_pool(storage)
    assert assessment.experiment_error(payload) == ""
    assert not assessment.assess(payload, "research")["eligible"]
    assert automation.status()["authorization_kind"] == "paper_experiment"
    with pytest.raises(ValueError):
        automation.configure(authorize(model_id), "qa")
    with pytest.raises(ValueError, match="experiment_ack_required"):
        automation.configure(
            authorize(model_id, authorization_kind="paper_experiment"), "qa"
        )
    body = authorize(
        model_id, authorization_kind="paper_experiment", acknowledge_experiment=True
    )
    grant = automation.configure(body, "qa")
    assert grant["authorized"] and grant["authorization_kind"] == "paper_experiment"
    source_failure(storage)
    assert (
        automation.tick()["phase"] == "planned"
    )  # Global scan != frozen order input health.
    with storage() as db:
        assert (
            db.get(QlibRecord, grant["grant_id"]).payload["authorization_kind"]
            == "paper_experiment"
        )
        assert service.market_mode(db.get(QlibControl, 1)) == "research"


def test_fixed_rule_snapshot_to_automatic_plan_and_real_paper_engine(
    storage, ready, monkeypatch
):
    _, model_id = ready
    convert_rule(storage, model_id)
    price = rule_pool(storage)
    first = strategies.refresh(model_id, "controlled-qa")
    assert len(first["predictions"]) == 3 and all(
        r["signal_active"] for r in first["predictions"]
    )
    assert strategies.refresh(model_id, "controlled-qa")["id"] == first["id"]
    grant = automation.configure(
        authorize(
            model_id, authorization_kind="paper_experiment", acknowledge_experiment=True
        ),
        "qa",
    )
    plan = automation.tick()
    assert (
        plan["phase"] == "planned"
        and automation.tick()["decision_id"] == plan["decision_id"]
    )
    decision = service.get_record(plan["decision_id"], "decision")
    assert (
        decision["authorization_kind"] == "paper_experiment"
        and decision["run_id"] == first["id"]
    )
    execution_clock(monkeypatch)
    engine = engine_module.PaperTradingEngine()
    monkeypatch.setattr(
        engine,
        "_fetch_quotes_map",
        lambda pairs: {
            ("CN", code): {
                "quote_date": "2026-10-14",
                "open_price": price,
                "prev_close": price,
                "volume": 1000000,
                "current_price": price,
            }
            for code, market in pairs
        },
    )
    assert engine._scan_sync()["opened"] > 0
    assert engine._scan_sync()["opened"] == 0
    with storage() as db:
        assert db.query(PaperTradingPosition).count() > 0
        assert (
            db.get(QlibRecord, grant["grant_id"]).payload["authorization_kind"]
            == "paper_experiment"
        )


@pytest.mark.parametrize(
    "mutation", ["version", "synthetic", "missing_replay", "unregistered", "nan"]
)
def test_paper_experiment_cannot_skip_structural_evidence(storage, ready, mutation):
    _, model_id = ready
    payload = convert_rule(storage, model_id)
    if mutation == "version":
        payload["rule_version"] = "old"
    if mutation == "synthetic":
        payload["data_kind"] = "synthetic"
    if mutation == "missing_replay":
        payload["rolling"] = []
    if mutation == "unregistered":
        payload["config"]["algorithm"] = "ridge"
    if mutation == "nan":
        payload["metrics"]["backtest"]["return"] = float("nan")
    assert assessment.experiment_error(payload)
