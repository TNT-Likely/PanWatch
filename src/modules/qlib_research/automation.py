"""Standing, bounded authorization for automatic CN paper plans, never brokers.

Automatic is the default presentation, not an implicit authorization upgrade.
All mutations share the account/service locks with confirmation and execution.
"""

from datetime import datetime, timedelta, timezone
import hashlib

from src.platform.persistence.models import PaperTradingAccount
from src.platform.persistence.qlib_models import (
    QlibAudit,
    QlibControl,
    QlibRecord,
    QlibUniverseJob,
)
from . import service
from .policy import execution_date
from .portfolio import CAPITAL_POLICY, capital_config

KEY = "paper_automation"


def input_error(selection, orders):
    from .opportunity_context import enrich

    wanted = {o["symbol"] for o in orders}
    rows = enrich(
        selection,
        [r for r in selection.get("predictions", []) if r["symbol"] in wanted],
    )
    if len(rows) != len(wanted) or any(not r["observed_context"] for r in rows):
        return "qlib_automation_inputs_unverified"
    if any(abs(r["observed_context"]["return_20_observations"]) > 1 for r in rows):
        return "qlib_automation_input_anomaly"
    return ""


def execution_error(db, decision):
    error = grant_error(db, decision.payload["automation_grant_id"])
    if error:
        return error
    selection = db.get(QlibRecord, decision.payload["run_id"])
    return (
        input_error(selection.payload, decision.payload["orders"])
        if selection
        else "qlib_automation_inputs_unverified"
    )


def configuration(state):
    return (state.settings or {}).get(KEY, {}) if state else {}


def grant_error(db, grant_id, *, check_source=True):
    from src.modules.paper_trading.paper_trading_engine import (
        market_allocations_or_default,
    )

    state = db.get(QlibControl, 1)
    config = configuration(state)
    if config.get("mode") != "automatic" or config.get("grant_id") != grant_id:
        return "qlib_automation_authorization_required"
    grant = db.get(QlibRecord, grant_id)
    if not grant or grant.kind != "paper_authorization":
        return "qlib_automation_authorization_required"
    data = grant.payload
    if data.get("capital_policy") != CAPITAL_POLICY:
        return "qlib_automation_capital_policy_changed"
    if service.now() >= datetime.fromisoformat(data["expires_at"]):
        return "qlib_automation_authorization_expired"
    account = db.query(PaperTradingAccount).first()
    if not account or not account.enabled:
        return "qlib_automation_account_paused"
    if (
        account.id != data["account_id"]
        or account.initial_capital != data["initial_capital"]
        or market_allocations_or_default(account) != data["allocations"]
    ):
        return "qlib_automation_funds_changed"
    if state.champion_id != data["model_id"]:
        return "qlib_automation_model_changed"
    if state.settings.get("universe_generation") != data["source_generation"]:
        return "qlib_source_configuration_changed"
    if check_source:
        history = (
            db.query(QlibUniverseJob)
            .filter_by(kind="history", market="CN")
            .order_by(QlibUniverseJob.created_at.desc(), QlibUniverseJob.id.desc())
            .first()
        )
        if history and history.status in ("failed", "cancelled", "interrupted"):
            return "qlib_automation_source_failed"
        if history and history.status in ("queued", "running"):
            return "qlib_automation_data_updating"
    return ""


