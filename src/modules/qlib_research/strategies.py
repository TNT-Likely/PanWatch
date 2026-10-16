"""Everyday fixed-strategy setup, separate from ML training/governance tools."""

from datetime import timezone

from . import service, universe
from .rules import VERSION, TEMPLATES, is_rule, score_snapshot
from .contracts import content_hash
from src.platform.persistence.qlib_models import QlibRecord, QlibUniverseJob


def catalog(market="CN"):
    with service.transaction() as db:
        state = service.control(db)
        models = (
            db.query(QlibRecord)
            .filter_by(kind="model")
            .order_by(QlibRecord.created_at.desc(), QlibRecord.id.desc())
            .all()
        )
        latest = {}
        for row in models:
            algorithm = row.payload.get("config", {}).get("algorithm")
            if (
                row.payload.get("market", "CN") == market
                and algorithm in TEMPLATES
                and row.payload.get("rule_version") == VERSION
                and algorithm not in latest
            ):
                latest[algorithm] = service.serialize(row)
        champion = service.market_champion(state, market)
        selection = service.latest_selection(champion, db) if champion else None
        tasks = [
            dict(
                id=j.id,
                status=j.status,
                error_code=j.error_code,
                algorithm=j.config.get("model_config", {}).get("algorithm"),
                created_at=j.created_at.replace(tzinfo=timezone.utc).isoformat(),
            )
            for j in db.query(QlibUniverseJob)
            .filter_by(market=market)
            .order_by(QlibUniverseJob.created_at.desc())
            .limit(8)
        ]
        tasks += [
            dict(
                id=j["id"],
                status=j["status"],
                error_code=j["error_code"],
                algorithm=j["config"].get("algorithm"),
                created_at=j["created_at"],
            )
            for j in service.jobs()
            if is_rule(j["config"]) and j["market"] == market
        ]
        tasks.sort(key=lambda j: j["created_at"] or "", reverse=True)
        return dict(
            version=VERSION,
            market=market,
            revision=state.revision,
            active_id=champion,
            selection_id=selection.id if selection else None,
            items=[dict(algorithm=a, model=latest.get(a)) for a in TEMPLATES],
            jobs=tasks,
        )


def activate(model_id, revision, actor):
    with service.LOCK:
        with service.transaction() as db:
            state = service.control(db)
            if state.settings.get("paper_automation", {}).get("grant_id"):
                raise ValueError("qlib_rule_stop_before_switch")
            model = service.record(db, model_id, "model")
            if (
                not is_rule(model.payload.get("config", {}))
                or model.payload.get("rule_version") != VERSION
            ):
                raise ValueError("qlib_rule_version_unverified")
            market = model.payload.get("market", "CN")
            if model.payload.get("data_kind") != "market":
                raise ValueError("qlib_rule_execution_data_required")
            algorithm = model.payload["config"]["algorithm"]
        snapshot, _ = universe.freeze_snapshot(market)
        score_snapshot(snapshot, algorithm, universe.read_history)
        result = service.publish(
            model_id,
            revision,
            "Explicit fixed-rule research strategy selection",
            "research",
            actor,
        )
        selection = refresh(model_id, actor)
        return dict(governance=result, selection=selection)


def refresh(model_id, actor):
    """No-fit evaluation of an exact immutable ready snapshot, not retraining.

    Qlib retrospective evidence stays frozen on the model. Forward selection
    has its own identity, coverage and date; no historical report relabeling.
    """
    from src.platform.persistence.qlib_models import QlibAudit

    with service.LOCK:
        with service.transaction() as db:
            model = service.record(db, model_id, "model")
            if (
                not is_rule(model.payload.get("config", {}))
                or model.payload.get("rule_version") != VERSION
            ):
                raise ValueError("qlib_rule_version_unverified")
            payload = dict(model.payload)
            market = payload.get("market", "CN")
            state = service.control(db)
            if service.market_champion(state, market) != model_id:
                raise ValueError("qlib_model_not_champion")
            generation = state.settings.get("universe_generation")
        snapshot, _ = universe.freeze_snapshot(market)
        ident = content_hash(
            dict(
                model=model_id,
                snapshot=snapshot["id"],
                rules=VERSION,
                generation=generation,
            )
        )[:32]
        with service.transaction() as db:
            previous = db.get(QlibRecord, ident)
            if previous:
                return service.serialize(previous)
        rows = score_snapshot(
            snapshot, payload["config"]["algorithm"], universe.read_history
        )
        with service.transaction() as db:
            state = service.control(db)
            if (
                service.market_champion(state, market) != model_id
                or state.settings.get("universe_generation") != generation
            ):
                raise ValueError("qlib_source_configuration_changed")
            prior = service.latest_selection(model_id, db)
            ranks = (
                {r["symbol"]: r["rank"] for r in prior.payload["predictions"]}
                if prior
                else {}
            )
            rows = [
                {
                    **r,
                    "rank_change": ranks[r["symbol"]] - r["rank"]
                    if r["symbol"] in ranks
                    else None,
                }
                for r in rows
            ]
            selection = QlibRecord(
                id=ident,
                kind="selection",
                parent_id=model_id,
                name=snapshot["asof_date"],
                payload=dict(
                    model_id=model_id,
                    dataset_id=payload["dataset_id"],
                    experiment_id=payload["experiment_id"],
                    market=market,
                    price_basis=payload.get("price_basis", "raw_execution"),
                    asof_date=snapshot["asof_date"],
                    data_kind="market",
                    config=payload["config"],
                    feature_set=VERSION,
                    rule_version=VERSION,
                    horizon=payload["config"]["horizon"],
                    predictions=rows,
                    monitor=dict(
                        expected=len(rows),
                        predicted=len(rows),
                        fraction=1.0,
                        method="all_ready_fixed_rules_no_fit",
                    ),
                    prediction_scale="fixed_rule_ordering_key_not_probability",
                    names={
                        r["symbol"]: r["metadata"]["name"] for r in snapshot["members"]
                    },
                    universe_snapshot_id=snapshot["id"],
                    catalog_count=snapshot["catalog_count"],
                    ready_stocks=len(rows),
                    prediction_count=len(rows),
                    source_generation=generation,
                ),
            )
            db.add(selection)
            db.add(
                QlibAudit(
                    action="rule_selection_refreshed",
                    actor=actor,
                    target_id=ident,
                    detail=dict(
                        model_id=model_id, snapshot=snapshot["id"], count=len(rows)
                    ),
                )
            )
            db.flush()
            return service.serialize(selection)
