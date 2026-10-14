"""Controlled ingestion/scheduling tests; live provider replay is separate."""
from datetime import date, datetime, timezone
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from src.modules.qlib_research import marketdata_sync as sync, service
from src.modules.qlib_research.contracts import MarketDataSourceInput
from src.platform.persistence.qlib_models import QlibSyncJob
from tests.test_qlib_service import storage  # noqa: F401
from tests.qlib_fixture import panel


@pytest.fixture
def source(storage, monkeypatch):
    payload = panel(end=date(2026, 10, 13))
    symbols = sorted({b["symbol"] for b in payload["bars"]})
    quotes = [SimpleNamespace(symbol=s[2:], name=f"Research {s}", quote_date=date(2026, 10, 13)) for s in symbols]
    md = SimpleNamespace(research_sources=lambda: ["tencent", "eastmoney"],
        research_calendar=lambda **kw: payload["calendar"], quotes=lambda *args, **kw: quotes,
        research_history=lambda s, **kw: SimpleNamespace(bars=[b for b in payload["bars"] if b["symbol"] == s]))
    monkeypatch.setattr(sync, "get_market_data", lambda: md)
    monkeypatch.setattr(sync, "EXECUTOR", SimpleNamespace(submit=Mock()))
    sync.configure(MarketDataSourceInput(symbols=symbols, license="Controlled fixture only", license_acknowledged=True), "test")
    return md


def run_sync(source, *, key="initialize-request", train=True, reset=False):
    job = sync.enqueue(key, "test", train_when_ready=train, reinitialize=reset)
    sync.execute(job["id"])
    return sync.status()["job"]


def test_complete_pool_is_atomic_research_only_and_explicit_train(source, storage):
    job = run_sync(source)
    assert job["status"] == "succeeded"
    assert job["train_job_id"]
    dataset = service.get_record(job["dataset_id"])
    assert dataset["data_kind"] == "market"
    assert dataset["adjustment"] == "snapshot_adjusted" and dataset["universe_kind"] == "fixed_research"
    assert dataset["provenance"]["volume_unit"] == "shares"
    assert dataset["names"]
    assert service.governance()["champion_id"] is None
    assert service.list_records("selection") == []
    assert service.list_records("decision") == []
    assert sync.enqueue("initialize-request", "test", train_when_ready=True)["id"] == job["id"]
    service.EXECUTOR.submit.assert_called_once()


def test_partial_failure_keeps_previous_dataset_and_never_trains(source, monkeypatch):
    initial = run_sync(source, train=False)
    previous = initial["dataset_id"]
    monkeypatch.setattr(source, "research_history", lambda *a, **kw: SimpleNamespace(bars=[]))
    failure = run_sync(source, key="failed-request-key")
    assert failure["status"] == "failed"
    assert failure["dataset_id"] is None
    assert sync.status()["dataset_id"] == previous
    assert len(service.list_records("dataset")) == 1
    service.EXECUTOR.submit.assert_not_called()


def test_cancel_and_recovery_prevent_late_publication(source, storage):
    job = sync.enqueue("cancel-before-start", "test", train_when_ready=True)
    assert sync.cancel(job["id"], "test")["status"] == "cancelled"
    sync.execute(job["id"])
    assert service.list_records("dataset") == []
    next_job = sync.enqueue("restart-before-start", "test", train_when_ready=True)
    service.recover_jobs()
    sync.execute(next_job["id"])
    assert sync.status()["job"]["error_code"] == "qlib_job_interrupted"
    assert service.list_records("dataset") == []


def test_config_change_invalidates_running_job(source):
    job = sync.enqueue("configured-request", "test", train_when_ready=True)
    body = sync.status()["source"]
    sync.configure(MarketDataSourceInput(**{**body, "license": "Changed controlled authorization"}), "test")
    sync.execute(job["id"])
    assert sync.status()["job"]["error_code"] == "qlib_source_configuration_changed"
    assert service.list_records("dataset") == []


def test_source_fallback_is_whole_pool_and_no_enabled_sources_blocks_config(source, monkeypatch):
    original = source.research_calendar
    monkeypatch.setattr(source, "research_calendar", lambda **kw: [] if kw["vendor"] == "tencent" else original(**kw))
    job = run_sync(source, train=False)
    assert service.get_record(job["dataset_id"])["source"] == "marketdata:eastmoney"
    monkeypatch.setattr(source, "research_sources", lambda: [])
    with pytest.raises(ValueError, match="source_disabled"):
        sync.configure(MarketDataSourceInput(**sync.status()["source"]), "test")


