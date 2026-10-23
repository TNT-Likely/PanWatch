"""Regression coverage for persistent data and concurrent provider boundaries."""
import asyncio
import os
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, text


def test_backup_includes_committed_wal_and_precedes_schema_changes(tmp_path, monkeypatch):
    from src.platform.persistence import database
    path = tmp_path / "legacy.db"
    monkeypatch.setattr(database, "DB_PATH", str(path))
    with sqlite3.connect(path) as source:
        source.execute("PRAGMA journal_mode=WAL")
        source.execute("CREATE TABLE evidence (id INTEGER)")
        source.commit()
        source.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        source.execute("INSERT INTO evidence VALUES (1)")
        source.commit()
        database._backup_db_before_migration()
        with sqlite3.connect(next(tmp_path.glob("*.bak.*"))) as backup:
            assert backup.execute("SELECT count(*) FROM evidence").fetchone()[0] == 1
    calls = []
    monkeypatch.setattr(database, "has_pending_migrations", lambda _: True)
    monkeypatch.setattr(database, "_backup_db_before_migration", lambda: calls.append("backup"))
    monkeypatch.setattr(database.Base.metadata, "create_all", lambda **_: calls.append("schema"))
    for name in ("_migrate", "_migrate_old_providers", "_migrate_settings_to_models", "_migrate_positions_to_accounts", "_migrate_remove_stock_enabled", "run_versioned_migrations"):
        monkeypatch.setattr(database, name, lambda _, name=name: calls.append(name))
    database._init_db_once()
    assert calls[:2] == ["backup", "schema"]


def test_pending_detection_does_not_write_schema(tmp_path):
    from src.platform.persistence.migrations import has_pending_migrations
    engine = create_engine(f"sqlite:///{tmp_path / 'empty.db'}")
    assert has_pending_migrations(engine)
    with engine.connect() as conn:
        assert conn.execute(text("SELECT name FROM sqlite_master WHERE type='table'")).all() == []
    engine.dispose()


def test_all_persistent_paths_follow_data_dir(tmp_path, monkeypatch):
    from src.platform.runtime.paths import get_data_dir
    from src.modules.strategy.intraday_event_gate import _state_path
    from src.modules.research.orchestration.signals import _path
    from src.modules.administration.api.settings import _avatar_dir
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    assert get_data_dir() == tmp_path.resolve()
    for path in (_state_path(), _path(), _avatar_dir()):
        assert Path(path).resolve().is_relative_to(tmp_path.resolve())


def test_parallel_tier_clients_use_own_credentials_without_environment_changes():
    from tradingagents.graph import trading_graph
    from src.modules.automation.tradingagents.runtime_support import bounded_tier_clients
    before = dict(os.environ)
    barrier = threading.Barrier(2)
    def factory(config, tier, **kwargs):
        return kwargs["api_key"]
    def run(key):
        with bounded_tier_clients(key):
            barrier.wait(timeout=5)
            return trading_graph.create_tier_client({}, "deep")
    with patch.object(trading_graph, "create_tier_client", factory), ThreadPoolExecutor(2) as pool:
        assert list(pool.map(run, ["review-a", "review-b"])) == ["review-a", "review-b"]
    assert dict(os.environ) == before


def test_batch_quotes_overlap_deduplicate_and_preserve_partial_results(monkeypatch):
    from src.modules.market.api import quotes
    barrier = threading.Barrier(3)
    calls = []
    def collect(symbols, market):
        calls.append((market, symbols))
        barrier.wait(timeout=5)
        if market == "HK":
            raise RuntimeError("controlled outage")
        return [{"symbol": symbol, "current_price": 10} for symbol in symbols]
    monkeypatch.setattr(quotes, "md_quote_rows", collect)
    payload = quotes.QuoteBatchRequest(items=[{"symbol": "X", "market": market} for market in ["CN", "HK", "US", "CN"]])
    result = asyncio.run(quotes.get_quotes_batch(payload))
    assert [row["current_price"] for row in result] == [10, None, 10, 10]
    assert sorted(calls) == [("CN", ["X"]), ("HK", ["X"]), ("US", ["X"])]
    with pytest.raises(ValueError):
        quotes.QuoteBatchRequest(items=[{"symbol": "X", "market": "CN"}] * 501)


def test_real_tier_client_accepts_explicit_key_without_ambient_credentials(monkeypatch):
    from tradingagents.graph import trading_graph
    from src.modules.automation.tradingagents.runtime_support import bounded_tier_clients, build_ta_llm_config
    for key in ("OPENROUTER_API_KEY", "OPENAI_API_KEY", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    config = build_ta_llm_config(SimpleNamespace(base_url="https://qa.invalid/v1", model="qa-model"))
    with bounded_tier_clients("review-explicit-key"):
        llm = trading_graph.create_tier_client(config, "deep").get_llm()
    assert llm.openai_api_key.get_secret_value() == "review-explicit-key"
    assert str(llm.openai_api_base) == "https://qa.invalid/v1"
    assert not llm.use_responses_api
    assert not any(os.environ.get(key) for key in ("OPENROUTER_API_KEY", "OPENAI_API_KEY", "DEEPSEEK_API_KEY"))
