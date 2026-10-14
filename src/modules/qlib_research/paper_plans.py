"""Read-only portfolio previews and explicitly confirmed immutable paper plans.

Reference-close estimates are not orders or fill promises. The confirmation
token binds the user's preview to current account, holdings and model state.
"""

from datetime import datetime, timezone
import hashlib
import math
from zoneinfo import ZoneInfo

from src.platform.persistence.models import (
    PaperTradingAccount,
    PaperTradingPosition,
    PaperTradingSettlement,
)
from src.platform.persistence.qlib_models import (
    QlibRecord,
    QlibControl,
    QlibAudit,
    QlibExecution,
)
from . import service
from .contracts import content_hash
from .costs import QlibCostModel
from .policy import (
    POLICY,
    dropout_plan,
    execution_date,
    lot_quantity,
    paper_security_supported,
)


def marker(kind, ident):
    return hashlib.sha256(f"{kind}:{ident}".encode()).hexdigest()[:32]


def _number(value):
    return isinstance(value, (float, int)) and math.isfinite(value) and value > 0


def _preview(db, selection):
    from src.modules.paper_trading.paper_trading_engine import (
        market_available_cash,
        sell_block_reason,
    )

    payload = selection.payload
    state = db.get(QlibControl, 1)
    market = payload.get("market", "CN")
    account = db.query(PaperTradingAccount).first()
    result = {
        "run_id": selection.id,
        "model_id": payload["model_id"],
        "market": market,
        "asof_date": payload.get("asof_date", ""),
        "execution_date": None,
        "execution_window": "09:30–09:35 Asia/Shanghai",
        "policy": POLICY,
        "research_mode": not state or service.market_mode(state, market) != "validated",
        "ready": False,
        "blocking_reason": "",
        "orders": [],
        "kept": [],
        "excluded": [],
        "cash_budget": 0.0,
        "available_cash": 0.0,
        "estimated_fees": 0.0,
        "account_enabled": bool(account and account.enabled),
        "existing_decision_id": None,
        "preview_hash": None,
        "pool_count": payload.get("catalog_count", len(payload.get("predictions", []))),
        "ranked_count": len(payload.get("predictions", [])),
    }

    def blocked(code):
        result["blocking_reason"] = code
        return result

    if market != "CN":
        return blocked("qlib_market_execution_not_supported")
    if payload.get("price_basis", "raw_execution") != "raw_execution":
        return blocked("qlib_unverified_execution_prices")
    if not state or state.champion_id != payload["model_id"]:
        return blocked("qlib_model_not_champion")
    latest_selection = service.latest_selection(state.champion_id, db)
    if latest_selection and latest_selection.id != selection.id:
        return blocked("qlib_selection_not_latest")
    if not service.freshness(payload["asof_date"]):
        return blocked("qlib_prediction_stale")
    if payload.get("monitor", {}).get("fraction", 0) < 0.95:
        return blocked("qlib_prediction_coverage_insufficient")
    if payload.get("data_kind") == "synthetic":
        return blocked("qlib_synthetic_execution_not_supported")
    if payload.get("selection_scope") == "training_cohort":
        return blocked("qlib_training_cohort_not_full_prediction")
    try:
        result["execution_date"] = execution_date(payload["asof_date"])
    except ValueError as error:
        return blocked(str(error))
    end = datetime.fromisoformat(result["execution_date"] + "T09:35:59").replace(
        tzinfo=ZoneInfo("Asia/Shanghai")
    )
    if service.now() > end:
        return blocked("qlib_plan_expired")
    previous = (
        db.query(QlibRecord).filter_by(kind="decision", parent_id=selection.id).first()
    )
    if previous:
        result["existing_decision_id"] = previous.id
        return blocked("qlib_plan_already_confirmed")
    latest = (
        db.query(QlibRecord)
        .filter_by(kind="decision")
        .order_by(QlibRecord.created_at.desc(), QlibRecord.id.desc())
        .first()
    )
    result["replaces_decision_id"] = (
        latest.id
        if latest and latest.payload["execution_date"] == result["execution_date"]
        else None
    )
    if not account:
        return blocked("qlib_paper_account_required")
    positions = (
        db.query(PaperTradingPosition)
        .filter_by(status="open")
        .order_by(PaperTradingPosition.id)
        .all()
    )
    managed = {
        p.research_meta["instrument"]: p
        for p in positions
        if p.stock_market == "CN" and (p.research_meta or {}).get("instrument")
    }
    if len(managed) != sum(
        p.stock_market == "CN" and bool((p.research_meta or {}).get("instrument"))
        for p in positions
    ):
        return blocked("qlib_duplicate_managed_holdings")
    legacy = {
        p.stock_symbol
        for p in positions
        if p.stock_market == "CN" and not (p.research_meta or {}).get("instrument")
    }
    rows = sorted(
        payload.get("predictions", []), key=lambda r: (r["rank"], r["symbol"])
    )
    prices = {r["symbol"]: r.get("reference_close") for r in rows}
    names = payload.get("names", {})
    eligible = set()
    prospective = end.replace(minute=31)
    for row in rows:
        symbol = row["symbol"]
        reason = (
            "qlib_security_execution_not_supported"
            if not paper_security_supported(row)
            else (
                "legacy_position_not_managed"
                if symbol[2:] in legacy
                else (
                    "opening_quote_unavailable"
                    if not row.get("tradable") or not _number(prices[symbol])
                    else (
                        sell_block_reason(managed[symbol], now=prospective)
                        if symbol in managed
                        else None
                    )
                )
            )
        )
        if reason:
            result["excluded"].append({"symbol": symbol, "reason": reason})
        else:
            eligible.add(symbol)
    config = payload["config"]
    sells, buys = dropout_plan(rows, managed, eligible, config["topk"])
    costs = QlibCostModel(config)
    cash = max(0.0, market_available_cash(db, account, "CN"))
    projected_cash = cash + sum(
        costs.fill("sell", prices[s], managed[s].quantity).cash_delta for s in sells
    )
    budget = projected_cash * 0.9
    per_symbol = budget / len(buys) if buys else 0
    for symbol in sells + buys:
        side = "sell" if symbol in sells else "buy"
        quantity = (
            managed[symbol].quantity
            if side == "sell"
            else lot_quantity(per_symbol, prices[symbol], per_symbol, costs)
        )
        fill = costs.fill(side, prices[symbol], quantity) if quantity else None
        result["orders"].append(
            {
                "symbol": symbol,
                "name": names.get(symbol, symbol),
                "side": side,
                "reference_price": prices[symbol],
                "estimated_quantity": quantity,
                "estimated_fee": fill.explicit_fees if fill else 0,
                "estimated_cash_delta": fill.cash_delta if fill else 0,
                "cash_limit": per_symbol if side == "buy" else None,
                "position_id": managed[symbol].id if side == "sell" else None,
            }
        )
    result["kept"] = [
        {"symbol": symbol, "quantity": p.quantity}
        for symbol, p in managed.items()
        if symbol not in sells
    ]
    result.update(
        available_cash=cash,
        cash_budget=budget,
        estimated_fees=sum(o["estimated_fee"] for o in result["orders"]),
    )
    if not result["orders"]:
        return blocked("qlib_no_changes_required")
    if not sells and not any(o["estimated_quantity"] for o in result["orders"]):
        return blocked("insufficient_cash_or_lot")
    account_state = {
        "id": account.id,
        "cash": account.current_capital,
        "initial": account.initial_capital,
        "allocations": account.market_allocations,
        "enabled": account.enabled,
        "holdings": [
            {
                "id": p.id,
                "market": p.stock_market,
                "symbol": p.stock_symbol,
                "quantity": p.quantity,
                "entry": p.entry_price,
                "opened": p.opened_at.isoformat() if p.opened_at else None,
                "research": p.research_meta,
            }
            for p in positions
        ],
        "settlements": [
            {"id": r.id, "remaining": r.remaining_amount, "status": r.status}
            for r in db.query(PaperTradingSettlement).order_by(
                PaperTradingSettlement.id
            )
        ],
    }
    result["ready"] = True
    result["preview_hash"] = content_hash(
        {
            "preview": result,
            "account": account_state,
            "revision": state.revision,
            "date": service.now().date().isoformat(),
        }
    )
    # Exclusion detail is bounded; the count remains explicit for large pools.
    result["excluded_count"] = len(result["excluded"])
    result["excluded"] = result["excluded"][:50]
    return result