def _status(db):
    from src.modules.paper_trading.paper_trading_engine import (
        market_allocations_or_default,
    )
    from . import paper_plans

    state = db.get(QlibControl, 1)
    config = configuration(state)
    account = db.query(PaperTradingAccount).first()
    grant = db.get(QlibRecord, config["grant_id"]) if config.get("grant_id") else None
    model = (
        db.get(QlibRecord, state.champion_id) if state and state.champion_id else None
    )
    source = (state.settings or {}).get("universe_config") if state else None
    available = bool(
        account
        and model
        and model.payload.get("data_kind") == "market"
        and source
        and source.get("license_acknowledged")
    )
    result = {
        "mode": config.get("mode", "automatic"),
        "authorized": bool(grant),
        "model_id": model.id if model else None,
        "model_name": model.name if model else None,
        "account_enabled": bool(account and account.enabled),
        "can_authorize": available,
        "capital": capital_config(account) if account else None,
        "bound_market_principal": (
            (
                grant.payload["initial_capital"]
                * grant.payload["allocations"].get("CN", 0)
            )
            if grant
            else None
        ),
        "expires_at": grant.payload["expires_at"] if grant else None,
        "grant_id": grant.id if grant else None,
        "reason": "",
        "phase": "needs_authorization",
        "latest_decision_id": None,
        "bound_model_id": grant.payload["model_id"] if grant else None,
    }
    if result["mode"] == "manual":
        result["phase"] = "manual"
        return result
    if not grant:
        result["reason"] = (
            "qlib_paper_account_required"
            if not account
            else "qlib_automation_setup_required" if not available else ""
        )
        return result
    error = grant_error(db, grant.id)
    if error:
        result.update(
            phase="paused" if error == "qlib_automation_account_paused" else "blocked",
            reason=error,
        )
        return result
    selection = service.latest_selection(state.champion_id, db)
    if not selection:
        result.update(
            phase="waiting_data", reason="qlib_automation_prediction_required"
        )
        return result
    if not service.freshness(selection.payload["asof_date"]):
        result.update(phase="waiting_data", reason="qlib_prediction_stale")
        return result
    decision = (
        db.query(QlibRecord)
        .filter(
            QlibRecord.kind == "decision",
            QlibRecord.payload["automation_grant_id"].as_string() == grant.id,
        )
        .order_by(QlibRecord.created_at.desc(), QlibRecord.id.desc())
        .first()
    )
    if decision and decision.payload["asof_date"] == selection.payload["asof_date"]:
        result.update(phase="planned", latest_decision_id=decision.id)
        return result
    plan = paper_plans._preview(db, selection)
    result.update(
        phase="ready" if plan["ready"] else "waiting_data",
        reason=plan["blocking_reason"],
    )
    if plan["ready"]:
        error = input_error(selection.payload, plan["orders"])
        if error:
            result.update(phase="blocked", reason=error)
    return result


def status():
    with service.transaction() as db:
        return _status(db)


