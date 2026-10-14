"""Full current-listed universes, resumable history and frozen bulk inference.

Catalog membership and price/prediction coverage are separate, explicit states.
No automatic model approval, old heuristic fallback or broker execution.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
import hashlib
import json
import math
from pathlib import Path
import re
import subprocess
import time
from zoneinfo import ZoneInfo

from sqlalchemy import update

from src.platform.marketdata.marketdata_client import get_market_data
from src.platform.persistence.qlib_models import (
    QlibAudit,
    QlibRecord,
    QlibUniverseJob,
    QlibUniverseMember,
)
from src.platform.scheduling.trading_calendar import is_trading_day, trading_sessions
from . import service
from .contracts import Bar, DatasetInput, UniverseConfigInput, content_hash, valid_id

EXECUTOR = ThreadPoolExecutor(max_workers=3, thread_name_prefix="qlib-universe")
MARKETS = ("CN", "HK", "US")


def completed_day(market):
    timezone = {
        "CN": "Asia/Shanghai",
        "HK": "Asia/Hong_Kong",
        "US": "America/New_York",
    }[market]
    current = service.now().astimezone(ZoneInfo(timezone))
    day = current.date()
    sessions = trading_sessions(market, day)
    if not sessions or current.time().replace(tzinfo=None) < sessions[-1].end:
        day -= timedelta(days=1)
    for _ in range(15):
        if is_trading_day(market, day):
            return day.isoformat()
        day -= timedelta(days=1)
    raise ValueError("qlib_calendar_unknown")


def job_dict(job):
    return {
        "id": job.id,
        "kind": job.kind,
        "market": job.market,
        "status": job.status,
        "progress": job.progress,
        "result_id": job.result_id,
        "error_code": job.error_code,
        "resumable": job.kind in ("catalog", "history")
        and job.status in ("interrupted", "cancelled", "failed"),
        "created_at": job.created_at.isoformat(),
    }


def status():
    vendors = {market: get_market_data().research_sources(market) for market in MARKETS}
    with service.LOCK, service.transaction() as db:
        state = service.control(db)
        catalogs = state.settings.get("universe_catalogs", {})
        markets = {}
        for market in MARKETS:
            completed = completed_day(market)
            counts, reasons = {}, {}
            for row in db.query(QlibUniverseMember).filter_by(
                market=market, catalog_id=catalogs.get(market, "")
            ):
                coverage = (
                    "stale"
                    if row.status == "ready" and row.asof_date != completed
                    else row.status
                )
                counts[coverage] = counts.get(coverage, 0) + 1
                if row.error_code:
                    reasons[row.error_code] = reasons.get(row.error_code, 0) + 1
            catalog = db.get(QlibRecord, catalogs.get(market, ""))
            champion = service.market_champion(state, market)
            latest = service.latest_selection(champion, db) if champion else None
            markets[market] = {
                "catalog_id": catalog.id if catalog else None,
                "catalog": catalog.payload if catalog else None,
                "counts": counts,
                "reasons": reasons,
                "total": sum(counts.values()),
                "vendors": vendors[market],
                "champion_id": champion,
                "completed_day": completed_day(market),
                "predicted": (
                    len(latest.payload["predictions"])
                    if latest
                    and latest.payload.get("selection_scope") != "training_cohort"
                    else 0
                ),
                "prediction_date": latest.payload["asof_date"] if latest else None,
            }
        jobs = [
            job_dict(j)
            for j in db.query(QlibUniverseJob)
            .order_by(QlibUniverseJob.created_at.desc())
            .limit(30)
        ]
        return {
            "config": state.settings.get("universe_config"),
            "markets": markets,
            "jobs": jobs,
        }


def members(market, *, page=1, limit=100, search="", status_filter=""):
    with service.transaction() as db:
        catalog = (
            service.control(db).settings.get("universe_catalogs", {}).get(market, "")
        )
        query = db.query(QlibUniverseMember).filter_by(
            market=market, catalog_id=catalog
        )
        rows = query.order_by(QlibUniverseMember.symbol).all()
        completed = completed_day(market)

        def coverage(row):
            return (
                "stale"
                if row.status == "ready" and row.asof_date != completed
                else row.status
            )

        if status_filter:
            rows = [r for r in rows if coverage(r) == status_filter]
        if search:
            text = search.casefold()
            rows = [
                r
                for r in rows
                if text in f"{r.symbol} {r.metadata_.get('name','')}".casefold()
            ]
        return {
            "total": len(rows),
            "items": [
                {
                    "symbol": r.symbol,
                    **r.metadata_,
                    "status": coverage(r),
                    "error_code": r.error_code,
                    "asof_date": r.asof_date,
                }
                for r in rows[(page - 1) * limit : page * limit]
            ],
        }


def configure(body: UniverseConfigInput, actor):
    with service.LOCK, service.transaction() as db:
        if (
            db.query(QlibUniverseJob)
            .filter(QlibUniverseJob.status.in_(("queued", "running")))
            .first()
        ):
            raise ValueError("qlib_universe_busy")
        state = service.control(db)
        state.settings = {
            **state.settings,
            "universe_config": body.model_dump(),
            "universe_generation": service.identity(),
        }
        # A full-universe configuration replaces legacy small-pool ingestion;
        # keep its artifacts/settings for audit, but do not run both schedules.
        legacy = state.settings.get("marketdata_source")
        if legacy:
            state.settings = {
                **state.settings,
                "enabled": False,
                "marketdata_source": {**legacy, "automatic": False},
            }
        db.add(
            QlibAudit(
                action="universe_configured",
                actor=actor,
                detail={
                    "markets": body.markets,
                    "training_stocks": body.training_stocks,
                    "automatic": body.automatic,
                },
            )
        )
    return status()


def enqueue(kind, request_key, actor, market=None, config=None, reinitialize=False):
    if kind in ("train", "predict"):
        health = service.runtime_health()
        if not health["available"]:
            raise ValueError(health["error_code"])
    with service.LOCK, service.transaction() as db:
        state = service.control(db)
        if not state.settings.get("universe_config"):
            raise ValueError("qlib_universe_not_configured")
        if market and market not in state.settings["universe_config"]["markets"]:
            raise ValueError("qlib_market_not_configured")
        payload = {
            "generation": state.settings["universe_generation"],
            "source": state.settings["universe_config"],
            "model_config": config or {},
            "reinitialize": reinitialize,
        }
        previous = db.query(QlibUniverseJob).filter_by(request_key=request_key).first()
        if previous:
            intent = {k: v for k, v in previous.config.items() if k in payload}
            if previous.kind != kind or previous.market != market or intent != payload:
                raise ValueError("qlib_request_key_conflict")
            return job_dict(previous)
        if (
            db.query(QlibUniverseJob)
            .filter(QlibUniverseJob.status.in_(("queued", "running")))
            .count()
            >= 4
        ):
            raise ValueError("qlib_queue_full")
        active_jobs = (
            db.query(QlibUniverseJob)
            .filter(QlibUniverseJob.status.in_(("queued", "running")))
            .all()
        )
        if any(
            kind == "catalog"
            or j.kind == "catalog"
            or market is None
            or j.market is None
            or j.market == market
            for j in active_jobs
        ):
            raise ValueError("qlib_universe_busy")
        if kind == "predict":
            champion = service.market_champion(state, market)
            if not champion:
                raise ValueError("qlib_no_champion")
            if (
                service.record(db, champion, "model").payload.get("data_kind")
                != "market"
            ):
                raise ValueError("qlib_universe_requires_market_model")
            payload["model_id"] = champion
        job = QlibUniverseJob(
            id=service.identity(),
            request_key=request_key,
            kind=kind,
            market=market,
            config=payload,
            status="queued",
            progress={"phase": "queued", "completed": 0, "total": 0},
        )
        db.add(job)
        db.add(
            QlibAudit(action=f"universe_{kind}_queued", actor=actor, target_id=job.id)
        )
        db.flush()
        result = job_dict(job)
    EXECUTOR.submit(execute, result["id"])
    return result


def cancel(job_id, actor):
    with service.LOCK, service.transaction() as db:
        job = db.get(QlibUniverseJob, valid_id(job_id))
        if not job:
            raise ValueError("qlib_job_not_found")
        if job.status in ("queued", "running"):
            job.status = "cancelled"
            job.finished_at = service.now()
            process = service.PROCESSES.get(job.id)
            if process and process.poll() is None:
                process.terminate()
            db.add(
                QlibAudit(action="universe_cancelled", actor=actor, target_id=job.id)
            )
        return job_dict(job)


def resume(job_id, actor):
    with service.LOCK, service.transaction() as db:
        job = db.get(QlibUniverseJob, valid_id(job_id))
        if (
            not job
            or job.kind not in ("catalog", "history")
            or job.status not in ("failed", "interrupted", "cancelled")
        ):
            raise ValueError("qlib_resume_not_available")
        if job.config["generation"] != service.control(db).settings.get(
            "universe_generation"
        ):
            raise ValueError("qlib_source_configuration_changed")
        active = (
            db.query(QlibUniverseJob)
            .filter(QlibUniverseJob.status.in_(("queued", "running")))
            .all()
        )
        if any(
            job.kind == "catalog"
            or j.kind == "catalog"
            or job.market is None
            or j.market is None
            or job.market == j.market
            for j in active
        ):
            raise ValueError("qlib_universe_busy")
        job.status = "queued"
        job.error_code = ""
        job.finished_at = None
        db.add(QlibAudit(action="universe_resumed", actor=actor, target_id=job.id))
        result = job_dict(job)
    EXECUTOR.submit(execute, job_id)
    return result


def recover():
    with service.LOCK, service.transaction() as db:
        for job in db.query(QlibUniverseJob).filter(
            QlibUniverseJob.status.in_(("queued", "running"))
        ):
            job.status = "interrupted"
            job.error_code = "qlib_job_interrupted"
            job.finished_at = service.now()


def checkpoint(job_id, **progress):
    with service.LOCK, service.transaction() as db:
        job = db.get(QlibUniverseJob, job_id)
        if job.status != "running":
            raise ValueError("qlib_sync_cancelled")
        if job.config["generation"] != service.control(db).settings.get(
            "universe_generation"
        ):
            raise ValueError("qlib_source_configuration_changed")
        job.progress = {**job.progress, **progress}


def write_immutable(category, payload):
    raw = json.dumps(
        payload, sort_keys=True, allow_nan=False, separators=(",", ":")
    ).encode()
    ident = hashlib.sha256(raw).hexdigest()
    directory = service.root() / category
    directory.mkdir(mode=0o700, exist_ok=True)
    path = directory / f"{ident}.json"
    with service.LOCK:
        if not path.exists():
            temp = path.with_suffix(".tmp")
            temp.write_bytes(raw)
            temp.chmod(0o600)
            temp.replace(path)
    return ident, path


def execute_catalog(job_id, config):
    errors = {}
    for market in config["source"]["markets"]:
        checkpoint(job_id, phase="catalog", market=market)
        try:
            catalog = get_market_data().research_catalog(market)
            source_day = datetime.fromisoformat(catalog.source_date).date()
            age = (service.now().date() - source_day).days
            if not -1 <= age <= 10:
                raise ValueError("qlib_catalog_stale")
            ident = catalog.content_hash[:32]
            with service.LOCK:
                checkpoint(job_id)
                with service.transaction() as db:
                    if not db.get(QlibRecord, ident):
                        db.add(
                            QlibRecord(
                                id=ident,
                                kind="catalog",
                                name=f"{market} full current-listed universe",
                                payload={
                                    "market": market,
                                    "source": catalog.source,
                                    "source_date": catalog.source_date,
                                    "total": len(catalog.members),
                                    "raw_count": catalog.raw_count,
                                    "excluded": catalog.excluded,
                                    "content_hash": catalog.content_hash,
                                    "complete": True,
                                    "historical_membership": False,
                                },
                            )
                        )
                    for meta in catalog.members:
                        row = db.get(QlibUniverseMember, meta["symbol"])
                        if not row:
                            row = QlibUniverseMember(
                                symbol=meta["symbol"],
                                market=market,
                                catalog_id=ident,
                                metadata_=meta,
                                status="pending",
                            )
                            db.add(row)
                        else:
                            if row.metadata_.get("currency") != meta["currency"]:
                                row.status = "pending"
                                row.history_id = None
                                row.asof_date = None
                                row.error_code = ""
                            row.catalog_id = ident
                            row.metadata_ = {**row.metadata_, **meta}
                    state = service.control(db)
                    state.settings = {
                        **state.settings,
                        "universe_catalogs": {
                            **state.settings.get("universe_catalogs", {}),
                            market: ident,
                        },
                    }
                    db.add(
                        QlibAudit(
                            action="universe_catalog_saved",
                            actor="worker",
                            target_id=ident,
                            detail={"market": market, "total": len(catalog.members)},
                        )
                    )
        except Exception as error:
            if str(error) in (
                "qlib_sync_cancelled",
                "qlib_source_configuration_changed",
            ):
                raise
            errors[market] = (
                str(error) if isinstance(error, ValueError) else "qlib_catalog_failed"
            )
    checkpoint(job_id, phase="catalog_complete", errors=errors)
    if errors:
        raise ValueError("qlib_catalog_partial_failure")


def read_history(ident):
    if not ident or not re.fullmatch(r"[a-f0-9]{64}", ident):
        raise ValueError("qlib_history_id_invalid")
    path = service.root() / "history" / f"{ident}.json"
    if path.is_symlink():
        raise ValueError("qlib_history_path_invalid")
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != ident:
        raise ValueError("qlib_history_hash_mismatch")
    return json.loads(raw)


def append_history(previous, current):
    if (
        previous["vendor"] != current["vendor"]
        or previous["adjustment_method"] != current["adjustment_method"]
    ):
        raise ValueError("qlib_history_source_changed")
    old = {b["date"]: b for b in previous["bars"]}
    new = {b["date"]: b for b in current["bars"]}
    start = min(new)
    end = max(old)
    if any(old.get(d) != bar for d, bar in new.items() if d <= end) or any(
        d not in new for d in old if d >= start
    ):
        raise ValueError("qlib_history_revision_requires_review")
    return {
        **current,
        "bars": previous["bars"] + [b for b in current["bars"] if b["date"] > end],
    }


def history_error(error):
    from pydantic import ValidationError

    if isinstance(error, ValidationError):
        for entry in error.errors():
            code = str(entry.get("ctx", {}).get("error", ""))
            if code.startswith("qlib_"):
                return code
    return (
        str(error)
        if isinstance(error, ValueError) and str(error).startswith("qlib_")
        else "qlib_history_fetch_failed"
    )


def fetch_member(job_id, symbol, md, day_by_market, calendars, config):
    checkpoint(job_id)
    with service.transaction() as db:
        row = db.get(QlibUniverseMember, symbol)
        meta = dict(row.metadata_)
        old_id = row.history_id
        asof = row.asof_date
    market = meta["market"]
    day = day_by_market[market]
    try:
        if old_id and asof == day and not config.get("reinitialize"):
            cached = read_history(old_id)
            return {
                "error": "",
                "history_id": old_id,
                "day": day,
                "quality_warnings": cached.get("quality_warnings", {}),
            }
        old = (
            read_history(old_id) if old_id and not config.get("reinitialize") else None
        )
        sources = [old["vendor"]] if old else md.research_sources(market)
        payload = None
        error = ""
        for vendor in sources:
            checkpoint(job_id)
            try:
                history = md.research_history(
                    symbol, vendor=vendor, market=market, completed_through=day
                )
                bars = [
                    Bar.model_validate(b).model_dump(mode="json")
                    for b in history.bars
                    if calendars[market][0] <= b["date"] <= day
                ]
                if len(bars) < 220:
                    raise ValueError("qlib_history_insufficient")
                if bars[-1]["date"] != day:
                    raise ValueError(
                        "qlib_history_latest_bar_invalid"
                        if day in history.rejected_dates
                        else "qlib_history_stale_or_suspended"
                    )
                sessions = set(calendars[market])
                if any(b["date"] not in sessions for b in bars):
                    raise ValueError("qlib_calendar_mismatch")
                currency = {"RMB": "CNY"}.get(history.currency, history.currency)
                if currency != meta["currency"]:
                    raise ValueError("qlib_source_currency_mismatch")
                payload = {
                    "symbol": symbol,
                    "market": market,
                    "vendor": vendor,
                    "currency": currency,
                    "adjustment_method": history.adjustment_method,
                    "bars": bars,
                    "quality_warnings": history.quality_warnings,
                }
                break
            except Exception as exc:
                error = history_error(exc)
        if payload is None:
            raise ValueError(error or "qlib_history_unavailable")
        if old:
            payload = append_history(old, payload)
        checkpoint(job_id)
        history_id, _ = write_immutable("history", payload)
        return {
            "error": "",
            "history_id": history_id,
            "day": day,
            "quality_warnings": payload["quality_warnings"],
        }
    except Exception as exc:
        return {"error": history_error(exc)}


def valid_calendar(dates, day):
    if not isinstance(dates, list) or len(dates) < 220:
        return False
    try:
        return (
            all(date.fromisoformat(d).isoformat() == d for d in dates)
            and dates == sorted(set(dates))
            and dates[-1] == day
        )
    except (TypeError, ValueError):
        return False


def history_calendars(md, markets, days, cursor, previous_days, frozen, cached):
    calendars, origins = {}, {}
    for market in markets:
        sources = md.research_sources(market)
        if not sources:
            raise ValueError(f"qlib_research_source_disabled_{market}")
        if (
            cursor
            and previous_days == days
            and valid_calendar(frozen.get(market), days[market])
        ):
            calendars[market] = frozen[market]
            origins[market] = "job_frozen_observed_calendar"
            continue
        for vendor in sources:
            try:
                dates = [
                    d
                    for d in md.research_calendar(vendor=vendor, market=market)
                    if d <= days[market]
                ]
                if not valid_calendar(dates, days[market]):
                    continue
                calendars[market] = dates
                origins[market] = vendor
                break
            except Exception:
                continue
        # Backwards-compatible resume of an already started snapshot only.
        # Never reuse yesterday's sessions for a newly completed market day.
        if (
            market not in calendars
            and cursor
            and previous_days == days
            and valid_calendar(cached.get(market), days[market])
        ):
            calendars[market] = cached[market]
            origins[market] = "persisted_same_session_observed_calendar"
        if market not in calendars:
            raise ValueError(f"qlib_calendar_unavailable_{market}")
    return calendars, origins


def execute_history(job_id, config, market_only):
    markets = [market_only] if market_only else config["source"]["markets"]
    md = get_market_data()
    with service.LOCK, service.transaction() as db:
        catalogs = service.control(db).settings.get("universe_catalogs", {})
        job = db.get(QlibUniverseJob, job_id)
        expected = job.config.get("catalog_ids")
        selected = {m: catalogs.get(m) for m in markets}
        if expected and expected != selected:
            raise ValueError("qlib_catalog_changed_restart_sync")
        days = {m: completed_day(m) for m in markets}
        previous_days = job.config.get("session_days")
        if job.cursor and previous_days is not None and previous_days != days:
            raise ValueError("qlib_session_changed_restart_sync")
        cursor = job.cursor
        frozen = job.config.get("calendar_snapshots", {})
        cached = service.control(db).settings.get("universe_calendars", {})
        job.config = {**job.config, "catalog_ids": selected, "session_days": days}
        symbols = [
            r.symbol
            for market in markets
            for r in db.query(QlibUniverseMember)
            .filter_by(market=market, catalog_id=catalogs.get(market, ""))
            .order_by(QlibUniverseMember.symbol)
        ]
    if not symbols:
        raise ValueError("qlib_catalog_required")
    day_by_market = days
    calendars, calendar_origins = history_calendars(
        md, markets, days, cursor, previous_days, frozen, cached
    )
    with service.LOCK, service.transaction() as db:
        state = service.control(db)
        state.settings = {
            **state.settings,
            "universe_calendars": {
                **state.settings.get("universe_calendars", {}),
                **calendars,
            },
        }
        job = db.get(QlibUniverseJob, job_id)
        job.config = {
            **job.config,
            "calendar_snapshots": calendars,
            "calendar_origins": calendar_origins,
        }
        cursor = job.cursor
    # Bounded pipeline, with supplier host throttles still enforced. Commit in
    # catalog order so the durable cursor never jumps over unfinished securities.
    failures = 0
    pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix="qlib-history")
    futures = {}
    try:
        for index in range(cursor, min(cursor + 4, len(symbols))):
            futures[index] = pool.submit(
                fetch_member,
                job_id,
                symbols[index],
                md,
                day_by_market,
                calendars,
                config,
            )
        for index in range(cursor, len(symbols)):
            symbol = symbols[index]
            checkpoint(
                job_id,
                phase="history",
                completed=index,
                total=len(symbols),
                symbol=symbol,
            )
            result = futures.pop(index).result()
            error = result["error"]
            with service.LOCK:
                checkpoint(job_id)
                with service.transaction() as db:
                    row = db.get(QlibUniverseMember, symbol)
                    row.status = "excluded" if error else "ready"
                    row.error_code = error
                    row.updated_at = service.now()
                    if not error:
                        row.history_id = result["history_id"]
                        row.asof_date = result["day"]
                        row.metadata_ = {
                            **row.metadata_,
                            "quality_warnings": result["quality_warnings"],
                        }
                    db.get(QlibUniverseJob, job_id).cursor = index + 1
            failures = (
                failures + 1
                if error
                in ("qlib_history_fetch_failed", "qlib_source_history_unavailable")
                else 0
            )
            if failures >= 10 or error == "qlib_research_source_disabled":
                with service.transaction() as db:
                    db.get(QlibUniverseJob, job_id).cursor = (
                        index - 9 if failures >= 10 else index
                    )
                raise ValueError("qlib_source_unavailable_resume_sync")
            next_index = index + 4
            if next_index < len(symbols):
                futures[next_index] = pool.submit(
                    fetch_member,
                    job_id,
                    symbols[next_index],
                    md,
                    day_by_market,
                    calendars,
                    config,
                )
    finally:
        for future in futures.values():
            future.cancel()
        pool.shutdown(wait=True, cancel_futures=True)
    checkpoint(
        job_id, phase="history_complete", completed=len(symbols), total=len(symbols)
    )


def freeze_snapshot(market):
    with service.transaction() as db:
        state = service.control(db)
        catalog_id = state.settings.get("universe_catalogs", {}).get(market)
        calendar = state.settings.get("universe_calendars", {}).get(market)
        if not calendar:
            raise ValueError("qlib_calendar_required")
        # A download may cross the next closing boundary. Its actual completed
        # snapshot remains researchable; freshness still blocks paper decisions.
        day = min(calendar[-1], completed_day(market))
        calendar = [d for d in calendar if d <= day]
        rows = (
            db.query(QlibUniverseMember)
            .filter_by(
                market=market, catalog_id=catalog_id, status="ready", asof_date=day
            )
            .order_by(QlibUniverseMember.symbol)
            .all()
        )
        refs = [
            {"symbol": r.symbol, "history_id": r.history_id, "metadata": r.metadata_}
            for r in rows
        ]
        if len(refs) < 3:
            raise ValueError("qlib_ready_pool_insufficient")
        catalog = service.record(db, catalog_id, "catalog")
        snapshot = {
            "market": market,
            "catalog_id": catalog_id,
            "catalog_count": catalog.payload["total"],
            "asof_date": day,
            "calendar": calendar,
            "members": refs,
            "license": state.settings["universe_config"]["license"],
        }
    ident = content_hash(snapshot)[:32]
    snapshot["id"] = ident
    directory = service.root() / "snapshots"
    directory.mkdir(mode=0o700, exist_ok=True)
    path = directory / f"{ident}.json"
    with service.LOCK:
        if not path.exists():
            temp = path.with_suffix(".tmp")
            temp.write_text(json.dumps(snapshot, allow_nan=False))
            temp.chmod(0o600)
            temp.replace(path)
    return snapshot, path


def training_dataset(snapshot, budget):
    # Deterministic stratification by board/exchange, independent of future returns.
    groups = {}
    for ref in snapshot["members"]:
        if (
            ref["metadata"]["currency"]
            != {"CN": "CNY", "HK": "HKD", "US": "USD"}[snapshot["market"]]
        ):
            continue
        groups.setdefault(ref["metadata"]["board"], []).append(ref)
    for rows in groups.values():
        rows.sort(key=lambda r: hashlib.sha256(r["symbol"].encode()).hexdigest())
    chosen = []
    while groups and len(chosen) < budget:
        for group in sorted(list(groups)):
            chosen.append(groups[group].pop())
            if not groups[group]:
                groups.pop(group)
            if len(chosen) >= budget:
                break
    calendar = snapshot["calendar"][-600:]
    bars = []
    members = []
    names = {}
    for ref in chosen:
        rows = [
            b
            for b in read_history(ref["history_id"])["bars"]
            if calendar[0] <= b["date"] <= calendar[-1]
        ]
        if len(rows) < 220:
            continue
        meta = ref["metadata"]
        bars.extend(rows)
        names[ref["symbol"]] = meta["name"]
        members.append(
            {
                "symbol": ref["symbol"],
                "start": rows[0]["date"],
                "end": calendar[-1],
                "price_limit": meta["price_limit"],
                "special_treatment": meta["special_treatment"],
                "lot": meta["lot"],
                "currency": meta["currency"],
            }
        )
    return DatasetInput.model_validate(
        {
            "name": f"{snapshot['market']} stratified training cohort (NOT universe limit)",
            "market": snapshot["market"],
            "source": f"Full-universe snapshot {snapshot['id']}",
            "license": snapshot["license"],
            "license_acknowledged": True,
            "data_kind": "market",
            "universe_kind": "fixed_research",
            "adjustment": "snapshot_adjusted",
            "price_basis": (
                "raw_execution" if snapshot["market"] == "CN" else "provider_snapshot"
            ),
            "calendar": calendar,
            "members": members,
            "bars": bars,
            "names": names,
            "provenance": {
                "universe_snapshot_id": snapshot["id"],
                "training_sampling": "deterministic_board_stratified_budget_not_prediction_limit",
                "historical_status": "current_only_not_PIT",
                "volume_unit": "shares",
                "quality_policy": "Discard incompatible supplier rows without filling prices; require >=95% observed valid rows, >=220 bars and valid latest session",
            },
        }
    )


def execute(job_id):
    try:
        with service.LOCK, service.transaction() as db:
            changed = db.execute(
                update(QlibUniverseJob)
                .where(QlibUniverseJob.id == job_id, QlibUniverseJob.status == "queued")
                .values(status="running")
            )
            if changed.rowcount != 1:
                return
            job = db.get(QlibUniverseJob, job_id)
            kind, market, config = job.kind, job.market, dict(job.config)
        result_id = None
        if kind == "catalog":
            execute_catalog(job_id, config)
        elif kind == "history":
            execute_history(job_id, config, market)
        elif kind == "train":
            snapshot, _ = freeze_snapshot(market)
            data = service.import_dataset(
                training_dataset(snapshot, config["source"]["training_stocks"]),
                "universe-user-request",
            )
            model_job = service.enqueue(
                data["id"],
                config["model_config"],
                f"universe-train-{job_id}",
                "universe-user-request",
            )
            result_id = model_job["id"]
        elif kind == "predict":
            snapshot, path = freeze_snapshot(market)
            result_id = execute_prediction(job_id, config["model_id"], snapshot, path)
        with service.LOCK, service.transaction() as db:
            job = db.get(QlibUniverseJob, job_id)
            if job.status == "running":
                job.status = "succeeded"
                job.result_id = result_id
                job.finished_at = service.now()
        if kind == "history" and config["source"].get("automatic"):
            for target in ([market] if market else config["source"]["markets"]):
                with service.transaction() as db:
                    champion = service.market_champion(service.control(db), target)
                if champion:
                    enqueue(
                        "predict",
                        f"universe-daily-predict-{job_id}-{target}-{champion}",
                        "scheduler",
                        market=target,
                    )
    except Exception as exc:
        with service.LOCK, service.transaction() as db:
            job = db.get(QlibUniverseJob, job_id)
            if job and job.status == "running":
                job.status = "failed"
                job.finished_at = service.now()
                job.error_code = (
                    str(exc)
                    if isinstance(exc, ValueError) and str(exc).startswith("qlib_")
                    else "qlib_universe_job_failed"
                )


def execute_prediction(job_id, model_id, snapshot, path):
    model = service.get_record(model_id, "model")
    if model.get("market", "CN") != snapshot["market"]:
        raise ValueError("qlib_model_market_mismatch")
    training_path = service.dataset_path(model["dataset_id"])
    dataset = service.get_record(model["dataset_id"], "dataset")
    if content_hash(json.loads(training_path.read_text())) != dataset["content_hash"]:
        raise ValueError("qlib_dataset_hash_mismatch")
    directory = service.root() / "jobs" / job_id
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    task = {
        "dataset_path": str(training_path),
        "config": model["config"],
        "segments": model["segments"],
        "replay_fingerprint": model["replay_fingerprint"],
        "universe_manifest": str(path),
        "manifest_hash": hashlib.sha256(path.read_bytes()).hexdigest(),
    }
    task_path = directory / "task.json"
    task_path.write_text(json.dumps(task))
    task_path.chmod(0o600)
    checkpoint(job_id, phase="prediction", total=len(snapshot["members"]))
    try:
        with (directory / "worker.log").open("w") as log:
            process = subprocess.Popen(
                [
                    str(service.worker_python()),
                    str(Path(__file__).with_name("worker.py")),
                    "--task",
                    str(task_path),
                ],
                cwd=directory,
                env=service.child_environment(directory),
                stdout=log,
                stderr=log,
            )
            with service.LOCK:
                service.PROCESSES[job_id] = process
            start = time.monotonic()
            while process.poll() is None:
                checkpoint(job_id)
                if time.monotonic() - start > 7200:
                    process.kill()
                    process.wait()
                    raise ValueError("qlib_job_timeout")
                progress = directory / "bulk-progress.json"
                if progress.exists():
                    try:
                        checkpoint(job_id, **json.loads(progress.read_text()))
                    except json.JSONDecodeError:
                        pass
                time.sleep(1)
            if process.returncode != 0:
                raise ValueError("qlib_worker_failed")
        result_path = directory / "result.json"
        if not result_path.exists() or result_path.stat().st_size > 30_000_000:
            raise ValueError("qlib_result_invalid")
        result = json.loads(
            result_path.read_text(),
            parse_constant=lambda _: (_ for _ in ()).throw(
                ValueError("qlib_result_invalid")
            ),
        )
        if (
            result.get("replay_fingerprint") != model["replay_fingerprint"]
            or result.get("universe_snapshot_id") != snapshot["id"]
            or not result.get("predictions")
        ):
            raise ValueError("qlib_result_invalid")
        symbols = {r["symbol"] for r in snapshot["members"]}
        rows = result["predictions"]
        if (
            result.get("feature_set") != "Alpha158"
            or len(rows) != len(symbols)
            or result.get("runtime", {}).get("pyqlib") != "0.9.7"
            or len({r["symbol"] for r in rows}) != len(rows)
            or any(
                r["symbol"] not in symbols
                or r["market"] != snapshot["market"]
                or r["rank"] != i
                or not math.isfinite(r["raw_score"])
                for i, r in enumerate(rows, 1)
            )
        ):
            raise ValueError("qlib_result_invalid")
        with service.LOCK:
            checkpoint(job_id)
            with service.transaction() as db:
                ident = service.identity()
                prior = service.latest_selection(model_id, db)
                ranks = (
                    {r["symbol"]: r["rank"] for r in prior.payload["predictions"]}
                    if prior and prior.payload.get("universe_snapshot_id")
                    else {}
                )
                predictions = [
                    {
                        **r,
                        "rank_change": (
                            ranks[r["symbol"]] - r["rank"]
                            if r["symbol"] in ranks
                            else None
                        ),
                    }
                    for r in result["predictions"]
                ]
                payload = {
                    "model_id": model_id,
                    "dataset_id": model["dataset_id"],
                    "experiment_id": model["experiment_id"],
                    "market": snapshot["market"],
                    "price_basis": model.get("price_basis", "raw_execution"),
                    "asof_date": snapshot["asof_date"],
                    "data_kind": "market",
                    "config": model["config"],
                    "feature_set": "Alpha158",
                    "horizon": model["config"]["horizon"],
                    "predictions": predictions,
                    "monitor": result["monitor"],
                    "prediction_scale": "within_market_raw_model_score_not_return_probability",
                    "names": {
                        r["symbol"]: r["metadata"]["name"] for r in snapshot["members"]
                    },
                    "universe_snapshot_id": snapshot["id"],
                    "catalog_count": snapshot["catalog_count"],
                    "ready_stocks": len(snapshot["members"]),
                    "training_stocks": result["training_stocks"],
                    "prediction_count": len(predictions),
                }
                db.add(
                    QlibRecord(
                        id=ident,
                        kind="selection",
                        parent_id=model_id,
                        name=snapshot["asof_date"],
                        payload=payload,
                    )
                )
                db.add(
                    QlibAudit(
                        action="universe_prediction_succeeded",
                        actor="worker",
                        target_id=ident,
                        detail={
                            "model_id": model_id,
                            "market": snapshot["market"],
                            "predictions": len(predictions),
                        },
                    )
                )
                return ident
    finally:
        with service.LOCK:
            process = service.PROCESSES.pop(job_id, None)
            if process and process.poll() is None:
                process.terminate()


def scheduled_tick():
    """Opt-in local-session synchronization. Never trains or approves a model."""
    with service.transaction() as db:
        state = service.control(db)
        config = state.settings.get("universe_config")
        if not config or not config.get("automatic"):
            return None
        generation = state.settings["universe_generation"]
        active = (
            db.query(QlibUniverseJob)
            .filter(QlibUniverseJob.status.in_(("running", "queued")))
            .first()
        )
        if active:
            return {"status": "universe_busy", "job": job_dict(active)}
        catalogs = state.settings.get("universe_catalogs", {})
    for market in config["markets"]:
        if not catalogs.get(market):
            return {
                "status": "queued",
                "job": enqueue(
                    "catalog", f"universe-daily-catalog-{generation}", "scheduler"
                ),
            }
        key = f"universe-daily-history-{generation}-{market}-{completed_day(market)}"
        with service.transaction() as db:
            existing = db.query(QlibUniverseJob).filter_by(request_key=key).first()
            if existing:
                continue  # Failure/cancellation requires explicit resume.
        return {
            "status": "queued",
            "job": enqueue("history", key, "scheduler", market=market),
        }
    return {"status": "idle"}