def preview(run_id):
    from src.modules.paper_trading.paper_trading_engine import ACCOUNT_LOCK

    with ACCOUNT_LOCK, service.LOCK, service.transaction() as db:
        return _preview(db, service.record(db, run_id, "selection"))


def confirm(run_id, actor, preview_hash=None, acknowledge_research=False):
    from src.modules.paper_trading.paper_trading_engine import ACCOUNT_LOCK

    with ACCOUNT_LOCK, service.LOCK, service.transaction() as db:
        selection = service.record(db, run_id, "selection")
        state = db.get(QlibControl, 1)
        if (
            state
            and service.market_mode(state, selection.payload.get("market", "CN"))
            != "validated"
            and not acknowledge_research
        ):
            raise ValueError("qlib_research_ack_required")
        existing = (
            db.query(QlibRecord).filter_by(kind="decision", parent_id=run_id).first()
        )
        if existing:
            return service.serialize(existing)
        health = service.runtime_health()
        if not health["available"]:
            raise ValueError(health["error_code"])
        plan = _preview(db, selection)
        if not plan["ready"]:
            raise ValueError(plan["blocking_reason"])
        if preview_hash is not None and preview_hash != plan["preview_hash"]:
            raise ValueError("qlib_preview_changed_conflict")
        payload = selection.payload
        decision = QlibRecord(
            id=service.identity(),
            kind="decision",
            parent_id=run_id,
            name=payload["asof_date"],
            payload={
                "run_id": run_id,
                "model_id": payload["model_id"],
                "dataset_id": payload["dataset_id"],
                "market": "CN",
                "asof_date": payload["asof_date"],
                "execution_date": plan["execution_date"],
                "policy": POLICY,
                "confirmation_kind": "explicit_frozen_orders_v1",
                "risk_degree": 0.9,
                "config": payload["config"],
                "research_mode": plan["research_mode"],
                "symbols": [o["symbol"] for o in plan["orders"]],
                "orders": plan["orders"],
                "cash_budget": plan["cash_budget"],
                "preview_hash": plan["preview_hash"],
                "reference_prices": {
                    o["symbol"]: o["reference_price"] for o in plan["orders"]
                },
                "target_weights": {
                    o["symbol"]: 0.9
                    / max(1, sum(x["side"] == "buy" for x in plan["orders"]))
                    for o in plan["orders"]
                    if o["side"] == "buy"
                },
            },
        )
        db.add(decision)
        db.add(
            QlibAudit(
                action="decision_created",
                actor=actor,
                target_id=decision.id,
                detail={
                    "run_id": run_id,
                    "preview_hash": plan["preview_hash"],
                    "manual_confirmation": True,
                },
            )
        )
        db.flush()
        return service.serialize(decision)