def test_changed_history_blocks_append_but_explicit_reinitialize_can_train(source, monkeypatch):
    first = run_sync(source, train=False)
    original = source.research_history
    def changed(s, **kw):
        bars = [dict(b) for b in original(s, **kw).bars]
        bars[0]["factor"] *= 1.1
        return SimpleNamespace(bars=bars)
    monkeypatch.setattr(source, "research_history", changed)
    failure = run_sync(source, key="changed-history-key")
    assert failure["error_code"] == "qlib_history_changed_reinitialize_required"
    assert sync.status()["dataset_id"] == first["dataset_id"]
    reinit = run_sync(source, key="explicit-reinit-key", reset=True)
    assert reinit["status"] == "succeeded" and reinit["dataset_id"] != first["dataset_id"]
    assert len(service.list_records("dataset")) == 2


def test_daily_scheduler_ingests_before_prediction_and_is_opt_in(source):
    moment = datetime(2026, 10, 13, 12, tzinfo=timezone.utc)
    assert service.scheduled_tick(moment)["status"] == "idle"
    sync.configure(MarketDataSourceInput(**{**sync.status()["source"], "automatic": True}), "test")
    first = service.scheduled_tick(moment)
    assert first["status"] == "syncing"
    assert service.scheduled_tick(moment)["sync_job"]["id"] == first["sync_job"]["id"]
    assert service.jobs() == []
    sync.execute(first["sync_job"]["id"])
    assert sync.status()["job"]["status"] == "succeeded"
    result = service.scheduled_tick(moment)
    assert result["status"] == "queued" and result["job"]["kind"] == "train"
    assert service.governance()["champion_id"] is None


def test_daily_failure_has_bounded_retries_without_replacing_data(source, monkeypatch):
    sync.configure(MarketDataSourceInput(**{**sync.status()["source"], "automatic": True}), "test")
    monkeypatch.setattr(source, "research_calendar", lambda **kw: [])
    moment = datetime(2026, 10, 13, 12, tzinfo=timezone.utc)
    for _ in range(3):
        result = service.scheduled_tick(moment)
        assert result["status"] == "syncing"
        sync.execute(result["sync_job"]["id"])
    assert service.scheduled_tick(moment)["status"] == "data_sync_failed"
    assert sync.status()["dataset_id"] is None
    service.EXECUTOR.submit.assert_not_called()


def test_append_preserves_frozen_prefix_and_does_not_drop_missing_overlap(source):
    job = run_sync(source, train=False)
    old = json.loads(service.dataset_path(job["dataset_id"]).read_text())
    newer = json.loads(json.dumps(old))
    newer["calendar"].append("2026-10-14")
    newer["bars"].extend([{**b, "date": "2026-10-14"} for b in old["bars"] if b["date"] == "2026-10-13"])
    newer["members"] = [{**m, "end": "2026-10-14"} for m in old["members"]]
    appended = sync.append_panel(old, newer)
    assert appended["bars"][:len(old["bars"])] == old["bars"]
    assert appended["calendar"][:len(old["calendar"])] == old["calendar"]
    del newer["bars"][5]
    with pytest.raises(ValueError, match="history_changed"):
        sync.append_panel(old, newer)


def test_new_endpoints_require_auth_and_reject_invalid_pool(source):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from src.modules.qlib_research import api
    app = FastAPI(); app.include_router(api.router, prefix="/qlib")
    app.dependency_overrides[api.current_actor] = lambda: None
    with TestClient(app) as client:
        assert client.get("/qlib/data-source").status_code == 401
        assert client.post("/qlib/data-source/sync", json={"request_key": "unauthorized-key"}).status_code == 401
    app.dependency_overrides[api.current_actor] = lambda: "test"
    with TestClient(app) as client:
        assert client.get("/qlib/data-source").status_code == 200
        assert client.put("/qlib/data-source", json={"symbols": ["SH600000"] * 3, "license": "Fixture", "license_acknowledged": True}).status_code == 422
        assert client.put("/qlib/data-source", json={"symbols": ["SH600000", "SH600036", "SZ000001"], "license": "Fixture", "license_acknowledged": False}).status_code == 422


def test_explicit_refresh_predicts_champion_without_retraining_or_publishing(source, storage):
    from tests.test_qlib_service import model
    first = run_sync(source, train=False)
    champion = model(storage, data_kind="market")
    service.publish(champion, 0, "Controlled queue review only", "research", "test")
    job = sync.enqueue("manual-refresh-ranks", "test", predict_when_ready=True)
    sync.execute(job["id"])
    result = sync.status()["job"]
    assert result["status"] == "succeeded" and result["dataset_id"] == first["dataset_id"]
    assert result["predict_job_id"] and result["train_job_id"] is None
    service.PREDICTOR.submit.assert_called_once()
    service.EXECUTOR.submit.assert_not_called()
    assert service.governance()["champion_id"] == champion
    assert sync.enqueue("manual-refresh-ranks", "test", predict_when_ready=True)["id"] == job["id"]
