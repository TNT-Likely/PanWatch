"""Qlib application service: immutable data, durable jobs, review and lineage."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import subprocess
import threading
import time
import uuid

from sqlalchemy import update, or_

from src.platform.persistence.database import DB_PATH, SessionLocal
from .contracts import DatasetInput, ResearchConfig, content_hash, valid_id
from src.platform.persistence.qlib_models import QlibAudit, QlibControl, QlibJob, QlibRecord, QlibTemplate
from .policy import POLICY, execution_date

LOCK = threading.RLock()
EXECUTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="qlib-worker")
PREDICTOR = ThreadPoolExecutor(max_workers=1, thread_name_prefix="qlib-predict")
PROCESSES: dict[str, subprocess.Popen] = {}
HEALTH_LOCK = threading.Lock()
HEALTH_CACHE = {"key": None, "expires": 0, "value": None}
DEFAULT_SETTINGS = {"enabled": False, "dataset_id": None, "daily_hour": 18, "retrain_every_days": 30, "last_daily_date": "", "last_retrain_date": "", "champion_mode": "research"}


def now():
    return datetime.now(timezone.utc)


def identity():
    return uuid.uuid4().hex


def root():
    directory = Path(DB_PATH).resolve().parent / "qlib"
    directory.mkdir(mode=0o700, exist_ok=True)
    return directory


def worker_python():
    from src.platform.runtime.config import Settings
    configured = os.environ.get("QLIB_WORKER_PYTHON") or Settings().qlib_worker_python
    return Path(configured) if configured else Path(__file__).resolve().parents[3] / ".venv-qlib/bin/python"


def child_environment(directory: Path):
    # Do not inherit provider tokens, proxies, telemetry or a remote MLflow URI.
    env = {key: os.environ[key] for key in ("PATH", "HOME", "LANG", "LC_ALL", "SYSTEMROOT") if key in os.environ}
    cache = directory / "cache"
    cache.mkdir(mode=0o700, parents=True, exist_ok=True)
    env.update({"PYTHONNOUSERSITE": "1", "PYTHONDONTWRITEBYTECODE": "1", "XDG_CACHE_HOME": str(cache), "MPLCONFIGDIR": str(cache), "TMPDIR": str(cache), "OMP_NUM_THREADS": "2", "OPENBLAS_NUM_THREADS": "2", "MLFLOW_TRACKING_URI": (directory / "mlruns").as_uri()})
    return env


def runtime_health():
    interpreter = worker_python()
    if not interpreter.is_file():
        return {"available": False, "error_code": "qlib_worker_missing", "runtime": None}
    key = (str(interpreter), interpreter.stat().st_mtime_ns)
    with HEALTH_LOCK:
        if HEALTH_CACHE["key"] == key and HEALTH_CACHE["expires"] > time.monotonic():
            return dict(HEALTH_CACHE["value"])
        result = _probe_runtime(interpreter)
        HEALTH_CACHE.update(key=key, expires=time.monotonic() + 30, value=result)
        return dict(result)


def _probe_runtime(interpreter):
    try:
        result = subprocess.run([str(interpreter), str(Path(__file__).with_name("worker.py")), "--health"], capture_output=True, text=True, timeout=25, env=child_environment(root() / "health"))
        if result.returncode:
            return {"available": False, "error_code": "qlib_worker_import_failed", "runtime": None}
        versions = json.loads(result.stdout)
        expected = {"pyqlib": "0.9.7", "pandas": "2.2.3", "numpy": "2.2.6", "lightgbm": "4.6.0", "scikit-learn": "1.7.2", "mlflow": "2.22.0"}
        if any(versions.get(name) != version for name, version in expected.items()):
            return {"available": False, "error_code": "qlib_runtime_version_mismatch", "runtime": versions}
        return {"available": True, "error_code": "", "runtime": versions}
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return {"available": False, "error_code": "qlib_worker_unavailable", "runtime": None}


@contextmanager
def transaction():
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except BaseException:
        session.rollback()
        raise
    finally:
        session.close()


def control(db):
    state = db.get(QlibControl, 1)
    if not state:
        state = QlibControl(id=1, settings=dict(DEFAULT_SETTINGS), revision=0)
        db.add(state)
        db.flush()
    return state


def record(db, record_id: str, kind: str | None = None):
    item = db.get(QlibRecord, valid_id(record_id))
    if not item or (kind and item.kind != kind):
        raise ValueError("qlib_record_not_found")
    return item


def serialize(item):
    return {"id": item.id, "kind": item.kind, "parent_id": item.parent_id, "name": item.name, "created_at": item.created_at.replace(tzinfo=timezone.utc).isoformat() if item.created_at else None, **item.payload}


def serialize_job(item):
    return {"id": item.id, "kind": item.kind, "dataset_id": item.dataset_id, "model_id": item.model_id, "config": item.config, "status": item.status, "result_id": item.result_id, "error_code": item.error_code, "created_at": item.created_at.replace(tzinfo=timezone.utc).isoformat() if item.created_at else None}


def list_records(kind, metadata_only=False, market=None):
    with transaction() as db:
        output = []
        query=db.query(QlibRecord).filter_by(kind=kind)
        if market:
            market_field=QlibRecord.payload["market"].as_string()
            query=query.filter(or_(market_field==market,market_field.is_(None)) if market=="CN" else market_field==market)
        for item in query.order_by(QlibRecord.created_at.desc(),QlibRecord.id.desc()).limit(100).yield_per(10):
            payload = serialize(item)
            if market and payload.get("market", "CN") != market:
                continue
            if metadata_only and kind == "selection":
                payload = {k: v for k, v in payload.items() if k not in ("predictions", "names")}
                payload["prediction_count"] = len(item.payload["predictions"])
            output.append(payload)
            if len(output) == 100:
                break
        return output


def get_record(record_id, kind=None):
    with transaction() as db:
        return serialize(record(db, record_id, kind))


def import_dataset(data: DatasetInput, actor):
    payload = data.model_dump(mode="json")
    checksum = content_hash(payload)
    dataset_id = checksum[:32]
    with LOCK, transaction() as db:
        existing = db.get(QlibRecord, dataset_id)
        if existing:
            return serialize(existing)
        destination = root() / "datasets" / f"{dataset_id}.json"
        destination.parent.mkdir(mode=0o700, exist_ok=True)
        temp = destination.with_suffix(".tmp")
        temp.write_text(json.dumps(payload, allow_nan=False, separators=(",", ":")))
        temp.chmod(0o600)
        temp.replace(destination)
        dates = sorted({bar.date.isoformat() for bar in data.bars})
        symbols = sorted({bar.symbol for bar in data.bars})
        item = QlibRecord(id=dataset_id, kind="dataset", name=data.name, payload={
            "source": data.source, "license": data.license, "license_acknowledged": True,
            "market": data.market, "data_kind": data.data_kind, "universe_kind": data.universe_kind,
            "adjustment": data.adjustment, "content_hash": checksum, "rows": len(data.bars),
            "price_basis": data.price_basis,
            "names": data.names, "provenance": data.provenance,
            "symbols": symbols, "dates": len(dates), "start_date": dates[0], "end_date": dates[-1],
            "warnings": [flag for flag, active in (("fixed_research_universe_not_historical_index", data.universe_kind == "fixed_research"), ("snapshot_adjustment_not_point_in_time", data.adjustment == "snapshot_adjusted"), ("synthetic_not_market_validation", data.data_kind == "synthetic"), ("hfq_close_ratio_proxy_preserves_raw_intraday_ratios", bool(data.provenance)), ("historical_ST_and_delisting_status_unverified", bool(data.provenance))) if active],
        })
        db.add(item)
        if data.market!="CN":
            item.payload={**item.payload,"warnings":[w for w in item.payload["warnings"] if w!="hfq_close_ratio_proxy_preserves_raw_intraday_ratios"]}
        if data.price_basis=="provider_snapshot":
            item.payload={**item.payload,"warnings":[*item.payload["warnings"],"provider_snapshot_prices_not_certified_raw_execution"]}
        db.add(QlibAudit(action="dataset_import", actor=actor, target_id=dataset_id, detail={"hash": checksum}))
        db.flush()
        return serialize(item)


def dataset_path(dataset_id):
    valid_id(dataset_id)
    path = root() / "datasets" / f"{dataset_id}.json"
    if path.is_symlink() or not path.is_file():
        raise ValueError("qlib_dataset_artifact_missing")
    return path


def enqueue(dataset_id, config, request_key, actor, model_id=None):
    health = runtime_health()
    if not health["available"]:
        raise ValueError(health["error_code"])
    with LOCK, transaction() as db:
        record(db, dataset_id, "dataset")
        if model_id:
            if model_id in control(db).settings.get("retired_models", []):
                raise ValueError("qlib_model_retired")
            model = record(db, model_id, "model")
            config = ResearchConfig.model_validate(model.payload["config"]).model_dump()
        previous = db.query(QlibJob).filter_by(request_key=request_key).first()
        if previous:
            if previous.dataset_id != dataset_id or previous.model_id != model_id or previous.config != config:
                raise ValueError("qlib_request_key_conflict")
            return serialize_job(previous)
        if db.query(QlibJob).filter(QlibJob.status.in_(("queued", "running"))).count() >= 5:
            raise ValueError("qlib_queue_full")
        job = QlibJob(id=identity(), request_key=request_key, kind="predict" if model_id else "train", dataset_id=dataset_id, model_id=model_id, config=config, status="queued")
        db.add(job)
        db.add(QlibAudit(action=f"job_{job.kind}", actor=actor, target_id=job.id, detail={"dataset_id": dataset_id}))
        db.flush()
        response = serialize_job(job)
    (PREDICTOR if model_id else EXECUTOR).submit(execute_job, response["id"])
    return response


def execute_job(job_id):
    directory = root() / "jobs" / job_id
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        with LOCK, transaction() as db:
            claimed = db.execute(update(QlibJob).where(QlibJob.id == job_id, QlibJob.status == "queued").values(status="running", started_at=now()))
            if claimed.rowcount != 1:
                return
            job = db.get(QlibJob, job_id)
            config = dict(job.config)
            data_id, model_id = job.dataset_id, job.model_id
            dataset = record(db, data_id, "dataset")
            expected_hash = dataset.payload["content_hash"]
            dataset_names = dict(dataset.payload.get("names", {}))
            universe_snapshot_id = dataset.payload.get("provenance", {}).get("universe_snapshot_id")
            task = {"dataset_path": str(dataset_path(data_id)), "config": config}
            if model_id:
                model = record(db, model_id, "model")
                training_record = record(db, model.payload["dataset_id"], "dataset")
                training_path = dataset_path(model.payload["dataset_id"])
                if content_hash(json.loads(training_path.read_text())) != training_record.payload["content_hash"]:
                    raise ValueError("qlib_dataset_hash_mismatch")
                task.update({"training_dataset_path": str(training_path), "segments": model.payload["segments"], "replay_fingerprint": model.payload["replay_fingerprint"]})
        if content_hash(json.loads(dataset_path(data_id).read_text())) != expected_hash:
            raise ValueError("qlib_dataset_hash_mismatch")
        task_path = directory / "task.json"
        task_path.write_text(json.dumps(task, allow_nan=False))
        task_path.chmod(0o600)
        # Isolated process, trusted fixed entrypoint, bounded runtime, private logs.
        with (directory / "worker.log").open("w") as log:
            process = subprocess.Popen([str(worker_python()), str(Path(__file__).with_name("worker.py")), "--task", str(task_path)], stdout=log, stderr=log, env=child_environment(directory), cwd=directory)
            with LOCK:
                PROCESSES[job_id] = process
                with transaction() as db:
                    if db.get(QlibJob, job_id).status == "cancelled":
                        process.terminate()
            try:
                code = process.wait(timeout=900)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
                raise ValueError("qlib_job_timeout")
        with LOCK, transaction() as db:
            job = db.get(QlibJob, job_id)
            if job.status == "cancelled":
                return
            if code != 0:
                raise ValueError("qlib_worker_failed")
            result_path = directory / "result.json"
            if not result_path.is_file() or result_path.stat().st_size > 10000000:
                raise ValueError("qlib_result_invalid")
            output = json.loads(result_path.read_text(), parse_constant=lambda value: (_ for _ in ()).throw(ValueError("qlib_nonfinite_result")))
            if not output.get("predictions") or output.get("feature_set") != "Alpha158" or output.get("runtime", {}).get("pyqlib") != "0.9.7":
                raise ValueError("qlib_result_invalid")
            experiment_id = identity()
            model_id = job.model_id or identity()
            model_payload = {"dataset_id": data_id, "config": config, "segments": output["segments"], "metrics": output["metrics"], "baseline": output["baseline"], "rolling": output["rolling"], "replay_fingerprint": output["replay_fingerprint"], "runtime": output["runtime"], "artifact_hash": hashlib.sha256(result_path.read_bytes()).hexdigest(), "data_kind": output["data_kind"], "universe_kind": output["universe_kind"], "adjustment": output["adjustment"], "experiment_id": experiment_id, "job_id": job_id}
            model_payload["market"] = output.get("market", "CN")
            model_payload["price_basis"] = output.get("price_basis","raw_execution")
            model_payload["universe_snapshot_id"] = universe_snapshot_id
            db.add(QlibRecord(id=experiment_id, kind="experiment", parent_id=model_id, name=f"{config['algorithm']} · {output['asof_date']}", payload={**model_payload, "label": output["label"]}))
            if not job.model_id:
                db.add(QlibRecord(id=model_id, kind="model", parent_id=experiment_id, name=f"Alpha158 + {config['algorithm']}", payload=model_payload))
            run_id = identity()
            prior = latest_selection(model_id, db)
            ranks = {row["symbol"]: row["rank"] for row in prior.payload["predictions"]} if prior else {}
            predictions = [{**row, "rank_change": ranks[row["symbol"]] - row["rank"] if row["symbol"] in ranks else None} for row in output["predictions"]]
            predictions = [{**row,"price_basis":output.get("price_basis","raw_execution")} for row in predictions]
            db.add(QlibRecord(id=run_id, kind="selection", parent_id=model_id, name=output["asof_date"], payload={"model_id": model_id, "dataset_id": data_id, "experiment_id": experiment_id, "asof_date": output["asof_date"], "market": output.get("market", "CN"), "predictions": predictions, "monitor": output["monitor"], "feature_set": "Alpha158", "horizon": config["horizon"], "prediction_scale": output["prediction_scale"], "data_kind": output["data_kind"], "config": config, "names": dataset_names,"selection_scope":"training_cohort" if model_payload["universe_snapshot_id"] else "imported_dataset"}))
            job.status, job.result_id, job.finished_at = "succeeded", run_id, now()
            if job.kind == "train" and job.request_key.startswith("scheduled-"):
                state = control(db)
                state.settings = {**DEFAULT_SETTINGS, **state.settings, "last_retrain_date": output["asof_date"]}
            db.add(QlibAudit(action="job_succeeded", actor="worker", target_id=job_id, detail={"model_id": model_id, "run_id": run_id}))
    except Exception as error:
        with LOCK, transaction() as db:
            job = db.get(QlibJob, job_id)
            if job and job.status != "cancelled":
                job.status = "failed"
                job.error_code = str(error) if isinstance(error, ValueError) and str(error).startswith("qlib_") else "qlib_job_failed"
                job.finished_at = now()
    finally:
        with LOCK:
            PROCESSES.pop(job_id, None)


def jobs():
    with transaction() as db:
        items=db.query(QlibJob).order_by(QlibJob.created_at.desc()).limit(100).all()
        datasets={r.id:r.payload for r in db.query(QlibRecord).filter(QlibRecord.id.in_([j.dataset_id for j in items]))}
        return [{**serialize_job(item),"market":datasets.get(item.dataset_id,{}).get("market","CN")} for item in items]


def cancel_job(job_id, actor):
    with LOCK, transaction() as db:
        job = db.get(QlibJob, valid_id(job_id))
        if not job:
            raise ValueError("qlib_job_not_found")
        if job.status in ("queued", "running"):
            job.status, job.finished_at = "cancelled", now()
            db.add(QlibAudit(action="job_cancelled", actor=actor, target_id=job_id))
            process = PROCESSES.get(job_id)
            if process and process.poll() is None:
                process.terminate()
        return serialize_job(job)


def recover_jobs():
    """Interrupted jobs are explicitly failed; never publish a surviving partial file."""
    with LOCK, transaction() as db:
        for job in db.query(QlibJob).filter(QlibJob.status.in_(("running", "queued"))).all():
            job.status, job.error_code, job.finished_at = "failed", "qlib_job_interrupted", now()
            db.add(QlibAudit(action="job_interrupted", actor="startup", target_id=job.id))
    from . import marketdata_sync
    marketdata_sync.recover()
    from . import universe
    universe.recover()


def shutdown():
    with LOCK:
        for process in PROCESSES.values():
            if process.poll() is None:
                process.terminate()
    from . import marketdata_sync
    marketdata_sync.recover()
    from . import universe
    universe.recover()


def market_champion(state, market="CN"):
    return state.settings.get("champions", {}).get(market, state.champion_id if market == "CN" else None)


def market_mode(state, market="CN"):
    return state.settings.get("champion_modes", {}).get(market, state.settings.get("champion_mode", "research") if market == "CN" else "research")


def governance():
    with LOCK, transaction() as db:
        state = control(db)
        published = {row.target_id for row in db.query(QlibAudit).filter(QlibAudit.action.in_(("publish", "rollback"))).all()}
        retired = set(state.settings.get("retired_models", []))
        champions = {m: market_champion(state, m) for m in ("CN", "HK", "US")}
        models = [{**serialize(model), "state": "retired" if model.id in retired else "champion" if model.id in champions.values() else "previous" if model.id in published else "candidate"} for model in db.query(QlibRecord).filter_by(kind="model").order_by(QlibRecord.created_at.desc()).limit(100)]
        audit = [{"id": row.id, "action": row.action, "actor": row.actor, "target_id": row.target_id, "detail": row.detail, "created_at": row.created_at.replace(tzinfo=timezone.utc).isoformat()} for row in db.query(QlibAudit).order_by(QlibAudit.id.desc()).limit(100)]
        return {"champion_id": state.champion_id, "champions": champions, "revision": state.revision, "settings": {**DEFAULT_SETTINGS, **state.settings}, "models": models, "audit": audit}


def publish(model_id, revision, reason, mode, actor, rollback=False):
    with LOCK, transaction() as db:
        model = record(db, model_id, "model")
        state = control(db)
        if state.revision != revision:
            raise ValueError("qlib_revision_conflict")
        if model_id in state.settings.get("retired_models", []):
            raise ValueError("qlib_model_retired")
        if rollback and not db.query(QlibAudit).filter(QlibAudit.target_id == model_id, QlibAudit.action.in_(("publish", "rollback"))).first():
            raise ValueError("qlib_rollback_unpublished")
        payload = model.payload
        market = payload.get("market", "CN")
        metrics, config = payload["metrics"], payload["config"]
        if mode == "validated":
            if market != "CN":
                raise ValueError("qlib_market_research_only")
            if payload.get("price_basis","raw_execution")!="raw_execution":
                raise ValueError("qlib_research_only_data")
            if payload["data_kind"] != "market" or payload["universe_kind"] != "historical_membership" or payload["adjustment"] != "point_in_time":
                raise ValueError("qlib_research_only_data")
            ic, drawdown = metrics.get("rank_ic"), metrics["backtest"].get("max_drawdown")
            if metrics["ic_dates"] < config["min_test_days"] or ic is None or ic < config["min_ic"] or drawdown is None or drawdown > config["max_drawdown"]:
                raise ValueError("qlib_publish_gate_failed")
            baseline_ic = payload["baseline"].get("rank_ic")
            if baseline_ic is not None and ic < baseline_ic:
                raise ValueError("qlib_baseline_not_beaten")
            folds = payload.get("rolling", [])
            if len(folds) < 3 or any(fold["metrics"]["ic_dates"] < config["min_test_days"] or fold["metrics"]["rank_ic"] is None or fold["metrics"]["rank_ic"] < config["min_ic"] or fold["metrics"]["backtest"]["max_drawdown"] is None or fold["metrics"]["backtest"]["max_drawdown"] > config["max_drawdown"] for fold in folds):
                raise ValueError("qlib_rolling_gate_failed")
            from .assessment import assess
            release = assess(payload, "validated")
            if not release["eligible"]:
                raise ValueError(release["reasons"][0])
        previous = market_champion(state, market)
        settings = {**DEFAULT_SETTINGS, **state.settings, "champions": {**state.settings.get("champions", {}), market: model_id}, "champion_modes": {**state.settings.get("champion_modes", {}), market: mode}}
        if market == "CN":
            settings["champion_mode"] = mode
        changed = db.execute(update(QlibControl).where(QlibControl.id == 1, QlibControl.revision == revision).values(champion_id=model_id if market == "CN" else state.champion_id, revision=revision + 1, settings=settings, updated_at=now()))
        if changed.rowcount != 1:
            raise ValueError("qlib_revision_conflict")
        db.add(QlibAudit(action="rollback" if rollback else "publish", actor=actor, target_id=model_id, detail={"previous_model_id": previous, "reason": reason, "mode": mode, "revision": revision + 1}))
    return governance()


def retire(model_id, revision, reason, actor):
    with LOCK, transaction() as db:
        record(db, model_id, "model")
        state = control(db)
        if revision != state.revision:
            raise ValueError("qlib_revision_conflict")
        if model_id in {market_champion(state, m) for m in ("CN", "HK", "US")}:
            raise ValueError("qlib_retire_champion_forbidden")
        retired = sorted(set(state.settings.get("retired_models", [])) | {model_id})
        result = db.execute(update(QlibControl).where(QlibControl.id == 1, QlibControl.revision == revision).values(revision=revision + 1, settings={**state.settings, "retired_models": retired}, updated_at=now()))
        if result.rowcount != 1:
            raise ValueError("qlib_revision_conflict")
        db.add(QlibAudit(action="model_retired", actor=actor, target_id=model_id, detail={"reason": reason}))
    return governance()


def latest_selection(model_id, db):
    return db.query(QlibRecord).filter_by(kind="selection", parent_id=model_id).order_by(QlibRecord.created_at.desc(), QlibRecord.id.desc()).first()


def freshness(asof_date, market="CN"):
    from .universe import completed_day
    try:
        return asof_date == completed_day(market)
    except ValueError:
        return False


def opportunities(market="CN", page=1, limit=None, search="", top=0, source="all", holding="all", availability="all", run_id=None, view="all"):
    with LOCK, transaction() as db:
        state = control(db)
        champion = market_champion(state, market)
        selection = record(db, run_id, "selection") if run_id else latest_selection(champion, db) if champion else None
        if not selection:
            return {"items": [], "total": 0, "prediction_count": 0, "status": "no_champion" if not champion else "no_prediction", "run": None, "mode": market_mode(state, market)}
        run = serialize(selection)
        if not run_id and run.get("selection_scope")=="training_cohort":
            return {"items":[],"total":0,"prediction_count":0,"status":"no_prediction","run":None,"mode":market_mode(state,market)}
        if run.get("market", "CN") != market:
            raise ValueError("qlib_model_market_mismatch")
        rows = run["predictions"]
        count = len(rows)
        from src.platform.persistence.models import Stock, Position
        stocks = db.query(Stock).filter_by(market=market).all()
        codes = {(s.symbol.zfill(5) if market=="HK" and s.symbol.isdigit() else s.symbol.upper()):s.id for s in stocks}
        owned = {p.stock_id for p in db.query(Position).filter(Position.quantity>0)}
        basket = {b["symbol"] for b in state.settings.get("basket",[])}
        def watched(row):
            return row["symbol"] in basket if run.get("data_kind")=="synthetic" else (row["symbol"][2:] if market=="CN" else row["symbol"][3:]) in codes
        watched_count=sum(watched(r) for r in rows)
        selected = set([r["symbol"] for r in sorted(rows,key=lambda r:r["rank"]) if r["tradable"]][:run.get("config",{}).get("topk",10)])
        if source != "all" or holding != "all":
            from src.platform.persistence.models import Stock, Position
            stocks = db.query(Stock).filter_by(market=market).all()
            codes = {(s.symbol.zfill(5) if market=="HK" and s.symbol.isdigit() else s.symbol.upper()): s.id for s in stocks}
            owned = {p.stock_id for p in db.query(Position).filter(Position.quantity > 0)}
            def code(symbol):
                return symbol[2:] if market == "CN" else symbol[3:]
            rows = [r for r in rows if (source != "watched" or code(r["symbol"]) in codes) and (holding == "all" or (codes.get(code(r["symbol"])) in owned) == (holding == "held"))]
            if run.get("data_kind")=="synthetic":
                basket={b["symbol"] for b in state.settings.get("basket",[])}
                rows=[r for r in run["predictions"] if source!="watched" or r["symbol"] in basket]
        rows = [r for r in rows if (not top or r["rank"] <= max(1, math.ceil(count * top / 100))) and (availability == "all" or bool(r["tradable"]) == (availability == "tradable")) and (not search or search.casefold() in f"{r['symbol']} {run.get('names',{}).get(r['symbol'],'')}".casefold())]
        if view == "featured":
            rows = [r for r in rows if r["symbol"] in selected]
        total = len(rows)
        rows = [{**r,"portfolio_candidate":r["symbol"] in selected} for r in rows]
        if limit is not None:
            rows = rows[(page-1)*limit:page*limit]
        from .opportunity_context import enrich
        rows = enrich(run, rows)
        from .assessment import assess
        model = db.get(QlibRecord, run["model_id"])
        run["assessment"] = assess(model.payload if model else None, market_mode(state, market) if run["model_id"] == champion else "research")
        names = run.get("names", {})
        run["watched_count"] = watched_count
        if limit is not None:
            names = {r["symbol"]: names.get(r["symbol"], "") for r in rows}
        return {"items": rows, "total": total, "prediction_count": count, "page": page, "limit": limit, "status": "history" if run_id else "ready" if freshness(run["asof_date"], market) else "stale", "run": {**{key: value for key, value in run.items() if key not in ("predictions", "names")}, "names": names}, "mode": market_mode(state, market)}


def forward_monitor(db, champion_id):
    """Ledger observations, never a reconstructed historical model return."""
    from src.platform.persistence.models import PaperTradingAccount, PaperTradingPosition, PaperTradingTrade
    from src.platform.persistence.qlib_models import QlibExecution
    positions = db.query(PaperTradingPosition).filter_by(status="open").all()
    owned = [row for row in positions if (row.research_meta or {}).get("model_id")]
    champion_positions = [row for row in owned if row.research_meta["model_id"] == champion_id]
    trades = [row for row in db.query(PaperTradingTrade).all() if (row.meta or {}).get("research", {}).get("model_id") == champion_id] if champion_id else []
    value = lambda row: (row.current_price or row.entry_price) * row.quantity
    account = db.query(PaperTradingAccount).first()
    equity = account.current_capital + sum(value(row) for row in positions) if account else None
    events = db.query(QlibExecution).filter_by(model_id=champion_id).all() if champion_id else []
    return {"champion_closed_trades": len(trades), "champion_realized_net_pnl": sum(row.pnl for row in trades),
            "champion_open_positions": len(champion_positions), "champion_unrealized_net_pnl": sum(row.unrealized_pnl for row in champion_positions),
            "qlib_market_value": sum(value(row) for row in owned),
            "qlib_equity_exposure": sum(value(row) for row in owned) / equity if equity and equity > 0 else None,
            "max_position_equity_weight": max((value(row) for row in owned), default=0) / equity if equity and equity > 0 else None,
            "filled_intents": sum(row.status == "filled" for row in events), "rejected_intents": sum(row.status == "rejected" for row in events),
            "asof": now().isoformat()}


def monitor():
    """Observable new-system health; never triggers an unreviewed model switch."""
    with LOCK, transaction() as db:
        state = control(db)
        runs = db.query(QlibRecord).filter_by(kind="selection", parent_id=state.champion_id).order_by(QlibRecord.created_at.desc()).limit(2).all() if state.champion_id else []
        latest = runs[0].payload if runs else None
        errors = db.query(QlibJob).filter_by(status="failed").order_by(QlibJob.created_at.desc()).limit(5).all()
        top_change = None
        if len(runs) == 2:
            k = latest["config"]["topk"]
            current = {row["symbol"] for row in latest["predictions"][:k]}
            previous = {row["symbol"] for row in runs[1].payload["predictions"][:k]}
            top_change = len(current - previous) / max(1, len(current))
        return {"champion_id": state.champion_id, "prediction_date": latest["asof_date"] if latest else None,
                "fresh": freshness(latest["asof_date"]) if latest else False,
                "coverage": latest.get("monitor", {}) if latest else {}, "topk_change_fraction": top_change,
                "recent_failures": [{"id": job.id, "error_code": job.error_code} for job in errors],
                "schedule": {**DEFAULT_SETTINGS, **state.settings}, "forward": forward_monitor(db, state.champion_id)}


def make_decision(run_id, actor, acknowledge_research=False):
    health = runtime_health()
    if not health["available"]:
        raise ValueError(health["error_code"])
    from .paper_plans import confirm
    return confirm(run_id, actor, acknowledge_research=acknowledge_research)


def templates():
    with transaction() as db:
        return [{"id": row.id, "name": row.name, "config": row.config} for row in db.query(QlibTemplate).order_by(QlibTemplate.created_at.desc())]


def executions():
    from src.platform.persistence.qlib_models import QlibExecution
    from src.platform.persistence.models import PaperTradingPosition
    with transaction() as db:
        events = [{"id": row.id, "decision_id": row.decision_id, "run_id": row.run_id, "model_id": row.model_id,
                   "symbol": row.symbol, "side": row.side, "status": row.status, "quantity": row.quantity,
                   "price": row.price, "reason": row.reason, "position_id": row.position_id}
                  for row in db.query(QlibExecution).order_by(QlibExecution.created_at.desc()).limit(200)]
        positions = [{"id": row.id, "symbol": row.stock_symbol, "quantity": row.quantity, "status": row.status,
                      "research": row.research_meta or {}} for row in db.query(PaperTradingPosition).filter_by(status="open").all()]
        return {"items": events, "positions": positions, "decisions": list_records("decision")}


def basket():
    with LOCK, transaction() as db:
        return control(db).settings.get("basket", [])


def set_basket(run_id, symbol, included, actor):
    with LOCK, transaction() as db:
        run = record(db, run_id, "selection")
        if symbol not in {row["symbol"] for row in run.payload["predictions"]}:
            raise ValueError("qlib_symbol_not_in_run")
        state = control(db)
        items = [row for row in state.settings.get("basket", []) if row["symbol"] != symbol]
        if included:
            if len(items) >= 100:
                raise ValueError("qlib_basket_full")
            items.append({"symbol": symbol, "run_id": run_id})
        state.settings = {**state.settings, "basket": items}
        db.add(QlibAudit(action="basket_updated", actor=actor, target_id=run_id, detail={"symbol": symbol, "included": included}))
        return items


def compare_experiments(ids):
    with transaction() as db:
        items = [serialize(record(db, item, "experiment")) for item in ids]
    def comparison_key(item):
        return [item.get("market","CN"), item["dataset_id"], item["segments"], {key: value for key, value in item["config"].items() if key not in ("algorithm", "num_boost_round")}]
    comparable = all(comparison_key(item) == comparison_key(items[0]) for item in items)
    return {"comparable": comparable, "reason": "same_cohort" if comparable else "qlib_comparison_cohort_differs", "items": items}


def save_template(name, config, actor):
    with transaction() as db:
        template = QlibTemplate(id=identity(), name=name, config=config)
        db.add(template)
        db.add(QlibAudit(action="template_saved", actor=actor, target_id=template.id))
        return {"id": template.id, "name": template.name, "config": template.config}


def save_schedule(settings, actor):
    with LOCK, transaction() as db:
        if settings.get("dataset_id"):
            record(db, settings["dataset_id"], "dataset")
        state = control(db)
        if settings["enabled"] and not settings.get("dataset_id") and not state.settings.get("marketdata_source"):
            raise ValueError("qlib_schedule_dataset_required")
        state.settings = {**DEFAULT_SETTINGS, **state.settings, **settings}
        db.add(QlibAudit(action="schedule_saved", actor=actor, detail=settings))
    return governance()


def scheduled_tick(current=None):
    """Only opt-in. Training yields a candidate; it never publishes itself."""
    from zoneinfo import ZoneInfo
    from src.platform.scheduling.trading_calendar import is_trading_day
    moment = (current or now()).astimezone(ZoneInfo("Asia/Shanghai"))
    from . import universe
    full_sync = universe.scheduled_tick()
    if full_sync is not None:
        return full_sync
    from . import marketdata_sync
    sync = marketdata_sync.scheduled_sync(moment)
    if sync is not None:
        return sync
    with LOCK, transaction() as db:
        state = control(db)
        settings = {**DEFAULT_SETTINGS, **state.settings}
        if not settings["enabled"] or not settings["dataset_id"] or not is_trading_day("CN", moment.date()) or moment.hour < settings["daily_hour"]:
            return {"status": "idle"}
        dataset = record(db, settings["dataset_id"], "dataset")
        if dataset.payload["end_date"] != moment.date().isoformat():
            return {"status": "data_stale"}
        model = record(db, state.champion_id, "model") if state.champion_id else None
        config = model.payload["config"] if model else ResearchConfig().model_dump()
        day = moment.date().isoformat()
        previous = settings.get("last_retrain_date")
        retrain = not previous or (moment.date() - datetime.fromisoformat(previous).date()).days >= settings["retrain_every_days"]
        model_id = None if retrain else state.champion_id
        champion_id = state.champion_id
        dataset_id = settings["dataset_id"]
        retired = settings.get("retired_models", [])
    def scheduled_job(job_config, base_key, model_id=None):
        # Bounded restart/timeout retries. User cancellations are not retried.
        for attempt in range(3):
            request_key = base_key if attempt == 0 else f"{base_key}-retry-{attempt}"
            result = enqueue(dataset_id, job_config, request_key, "scheduler", model_id=model_id)
            if result["status"] != "failed":
                return result
        return result
    prefix = f"scheduled-{day}-{dataset_id}"
    prediction_job = scheduled_job(config, f"{prefix}-predict-{champion_id}", champion_id) if champion_id else None
    job = scheduled_job(config, f"{prefix}-train") if retrain else prediction_job
    # A reviewed challenger is shadow-only until a separate publication action.
    with transaction() as db:
        challenger = db.query(QlibRecord).filter(QlibRecord.kind == "model", QlibRecord.id != champion_id, QlibRecord.id.notin_(retired)).order_by(QlibRecord.created_at.desc()).first()
        challenger_data = serialize(challenger) if challenger else None
    if challenger_data:
        scheduled_job(challenger_data["config"], f"{prefix}-shadow-{challenger_data['id']}", challenger_data["id"])
    with LOCK, transaction() as db:
        state = control(db)
        settings = {**DEFAULT_SETTINGS, **state.settings, "last_daily_date": day}
        state.settings = settings
    return {"status": "failed" if job and job["status"] == "failed" else "cancelled" if job and job["status"] == "cancelled" else "queued", "job": job, "prediction_job": prediction_job}
