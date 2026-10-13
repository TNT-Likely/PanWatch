"""Controlled service-state tests, separate from the real Qlib model suite."""
from datetime import datetime, timezone
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from src.modules.qlib_research import api, service
from src.modules.qlib_research.contracts import DatasetInput, ResearchConfig
from src.platform.persistence.qlib_models import QlibAudit, QlibControl, QlibJob, QlibRecord
from src.platform.persistence.database import Base
from src.platform.persistence.models import PaperTradingAccount, PaperTradingPosition, PaperTradingTrade
from tests.qlib_fixture import panel


@pytest.fixture
def storage(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'unit.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    (tmp_path / "qlib").mkdir()
    monkeypatch.setattr(service, "SessionLocal", factory)
    monkeypatch.setattr(service, "root", lambda: tmp_path / "qlib")
    monkeypatch.setattr(service, "now", lambda: datetime(2026, 10, 13, 12, tzinfo=timezone.utc))
    monkeypatch.setattr(service, "runtime_health", lambda: {"available": True, "runtime": {"pyqlib": "0.9.7"}})
    monkeypatch.setattr(service, "EXECUTOR", SimpleNamespace(submit=Mock()))
    monkeypatch.setattr(service, "PREDICTOR", SimpleNamespace(submit=Mock()))
    yield factory
    engine.dispose()


def model(storage, *, data_kind="synthetic", config=None):
    ident = uuid.uuid4().hex
    metrics = {"rank_ic": .2, "ic_dates": 30, "backtest": {"max_drawdown": .1}}
    with storage() as db:
        db.add(QlibRecord(id=ident, kind="model", name="controlled model", payload={
            "dataset_id": uuid.uuid4().hex, "config": config or ResearchConfig().model_dump(),
            "metrics": metrics, "baseline": {"rank_ic": .1}, "rolling": [{"metrics": metrics}] * 3,
            "data_kind": data_kind, "universe_kind": "historical_membership", "adjustment": "point_in_time"}))
        db.commit()
    return ident


def test_dataset_content_idempotency_and_private_artifact(storage):
    data = DatasetInput.model_validate(panel(end=datetime(2026, 10, 13).date()))
    first = service.import_dataset(data, "test")
    second = service.import_dataset(data, "test")
    assert first["id"] == second["id"]
    path = service.dataset_path(first["id"])
    assert path.stat().st_mode & 0o777 == 0o600
    assert "synthetic_not_market_validation" in first["warnings"]
    with storage() as db:
        assert db.query(QlibAudit).filter_by(action="dataset_import").count() == 1


def test_job_idempotency_cancel_and_restart_never_publish_partial_results(storage):
    data = service.import_dataset(DatasetInput.model_validate(panel()), "test")
    config = ResearchConfig().model_dump()
    job = service.enqueue(data["id"], config, "unique-request-key", "test")
    assert service.enqueue(data["id"], config, "unique-request-key", "test")["id"] == job["id"]
    service.EXECUTOR.submit.assert_called_once()
    with pytest.raises(ValueError, match="request_key_conflict"):
        service.enqueue(data["id"], {**config, "topk": 2}, "unique-request-key", "test")
    assert service.cancel_job(job["id"], "test")["status"] == "cancelled"
    pending = service.enqueue(data["id"], config, "second-request-key", "test")
    service.recover_jobs()
    with storage() as db:
        row = db.get(QlibJob, pending["id"])
        assert row.status == "failed" and row.error_code == "qlib_job_interrupted"
        assert db.query(QlibRecord).filter_by(kind="model").count() == 0


def test_unavailable_worker_cannot_enqueue_or_fallback(storage, monkeypatch):
    monkeypatch.setattr(service, "runtime_health", lambda: {"available": False, "error_code": "qlib_worker_missing"})
    with pytest.raises(ValueError, match="qlib_worker_missing"):
        service.enqueue("a" * 32, {}, "not-enqueued", "test")
    assert service.opportunities()["items"] == []
    assert service.opportunities()["status"] == "no_champion"


def test_publication_requires_review_cas_gates_and_keeps_history(storage):
    first, second = model(storage), model(storage)
    with pytest.raises(ValueError, match="research_only_data"):
        service.publish(first, 0, "reviewed test model", "validated", "test")
    state = service.publish(first, 0, "reviewed test model", "research", "test")
    assert state["champion_id"] == first and state["revision"] == 1
    with pytest.raises(ValueError, match="revision_conflict"):
        service.publish(second, 0, "stale review must fail", "research", "test")
    service.publish(second, 1, "review new candidate", "research", "test")
    service.publish(first, 2, "rollback already reviewed version", "research", "test", rollback=True)
    state = service.governance()
    assert state["champion_id"] == first and state["revision"] == 3
    with storage() as db:
        assert db.query(QlibRecord).filter_by(kind="model").count() == 2
        assert db.query(QlibAudit).count() == 3
    with pytest.raises(ValueError, match="rollback_unpublished"):
        service.publish(model(storage), 3, "unpublished rollback", "research", "test", rollback=True)


def test_validated_gates_are_pre_registered_in_frozen_config(storage):
    candidate = model(storage, data_kind="market", config=ResearchConfig(min_ic=.5).model_dump())
    with pytest.raises(ValueError, match="publish_gate_failed"):
        service.publish(candidate, 0, "no bypass of preset threshold", "validated", "test")
    assert service.governance()["champion_id"] is None


def test_empty_and_stale_qlib_output_never_produces_orders(storage):
    candidate = model(storage)
    service.publish(candidate, 0, "reviewed synthetic fixture", "research", "test")
    assert service.opportunities()["status"] == "no_prediction"
    run = uuid.uuid4().hex
    with storage() as db:
        db.add(QlibRecord(id=run, kind="selection", parent_id=candidate, name="stale",
                         payload={"model_id": candidate, "asof_date": "2026-09-30", "predictions": []}))
        db.commit()
    assert service.opportunities()["status"] == "stale"
    with pytest.raises(ValueError, match="prediction_stale"):
        service.make_decision(run, "test", True)
    assert service.list_records("decision") == []


def test_permission_required_even_before_global_auth_setup(storage):
    app = FastAPI()
    app.include_router(api.router, prefix="/qlib")
    app.dependency_overrides[api.current_actor] = lambda: None
    with TestClient(app) as client:
        assert client.get("/qlib/datasets").status_code == 401
        assert client.post("/qlib/datasets", json={}).status_code == 401
    app.dependency_overrides[api.current_actor] = lambda: "authenticated-test-user"
    with TestClient(app) as client:
        assert client.get("/qlib/datasets").status_code == 200
        assert client.post("/qlib/datasets", json={"python": "malicious"}).status_code == 422


def test_retirement_requires_review_and_never_changes_artifacts(storage):
    champion, candidate = model(storage), model(storage)
    service.publish(champion, 0, "review first champion", "research", "test")
    with pytest.raises(ValueError, match="retire_champion_forbidden"):
        service.retire(champion, 1, "cannot retire live champion", "test")
    with pytest.raises(ValueError, match="revision_conflict"):
        service.retire(candidate, 0, "stale retirement", "test")
    original = service.get_record(candidate)
    state = service.retire(candidate, 1, "reject candidate after review", "test")
    assert state["champion_id"] == champion and state["revision"] == 2
    assert service.get_record(candidate) == original
    assert next(row for row in state["models"] if row["id"] == candidate)["state"] == "retired"
    with pytest.raises(ValueError, match="model_retired"):
        service.publish(candidate, 2, "no resurrecting retired model", "research", "test")


def test_opt_in_schedule_is_fresh_idempotent_and_does_not_publish(storage, monkeypatch):
    moment = datetime(2026, 10, 13, 12, tzinfo=timezone.utc)
    monkeypatch.setattr("src.platform.scheduling.trading_calendar.is_trading_day", lambda *args: True)
    assert service.scheduled_tick(moment) == {"status": "idle"}
    data = service.import_dataset(DatasetInput.model_validate(panel(end=moment.date())), "test")
    service.save_schedule({"enabled": True, "dataset_id": data["id"], "daily_hour": 18, "retrain_every_days": 30}, "test")
    assert service.scheduled_tick(moment.replace(hour=1)) == {"status": "idle"}
    first = service.scheduled_tick(moment)
    second = service.scheduled_tick(moment)
    assert first["job"]["id"] == second["job"]["id"]
    service.EXECUTOR.submit.assert_called_once()
    assert service.governance()["champion_id"] is None
    assert service.governance()["settings"]["last_retrain_date"] == ""
    assert service.scheduled_tick(moment.replace(day=14)) == {"status": "data_stale"}
    service.cancel_job(first["job"]["id"], "test")
    assert service.scheduled_tick(moment)["status"] == "cancelled"
    service.EXECUTOR.submit.assert_called_once()
    monkeypatch.setattr("src.platform.scheduling.trading_calendar.is_trading_day", lambda *args: False)
    assert service.scheduled_tick(moment) == {"status": "idle"}


def test_schedule_retries_are_bounded_without_advancing_success_date(storage, monkeypatch):
    moment = datetime(2026, 10, 13, 12, tzinfo=timezone.utc)
    monkeypatch.setattr("src.platform.scheduling.trading_calendar.is_trading_day", lambda *args: True)
    data = service.import_dataset(DatasetInput.model_validate(panel(end=moment.date())), "test")
    service.save_schedule({"enabled": True, "dataset_id": data["id"], "daily_hour": 18, "retrain_every_days": 30}, "test")
    for _ in range(3):
        result = service.scheduled_tick(moment)
        with storage() as db:
            job = db.get(QlibJob, result["job"]["id"])
            job.status, job.error_code = "failed", "qlib_job_timeout"
            db.commit()
    assert service.scheduled_tick(moment)["status"] == "failed"
    assert service.EXECUTOR.submit.call_count == 3
    assert service.governance()["settings"]["last_retrain_date"] == ""


def test_tampered_dataset_fails_before_worker_and_no_partial_model(storage, monkeypatch):
    data = service.import_dataset(DatasetInput.model_validate(panel()), "test")
    job = service.enqueue(data["id"], ResearchConfig().model_dump(), "tamper-check", "test")
    service.dataset_path(data["id"]).write_text('{}')
    launch = Mock()
    monkeypatch.setattr(service.subprocess, "Popen", launch)
    service.execute_job(job["id"])
    launch.assert_not_called()
    assert service.jobs()[0]["error_code"] == "qlib_dataset_hash_mismatch"
    assert service.list_records("model") == []


def test_forward_monitor_separates_ledger_observations_and_legacy_holdings(storage):
    champion = model(storage)
    service.publish(champion, 0, "reviewed paper model", "research", "test")
    with storage() as db:
        db.add(PaperTradingAccount(initial_capital=10000, current_capital=6000))
        db.add(PaperTradingPosition(stock_symbol="600000", quantity=100, entry_price=10, current_price=12,
                                   unrealized_pnl=190, research_meta={"model_id": champion}))
        db.add(PaperTradingPosition(stock_symbol="600001", quantity=100, entry_price=20, current_price=20))
        db.add(PaperTradingTrade(stock_symbol="600002", quantity=100, entry_price=10, exit_price=11, pnl=90,
                                meta={"research": {"model_id": champion}}))
        db.add(PaperTradingTrade(stock_symbol="600003", quantity=100, entry_price=10, exit_price=11, pnl=999))
        db.commit()
    observed = service.monitor()["forward"]
    assert observed["champion_realized_net_pnl"] == 90
    assert observed["champion_unrealized_net_pnl"] == 190
    assert observed["champion_closed_trades"] == 1
    assert observed["qlib_market_value"] == 1200
    assert observed["qlib_equity_exposure"] == pytest.approx(1200 / 9200)


def test_worker_timeout_kills_only_own_process_and_never_publishes(storage, monkeypatch):
    import subprocess
    data = service.import_dataset(DatasetInput.model_validate(panel()), "test")
    job = service.enqueue(data["id"], ResearchConfig().model_dump(), "timeout-check", "test")
    process = Mock()
    process.wait.side_effect = [subprocess.TimeoutExpired("controlled-worker", 900), 0]
    monkeypatch.setattr(service.subprocess, "Popen", Mock(return_value=process))
    service.execute_job(job["id"])
    process.kill.assert_called_once()
    assert service.jobs()[0]["error_code"] == "qlib_job_timeout"
    assert job["id"] not in service.PROCESSES
    assert service.list_records("model") == []


def test_retirement_migration_keeps_account_positions_and_trades(storage):
    from src.platform.persistence.migrations import _m138_qlib_position_lineage, _m139_retire_selection_tables
    with storage() as db:
        db.add(PaperTradingAccount(initial_capital=10000, current_capital=9000))
        db.add(PaperTradingPosition(stock_symbol="600000", stock_market="CN", quantity=100, entry_price=10))
        db.add(PaperTradingTrade(stock_symbol="600000", stock_market="CN", quantity=100, entry_price=9, exit_price=10, pnl=99))
        db.commit()
    with storage.kw["bind"].begin() as conn:
        conn.execute(text("CREATE TABLE entry_candidates (id INTEGER PRIMARY KEY)"))
        _m138_qlib_position_lineage(conn)
        _m139_retire_selection_tables(conn)
        _m139_retire_selection_tables(conn)
    with storage() as db:
        assert db.query(PaperTradingAccount).one().current_capital == 9000
        assert db.query(PaperTradingPosition).count() == db.query(PaperTradingTrade).count() == 1
    assert "entry_candidates" not in inspect(storage.kw["bind"]).get_table_names()