def cancel(decision_id, actor):
    from src.modules.paper_trading.paper_trading_engine import ACCOUNT_LOCK

    with ACCOUNT_LOCK, service.LOCK, service.transaction() as db:
        decision = service.record(db, decision_id, "decision")
        if db.get(QlibRecord, marker("executed", decision_id)):
            raise ValueError("qlib_plan_already_executed_conflict")
        ident = marker("cancelled", decision_id)
        if not db.get(QlibRecord, ident):
            db.add(
                QlibRecord(
                    id=ident,
                    kind="decision_cancel",
                    parent_id=decision_id,
                    name=decision.name,
                    payload={"decision_id": decision_id},
                )
            )
            db.add(
                QlibAudit(
                    action="decision_cancelled",
                    actor=actor,
                    target_id=decision_id,
                    detail={},
                )
            )
        return {"id": decision_id, "status": "cancelled"}


def list_plans():
    with service.transaction() as db:
        state = db.get(QlibControl, 1)
        account = db.query(PaperTradingAccount).first()
        rows = (
            db.query(QlibRecord)
            .filter_by(kind="decision")
            .order_by(QlibRecord.created_at.desc(), QlibRecord.id.desc())
            .limit(25)
            .all()
        )
        items = []
        stamp = service.now().astimezone(ZoneInfo("Asia/Shanghai"))
        for decision in rows:
            payload = decision.payload
            events = (
                db.query(QlibExecution)
                .filter_by(decision_id=decision.id)
                .order_by(QlibExecution.id)
                .all()
            )
            orders = [e for e in events if e.side in ("buy", "sell")]
            batch = db.get(QlibRecord, marker("executed", decision.id))
            cancelled = db.get(QlibRecord, marker("cancelled", decision.id))
            filled = sum(e.status == "filled" for e in orders)
            rejected = sum(e.status == "rejected" for e in orders)
            end = payload["execution_date"] + "T09:35:59"
            phase = (
                "cancelled"
                if cancelled
                else (
                    "partially_filled"
                    if batch and filled and rejected
                    else (
                        "filled"
                        if batch and filled
                        else (
                            "rejected"
                            if batch and rejected
                            else (
                                "executed"
                                if batch
                                else (
                                    "model_changed"
                                    if not state
                                    or state.champion_id != payload["model_id"]
                                    else (
                                        "expired"
                                        if stamp
                                        > datetime.fromisoformat(end).replace(
                                            tzinfo=stamp.tzinfo
                                        )
                                        else (
                                            "superseded"
                                            if rows and decision.id != rows[0].id
                                            else (
                                                "paused"
                                                if not account or not account.enabled
                                                else "waiting_open"
                                            )
                                        )
                                    )
                                )
                            )
                        )
                    )
                )
            )
            items.append(
                {
                    "id": decision.id,
                    "run_id": payload["run_id"],
                    "model_id": payload["model_id"],
                    "asof_date": payload["asof_date"],
                    "execution_date": payload["execution_date"],
                    "status": phase,
                    "research_mode": payload.get("research_mode", True),
                    "cash_budget": payload.get("cash_budget"),
                    "orders": payload.get("orders", []),
                    "symbols": payload.get("symbols", []),
                    "filled": filled,
                    "rejected": rejected,
                    "can_cancel": not cancelled
                    and not batch
                    and phase not in ("expired", "model_changed", "superseded"),
                    "events": [
                        {
                            "symbol": e.symbol,
                            "side": e.side,
                            "status": e.status,
                            "quantity": e.quantity,
                            "price": e.price,
                            "reason": e.reason,
                        }
                        for e in events[:100]
                    ],
                }
            )
        return {
            "items": items,
            "account_enabled": bool(account and account.enabled),
            "execution_market": "CN",
            "execution_window": "09:30–09:35 Asia/Shanghai",
        }
