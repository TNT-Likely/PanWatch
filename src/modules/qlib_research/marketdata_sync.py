"""Opt-in, durable marketdata ingestion. No implicit approval or trading."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import json
import time
from zoneinfo import ZoneInfo

from sqlalchemy import update

from src.platform.persistence.qlib_models import QlibAudit, QlibSyncJob
from src.platform.marketdata.marketdata_client import get_market_data
from src.platform.scheduling.trading_calendar import is_trading_day
from . import service
from .contracts import DatasetInput, MarketDataSourceInput, ResearchConfig, content_hash, valid_id

EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="qlib-data")


def serialize(job):
    return {"id": job.id, "status": job.status, "progress": job.progress,
            "dataset_id": job.dataset_id, "train_job_id": job.train_job_id, "predict_job_id": job.predict_job_id,
            "error_code": job.error_code, "created_at": job.created_at.replace(tzinfo=ZoneInfo("UTC")).isoformat()}


def status():
    vendors = get_market_data().research_sources()
    with service.LOCK, service.transaction() as db:
        settings = service.control(db).settings
        job = db.query(QlibSyncJob).order_by(QlibSyncJob.created_at.desc()).first()
        return {"source": settings.get("marketdata_source"), "dataset_id": settings.get("marketdata_dataset_id"),
                "vendors": vendors, "job": serialize(job) if job else None}


def configure(body: MarketDataSourceInput, actor):
    vendors = get_market_data().research_sources()
    if not vendors or (body.vendor != "auto" and body.vendor not in vendors):
        raise ValueError("qlib_research_source_disabled")
    with service.LOCK, service.transaction() as db:
        state = service.control(db)
        config = body.model_dump()
        previous = state.settings.get("marketdata_source")
        compatible = previous and previous["symbols"] == config["symbols"] and previous["vendor"] == config["vendor"] and previous["license"] == config["license"]
        state.settings = {**service.DEFAULT_SETTINGS, **state.settings,
                          "marketdata_source": config, "marketdata_generation": service.identity(),
                          "marketdata_dataset_id": state.settings.get("marketdata_dataset_id") if compatible else None,
                          "enabled": body.automatic}
        db.add(QlibAudit(action="marketdata_configured", actor=actor, detail={"symbols": config["symbols"], "vendor": config["vendor"], "automatic": config["automatic"]}))
    return status()


def enqueue(request_key, actor, *, train_when_ready=False, reinitialize=False, predict_when_ready=False):
    if train_when_ready and not service.runtime_health()["available"]:
        raise ValueError("qlib_worker_unavailable")
    with service.LOCK, service.transaction() as db:
        settings = service.control(db).settings
        source = settings.get("marketdata_source")
        if not source:
            raise ValueError("qlib_marketdata_not_configured")
        config = {"source": source, "generation": settings["marketdata_generation"],
                  "base_dataset_id": None if reinitialize else settings.get("marketdata_dataset_id"),
                  "train_when_ready": train_when_ready, "reinitialize": reinitialize, "predict_when_ready": predict_when_ready}
        previous = db.query(QlibSyncJob).filter_by(request_key=request_key).first()
        if previous:
            # Completed ingestion can advance the base dataset: the original
            # request remains idempotent as long as its intent/generation agrees.
            if any(previous.config[k] != config[k] for k in ("source", "generation", "train_when_ready", "reinitialize", "predict_when_ready")):
                raise ValueError("qlib_request_key_conflict")
            return serialize(previous)
        if db.query(QlibSyncJob).filter(QlibSyncJob.status.in_(("queued", "running"))).first():
            raise ValueError("qlib_sync_busy")
        job = QlibSyncJob(id=service.identity(), request_key=request_key, config=config, status="queued", progress={"completed": 0, "total": len(source["symbols"]), "phase": "queued"})
        db.add(job)
        db.add(QlibAudit(action="marketdata_sync_queued", actor=actor, target_id=job.id, detail={"train_when_ready": train_when_ready, "reinitialize": reinitialize}))
        db.flush()
        result = serialize(job)
    EXECUTOR.submit(execute, result["id"])
    return result


def cancel(job_id, actor):
    with service.LOCK, service.transaction() as db:
        job = db.get(QlibSyncJob, valid_id(job_id))
        if not job:
            raise ValueError("qlib_job_not_found")
        if job.status in ("queued", "running"):
            job.status, job.finished_at = "cancelled", service.now()
            db.add(QlibAudit(action="marketdata_sync_cancelled", actor=actor, target_id=job_id))
        return serialize(job)


def recover():
    with service.LOCK, service.transaction() as db:
        for job in db.query(QlibSyncJob).filter(QlibSyncJob.status.in_(("queued", "running"))).all():
            job.status, job.error_code, job.finished_at = "failed", "qlib_job_interrupted", service.now()


def checkpoint(job_id, config, started, **progress):
    with service.LOCK, service.transaction() as db:
        job = db.get(QlibSyncJob, job_id)
        if job.status != "running":
            raise ValueError("qlib_sync_cancelled")
        if service.control(db).settings.get("marketdata_generation") != config["generation"]:
            raise ValueError("qlib_source_configuration_changed")
        if time.monotonic() - started > 600:
            raise ValueError("qlib_sync_timeout")
        job.progress = {**job.progress, **progress}


def completed_day():
    current = service.now().astimezone(ZoneInfo("Asia/Shanghai"))
    day = current.date()
    if current.hour < 15 or not is_trading_day("CN", day):
        day -= timedelta(days=1)
    for _ in range(15):
        if is_trading_day("CN", day):
            return day.isoformat()
        day -= timedelta(days=1)
    raise ValueError("qlib_calendar_unknown")


def append_panel(previous, current):
    """Strict append only; changing source/history requires explicit reinit."""
    if previous["source"] != current["source"] or previous["provenance"] != current["provenance"]:
        raise ValueError("qlib_source_changed_reinitialize_required")
    old = {(b["date"], b["symbol"]): b for b in previous["bars"]}
    new = {(b["date"], b["symbol"]): b for b in current["bars"]}
    end = max(previous["calendar"])
    if any(old.get(k) != b for k, b in new.items() if k[0] <= end):
        raise ValueError("qlib_history_changed_reinitialize_required")
    # Refetching a sliding 800-day window must not remove recent historical bars
    # or sessions (suspension/corrections), nor truncate frozen training history.
    overlap_start = min(current["calendar"])
    if any(k not in new for k in old if k[0] >= overlap_start):
        raise ValueError("qlib_history_changed_reinitialize_required")
    if [d for d in previous["calendar"] if d >= overlap_start] != [d for d in current["calendar"] if d <= end]:
        raise ValueError("qlib_history_changed_reinitialize_required")
    result = {**current, "bars": list(old.values()) + [b for k, b in new.items() if k[0] > end],
              "calendar": previous["calendar"] + [d for d in current["calendar"] if d > end],
              "members": [{**m, "end": max(current["calendar"])} for m in previous["members"]],
              "names": previous.get("names", {})}
    return result


def collect(vendor, config, job_id, started):
    md = get_market_data()
    source = config["source"]
    day = completed_day()
    checkpoint(job_id, config, started, phase="calendar", vendor=vendor, completed=0)
    calendar = [d for d in md.research_calendar(vendor=vendor) if d <= day]
    if len(calendar) < 220 or calendar[-1] != day:
        raise ValueError("qlib_marketdata_stale")
    checkpoint(job_id, config, started, phase="quotes", vendor=vendor)
    quotes = md.quotes([s[2:] for s in source["symbols"]], market="CN")
    names = {q.symbol.upper().removeprefix("SH").removeprefix("SZ"): q for q in quotes}
    bars, members = [], []
    for i, symbol in enumerate(source["symbols"]):
        checkpoint(job_id, config, started, phase="history", symbol=symbol, completed=i, vendor=vendor)
        quote = names.get(symbol[2:])
        if not quote or not quote.name or "ST" in quote.name.upper() or "退" in quote.name or not quote.quote_date or quote.quote_date.isoformat() < day:
            raise ValueError("qlib_pool_quote_or_status_unavailable")
        history = md.research_history(symbol, vendor=vendor, completed_through=day)
        stock_bars = [b for b in history.bars if calendar[0] <= b["date"] <= day]
        if len(stock_bars) < 220 or stock_bars[-1]["date"] != day or any(b["date"] not in set(calendar) for b in stock_bars):
            raise ValueError("qlib_pool_history_insufficient_or_stale")
        bars.extend(stock_bars)
        members.append({"symbol": symbol, "start": stock_bars[0]["date"], "end": day, "price_limit": 0.1, "special_treatment": False})
    return {"name": "Marketdata fixed CN research pool", "source": f"marketdata:{vendor}",
            "license": source["license"], "license_acknowledged": True,
            "data_kind": "market", "universe_kind": "fixed_research", "adjustment": "snapshot_adjusted",
            "calendar": calendar, "members": members, "bars": bars,
            "names": {s: names[s[2:]].name for s in source["symbols"]},
            "provenance": {"vendor": vendor, "volume_unit": "shares", "source_volume_unit": "100_share_lots",
                           "adjustment_method": "hfq_close_divided_by_raw_close", "calendar": "observed_SSE_index_daily_bars",
                           "historical_status": "unverified_assumed_regular_main_board_research_only"}}


def execute(job_id):
    started = time.monotonic()
    try:
        with service.LOCK, service.transaction() as db:
            claimed = db.execute(update(QlibSyncJob).where(QlibSyncJob.id == job_id, QlibSyncJob.status == "queued").values(status="running"))
            if claimed.rowcount != 1:
                return
            config = dict(db.get(QlibSyncJob, job_id).config)
        previous = json.loads(service.dataset_path(config["base_dataset_id"]).read_text()) if config["base_dataset_id"] else None
        if previous and content_hash(previous) != service.get_record(config["base_dataset_id"], "dataset")["content_hash"]:
            raise ValueError("qlib_dataset_hash_mismatch")
        source = config["source"]
        vendors = [previous["provenance"]["vendor"]] if previous else get_market_data().research_sources() if source["vendor"] == "auto" else [source["vendor"]]
        if not vendors:
            raise ValueError("qlib_research_source_disabled")
        panel = None
        for vendor in vendors[:2]:
            try:
                panel = collect(vendor, config, job_id, started)
                break
            except (ValueError, TypeError, KeyError, IndexError) as error:
                if str(error) in ("qlib_sync_cancelled", "qlib_source_configuration_changed", "qlib_sync_timeout"):
                    raise
                last_error = str(error) if str(error).startswith("qlib_") else "qlib_marketdata_fetch_failed"
                with service.LOCK, service.transaction() as db:
                    job = db.get(QlibSyncJob, job_id)
                    job.progress = {**job.progress, "attempt_errors": [*job.progress.get("attempt_errors", []), {"vendor": vendor, "error_code": last_error}]}
        if panel is None:
            raise ValueError(last_error)
        if previous:
            panel = append_panel(previous, panel)
        validated = DatasetInput.model_validate(panel)
        checkpoint(job_id, config, started, phase="persist", completed=len(source["symbols"]))
        with service.LOCK:
            # Cancellation/configuration cannot interleave between final check
            # and atomic publication of the completed dataset pointer.
            checkpoint(job_id, config, started)
            data = service.import_dataset(validated, "marketdata")
            with service.transaction() as db:
                job = db.get(QlibSyncJob, job_id)
                state = service.control(db)
                state.settings = {**state.settings, "marketdata_dataset_id": data["id"], "dataset_id": data["id"]}
                champion_id = state.champion_id
                job.dataset_id = data["id"]
                if config["train_when_ready"]:
                    # Keep cancellation/config checks and explicit enqueue in the
                    # same lock, but avoid an overlapping SQLite transaction.
                    job.progress = {**job.progress, "phase": "training"}
            training = service.enqueue(data["id"], ResearchConfig(topk=min(10, len(source["symbols"]))).model_dump(), f"sync-train-{job_id}", "marketdata-user-request") if config["train_when_ready"] else None
            prediction = service.enqueue(data["id"], {}, f"sync-predict-{job_id}", "marketdata-user-request", model_id=champion_id) if champion_id and previous and config["predict_when_ready"] and not config["train_when_ready"] else None
            with service.transaction() as db:
                job = db.get(QlibSyncJob, job_id)
                job.train_job_id = training["id"] if training else None
                job.predict_job_id = prediction["id"] if prediction else None
                job.status, job.finished_at = "succeeded", service.now()
                job.progress = {**job.progress, "phase": "complete"}
                db.add(QlibAudit(action="marketdata_sync_succeeded", actor="marketdata", target_id=job_id, detail={"dataset_id": data["id"], "train_job_id": job.train_job_id}))
    except Exception as error:
        with service.LOCK, service.transaction() as db:
            job = db.get(QlibSyncJob, job_id)
            if job and job.status in ("queued", "running"):
                job.status, job.finished_at = "failed", service.now()
                job.error_code = str(error) if isinstance(error, ValueError) and str(error).startswith("qlib_") else "qlib_marketdata_sync_failed"


def scheduled_sync(moment):
    """Return None only when a complete fresh source is ready for prediction."""
    with service.LOCK, service.transaction() as db:
        settings = {**service.DEFAULT_SETTINGS, **service.control(db).settings}
        source = settings.get("marketdata_source")
        if not settings["enabled"] or not source or not source["automatic"] or not is_trading_day("CN", moment.date()) or moment.hour < settings["daily_hour"]:
            return None
        day = moment.date().isoformat()
        dataset_id = settings.get("marketdata_dataset_id")
        if dataset_id and service.record(db, dataset_id, "dataset").payload["end_date"] == day:
            return None
        active = db.query(QlibSyncJob).filter(QlibSyncJob.status.in_(("queued", "running"))).first()
        if active:
            return {"status": "syncing", "sync_job": serialize(active)}
        generation = settings["marketdata_generation"]
    for attempt in range(3):
        job = enqueue(f"daily-data-{day}-{generation}-{attempt}", "scheduler")
        if job["status"] not in ("failed", "cancelled"):
            return {"status": "syncing", "sync_job": job}
        if job["status"] == "cancelled" or job["error_code"] in ("qlib_history_changed_reinitialize_required", "qlib_source_changed_reinitialize_required"):
            return {"status": "data_sync_failed", "sync_job": job}
    return {"status": "data_sync_failed", "sync_job": job}
