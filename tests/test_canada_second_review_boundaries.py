"""Regression boundaries: persisted datasource config and assistant/MCP quotes.

HTTP/MCP use the actual router and vendor engine, with only Yahoo's transport
replaced by deterministic fixtures. No live provider availability is claimed.
"""
import asyncio
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pan_agent import ModelMessage, RunRequest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import server
from marketdata import MarketData, SourceConfig, StaticConfigProvider
from marketdata.vendors import canada
from src.modules.administration.api import datasources, mcp, pats
from src.modules.assistant import tools
from src.platform.marketdata import marketdata_client, quote_display
from src.platform.persistence.database import Base, get_db
from src.platform.persistence.models import DataSource


@pytest.fixture
def boundary(monkeypatch):
    engine = create_engine("sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    app = FastAPI()
    app.include_router(datasources.router, prefix="/datasources")
    app.include_router(pats.router, prefix="/pats")
    app.include_router(mcp.router, prefix="/mcp")

    def db_dep():
        with sessions() as db:
            yield db

    app.dependency_overrides[get_db] = db_dep
    monkeypatch.setattr(mcp, "SessionLocal", sessions)
    md = MarketData(StaticConfigProvider({"quote": [SourceConfig(vendor="yahoo_canada")]}))
    monkeypatch.setattr(marketdata_client, "get_market_data", lambda: md)
    now = int(datetime.now(UTC).timestamp())
    requests = []

    def yahoo(url, **kwargs):
        symbol = url.rsplit("/", 1)[-1]
        requests.append(symbol)
        return {"chart": {"result": [{"meta": {
            "symbol": symbol, "exchangeName": "VAN" if symbol.endswith(".V") else "TOR",
            "instrumentType": "ETF", "currency": "USD" if symbol == "DLR-U.TO" else "CAD",
            "regularMarketTime": now, "regularMarketPrice": 10,
        }, "timestamp": [now - 86400, now], "indicators": {"quote": [{
            "open": [9, 9], "close": [9, 10], "high": [10, 11], "low": [8, 8], "volume": [10, 20],
        }]}}]}}

    monkeypatch.setattr(canada, "market_get", yahoo)
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client, sessions, requests
    engine.dispose()


@pytest.mark.parametrize("legacy", [False, True])
def test_canadian_seed_config_survives_list_test_edit_and_restart(boundary, legacy):
    client, sessions, requests = boundary
    with sessions() as db:
        for seed in server.DATA_SOURCE_SEEDS:
            if seed["provider"] == "yahoo_canada":
                values = dict(seed)
                if legacy:
                    values["test_symbols"] = [
                        {"symbol": "ZSP", "market": "TSX"},
                        {"symbol": "REI-UN", "market": "TSX"},
                        {"symbol": "RCK", "market": "TSXV"},
                    ]
                db.add(DataSource(**values))
        db.commit()
    rows = client.get("/datasources").json()
    assert len(rows) == 2
    for row in rows:
        assert row["test_symbols"] == ["ZSP.TO", "REI-UN.TO", "RCK.V"]
        result = client.post(f"/datasources/{row['id']}/test")
        assert result.status_code == 200, result.text
        body = result.json()
        assert body["test_passed"] is True, body
        assert body["count"] == 3
        assert body["test_symbols"] == row["test_symbols"]
        # Settings form round-trips the API list[str] contract.
        edited = client.put(f"/datasources/{row['id']}", json={"test_symbols": ["DLR-U.TO", "RCK.V"]})
        assert edited.status_code == 200, edited.text
    assert {"ZSP.TO", "REI-UN.TO", "RCK.V"} <= set(requests)
    with sessions() as db:
        # Normal startup migrates old object values without replacing custom symbols.
        first = db.get(DataSource, rows[0]["id"])
        first.test_symbols = [{"symbol": "DLR.U", "market": "TSX"}, {"symbol": "RCK", "market": "TSXV"}]
        db.commit()
        server.seed_data_sources(db)
        db.commit()
        server.seed_data_sources(db)
        db.commit()
        for row in rows:
            db.expire_all()
            assert db.get(DataSource, row["id"]).test_symbols == ["DLR-U.TO", "RCK.V"]


@pytest.mark.parametrize("symbol,currency", [("DLR-U", "USD"), ("ZSP", "CAD")])
def test_provider_to_registry_and_authenticated_mcp_preserve_quote_provenance(boundary, monkeypatch, symbol, currency):
    client, sessions, _ = boundary
    # Test the dangerous boundary: a very recent, explicitly delayed snapshot
    # during open trading must still never be labeled real-time.
    monkeypatch.setattr(quote_display.calendar, "market_status", lambda *_: "trading")
    with sessions() as db:
        request = RunRequest(run_id="boundary", messages=[ModelMessage(role="user", content="Quote")])
        result = asyncio.run(tools.build_panwatch_tool_registry(db).execute(
            "get_stock_quote", request, {"symbol": symbol, "market": "TSX"}))
        payload = result.model_dump(mode="json")
    assert payload["ok"] is True
    data = payload["data"]
    assert data["currency"] == currency
    assert data["security_type"] == "ETF"
    assert data["source"] == "yahoo_canada"
    assert data["delayed"] is True
    assert data["freshness"] == "delayed"
    assert data["is_realtime"] is False
    assert data["observed_at"] and data["source_timestamp"]
    assert payload["sources"][0]["as_of"] == data["source_timestamp"]
    assert currency in payload["summary"] and "不能视为当前实时行情" in payload["summary"]

    token = client.post("/pats", json={"name": "fixture"}).json()["token"]
    response = client.post("/mcp", headers={"Authorization": f"Bearer {token}"}, json={
        "jsonrpc": "2.0", "id": 1, "method": "tools/call",
        "params": {"name": "get_stock_quote", "arguments": {"symbol": symbol, "market": "TSX"}},
    })
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert result["isError"] is False
    text = result["content"][0]["text"]
    for field in [currency, "ETF", "yahoo_canada", data["source_timestamp"], "供应商延迟 是", "时效 delayed", "不能视为当前实时行情"]:
        assert field in text
    assert not text.startswith("实时行情")


def test_missing_quote_metadata_remains_unknown(monkeypatch):
    from src.modules.assistant.tool_adapters import fetch_realtime_context
    monkeypatch.setattr(quote_display.calendar, "market_status", lambda *_: "trading")
    text = asyncio.run(fetch_realtime_context("ZSP", "TSX", quotes=[{
        "symbol": "ZSP", "market": "TSX", "current_price": 10,
    }]))
    for value in ["币种未知", "证券类型未知", "来源未知", "时间未知", "供应商延迟 未知", "时效 unknown", "不能视为当前实时行情"]:
        assert value in text