def configure(body, actor):
    from src.modules.paper_trading.paper_trading_engine import (
        ACCOUNT_LOCK,
        market_allocations_or_default,
    )
    from .paper_plans import marker

    with ACCOUNT_LOCK, service.LOCK, service.transaction() as db:
        state = service.control(db)
        account = db.query(PaperTradingAccount).first()
        if body.mode == "automatic" and body.enabled:
            if not body.acknowledge_research:
                raise ValueError("qlib_research_ack_required")
            if not body.request_key:
                raise ValueError("qlib_automation_request_key_required")
            grant_id = hashlib.sha256(
                ("paper-authorization:" + body.request_key).encode()
            ).hexdigest()[:32]
            previous = db.get(QlibRecord, grant_id)
            if previous:
                if configuration(state).get("grant_id") != grant_id or any(
                    previous.payload.get(k) != value
                    for k, value in {
                        "actor": actor,
                        "model_id": body.model_id,
                        "capital_config_hash": body.capital_config_hash,
                        "days": body.days,
                    }.items()
                ):
                    raise ValueError("qlib_automation_authorization_conflict")
                return _status(db)
            if not account:
                raise ValueError("qlib_paper_account_required")
            if body.model_id != state.champion_id:
                raise ValueError("qlib_automation_model_changed")
            model = service.record(db, body.model_id, "model")
            if model.payload.get("market", "CN") != "CN":
                raise ValueError("qlib_market_execution_not_supported")
            source = state.settings.get("universe_config")
            if (
                model.payload.get("data_kind") != "market"
                or not source
                or not source.get("license_acknowledged")
            ):
                raise ValueError("qlib_automation_setup_required")
            if not service.runtime_health()["available"]:
                raise ValueError("qlib_worker_unavailable")
            capital = capital_config(account)
            if body.capital_config_hash != capital["capital_config_hash"]:
                raise ValueError("qlib_automation_funds_changed")
            if capital["market_principal"] <= 0:
                raise ValueError("qlib_automation_market_unallocated")
        # Switching authorization never restores prior pending plans.
        for decision in db.query(QlibRecord).filter_by(kind="decision"):
            if not db.get(QlibRecord, marker("executed", decision.id)) and not db.get(
                QlibRecord, marker("cancelled", decision.id)
            ):
                db.add(
                    QlibRecord(
                        id=marker("cancelled", decision.id),
                        kind="decision_cancel",
                        parent_id=decision.id,
                        name=decision.name,
                        payload={
                            "decision_id": decision.id,
                            "reason": "automation_mode_changed",
                        },
                    )
                )
                db.add(
                    QlibAudit(
                        action="decision_cancelled",
                        actor=actor,
                        target_id=decision.id,
                        detail={"reason": "automation_mode_changed"},
                    )
                )
        if body.mode == "manual" or not body.enabled:
            state.settings = {
                **state.settings,
                KEY: {"mode": body.mode, "grant_id": None},
            }
            if account:
                account.enabled = False
        else:
            grant = QlibRecord(
                id=grant_id,
                kind="paper_authorization",
                name="CN automatic paper authorization",
                payload={
                    "model_id": model.id,
                    "account_id": account.id,
                    "initial_capital": account.initial_capital,
                    "allocations": market_allocations_or_default(account),
                    "capital_policy": CAPITAL_POLICY,
                    "capital_config_hash": capital["capital_config_hash"],
                    "source_generation": state.settings.get("universe_generation"),
                    "expires_at": (
                        service.now() + timedelta(days=body.days)
                    ).isoformat(),
                    "actor": actor,
                    "days": body.days,
                },
            )
            db.add(grant)
            state.settings = {
                **state.settings,
                "universe_config": {**source, "automatic": True},
                KEY: {"mode": "automatic", "grant_id": grant.id},
            }
            account.enabled = True
        db.add(
            QlibAudit(
                action="paper_automation_configured",
                actor=actor,
                detail={
                    "mode": body.mode,
                    "grant_id": configuration(state).get("grant_id"),
                },
            )
        )
    return status()


def tick(session_factory=None):
    """No training/review. Failed data stops orders; no unbounded resume loop."""
    from src.modules.paper_trading.paper_trading_engine import ACCOUNT_LOCK
    from . import paper_plans, universe

    with ACCOUNT_LOCK, service.LOCK:
        with (session_factory or service.SessionLocal)() as db:
            state = db.get(QlibControl, 1)
            config = configuration(state)
            if not config.get("grant_id"):
                return {"phase": "needs_authorization"}
            reason = grant_error(db, config["grant_id"], check_source=False)
            if reason:
                return {"phase": "blocked", "reason": reason}
        # Durable daily job keys prevent repeated downloads/implicit retraining.
        universe.scheduled_tick()
        with (session_factory or service.SessionLocal)() as db:
            reason = grant_error(db, config["grant_id"])
            if reason:
                return {"phase": "waiting_data", "reason": reason}
            state = db.get(QlibControl, 1)
            selection = service.latest_selection(state.champion_id, db)
            if not selection:
                return {
                    "phase": "waiting_data",
                    "reason": "qlib_automation_prediction_required",
                }
            run_id = selection.id
        try:
            decision = paper_plans.confirm(
                run_id,
                "automatic_paper",
                acknowledge_research=True,
                automation_grant_id=config["grant_id"],
            )
            return {"phase": "planned", "decision_id": decision["id"]}
        except ValueError as error:
            return {"phase": "waiting_data", "reason": str(error)}
