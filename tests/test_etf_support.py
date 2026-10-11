"""Listed ETF discovery, persistence, portfolio and research regressions."""

import json
import time
from types import SimpleNamespace

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.platform.marketdata import stock_list
from src.platform.persistence.database import Base, get_db
from src.platform.persistence.models import Stock
from src.modules.market.api import stocks, price_alerts
from src.modules.portfolio.api import accounts


def _search_item(code, name, classify, mkt_num, type_us=""):
    return {"Code": code, "Name": name, "Classify": classify, "MktNum": mkt_num, "TypeUS": type_us}


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    monkeypatch.setattr(stock_list, "DATA_DIR", tmp_path)
    monkeypatch.setattr(stock_list, "CACHE_FILE", tmp_path / "catalog.json")
    return tmp_path / "catalog.json"


def test_search_admits_listed_etfs_and_preserves_real_us_tickers(monkeypatch):
    items = [
        _search_item("510300", "沪深300ETF华泰柏瑞", "Fund", "1", "9"),
        _search_item("159915", "创业板ETF易方达", "Fund", "0", "10"),
        _search_item("SPY", "S&P 500 fund", "UsStock", "107", "5"),
        _search_item("SHY", "Treasury ETF", "UsStock", "105", 5),
        _search_item("USO", "Oil fund", "UsStock", "107", "5"),
        _search_item("02800", "盈富基金", "HK", "116", "1"),
        _search_item("BRK.B", "Berkshire", "UsStock", "106", "1"),
        _search_item("000001", "平安银行", "AStock", "0"),
        _search_item("510300", "Duplicate", "Fund", "1", "9"),
        _search_item("000051", "ETF联接A", "OTCFUND", "150"),
        _search_item("161725", "招商白酒LOF", "Fund", "0", "10"),
        _search_item("123001", "转债", "Bond", "0"),
        _search_item("000300", "沪深300", "Index", "1"),
        _search_item("WARRANT", "Warrant", "UsStock", "107", "6"),
    ]
    client_class = httpx.Client
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"QuotationCodeTable": {"Data": items}}))
    monkeypatch.setattr(stock_list.httpx, "Client", lambda **kwargs: client_class(transport=transport, **kwargs))
    result = stock_list._realtime_search("ETF")
    assert [r["symbol"] for r in result] == ["510300", "159915", "SPY", "SHY", "USO", "02800", "BRK.B", "000001"]
    assert all(r["asset_type"] == "etf" for r in result[:5])
    assert [r["symbol"] for r in stock_list._realtime_search("ETF", "CN")] == ["510300", "159915", "000001"]


@pytest.mark.parametrize("code,market,expected", [
    ("SH510300", "CN", "510300"), ("159915.SZ", "CN", "159915"),
    ("2800.HK", "HK", "02800"), ("BJ920001", "CN", "920001"),
    ("SHY", "US", "SHY"), ("USO", "US", "USO"), ("BRK.B", "US", "BRK.B"),
])
def test_search_normalization(code, market, expected):
    assert stock_list._normalize_symbol(code, market) == expected


def test_etf_catalog_fetches_all_pages_and_marks_type(monkeypatch):
    requests = []
    def handler(request):
        requests.append(dict(request.url.params))
        page = int(request.url.params["pn"])
        return httpx.Response(200, json={"data": {"total": 101, "diff": [{"f12": "510300" if page == 1 else "159915", "f14": "ETF"}]}})
    client_class = httpx.Client
    monkeypatch.setattr(stock_list.httpx, "Client", lambda **kwargs: client_class(transport=httpx.MockTransport(handler), **kwargs))
    result = stock_list._fetch_etf_from_eastmoney()
    assert {r["symbol"] for r in result} == {"510300", "159915"}
    assert all(r["asset_type"] == "etf" and r["market"] == "CN" for r in result)
    assert {r["pn"] for r in requests} == {"1", "2"}
    assert all("MK0021" in r["fs"] for r in requests)


def test_refresh_retains_failed_etf_catalog_but_removes_delisted_stocks(catalog, monkeypatch):
    stock_list._save_cache([
        {"symbol": "510300", "name": "ETF", "market": "CN", "asset_type": "etf"},
        {"symbol": "600999", "name": "Delisted", "market": "CN"},
    ])
    for fn, item in [
        ("_fetch_from_eastmoney", {"symbol": "600519", "name": "茅台", "market": "CN"}),
        ("_fetch_hk_from_eastmoney", {"symbol": "00700", "name": "腾讯", "market": "HK"}),
        ("_fetch_us_from_eastmoney", {"symbol": "SPY", "name": "SPDR ETF", "market": "US"}),
        ("_fetch_bj_from_eastmoney", {"symbol": "920001", "name": "北交所", "market": "CN"}),
    ]:
        monkeypatch.setattr(stock_list, fn, lambda item=item: [item])
    monkeypatch.setattr(stock_list, "_fetch_etf_from_eastmoney", lambda: [])
    monkeypatch.setattr(stock_list, "_fetch_etf_from_sina", lambda: [])
    result = stock_list.refresh_stock_list()
    assert {r["symbol"] for r in result} == {"600519", "00700", "SPY", "920001", "510300"}
    assert json.loads(catalog.read_text())["ttl"] == 300
    monkeypatch.setattr(stock_list, "_realtime_search", lambda *args: [])
    assert stock_list.search_stocks("510300", "CN")[0]["asset_type"] == "etf"


def test_old_catalog_refreshes_once_and_remains_available_offline(catalog, monkeypatch):
    old = [{"symbol": "510300", "name": "沪深300ETF", "market": "CN"}]
    catalog.write_text(json.dumps({"ts": time.time(), "stocks": old}))
    assert stock_list._load_cache() is None
    calls = []
    monkeypatch.setattr(stock_list, "refresh_stock_list", lambda: calls.append(True) or [])
    monkeypatch.setattr(stock_list, "_realtime_search", lambda *args: [])
    assert stock_list.search_stocks("510300")[0]["asset_type"] == "etf"
    assert calls == [True]
    stock_list._save_cache(old)
    assert stock_list.get_stock_list() == old
    assert calls == [True]


def test_live_etf_search_does_not_wait_for_a_cold_bulk_catalog(monkeypatch):
    result = {"symbol": "SPY", "name": "SPDR ETF", "market": "US", "asset_type": "etf"}
    monkeypatch.setattr(stock_list, "_realtime_search", lambda *args: [result])
    monkeypatch.setattr(stock_list, "_load_cache", lambda **kwargs: None)
    monkeypatch.setattr(stock_list, "get_stock_list", lambda: pytest.fail("live search waited for bulk refresh"))
    assert stock_list.search_stocks("SPY", "US") == [result]


def test_sina_etf_fallback_fetches_all_pages(monkeypatch):
    def handler(request):
        if request.url.path.endswith("getHQNodeStockCount"):
            return httpx.Response(200, json="101")
        assert request.url.params["node"] == "etf_hq_fund"
        symbol = "510300" if request.url.params["page"] == "1" else "159915"
        return httpx.Response(200, json=[{"code": symbol, "name": "ETF"}])
    client_class = httpx.Client
    monkeypatch.setattr(stock_list.httpx, "Client", lambda **kwargs: client_class(transport=httpx.MockTransport(handler), **kwargs))
    result = stock_list._fetch_etf_from_sina()
    assert {row["symbol"] for row in result} == {"510300", "159915"}
    assert all(row["asset_type"] == "etf" for row in result)


@pytest.fixture
def api(monkeypatch):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    app = FastAPI()
    app.include_router(stocks.router, prefix="/stocks")
    app.include_router(accounts.router)
    app.include_router(price_alerts.router, prefix="/price-alerts")
    app.dependency_overrides[get_db] = lambda: session
    monkeypatch.setattr(accounts, "get_hkd_cny_rate", lambda: 0.9)
    monkeypatch.setattr(accounts, "get_usd_cny_rate", lambda: 7.0)
    yield TestClient(app), session
    session.close()
    engine.dispose()


@pytest.mark.parametrize("symbol,name,market", [("510300", "沪深300ETF", "CN"), ("02800", "盈富基金", "HK"), ("SPY", "SPDR ETF", "US")])
def test_etf_can_be_saved_held_and_monitored(api, symbol, name, market):
    client, db = api
    payload = {"symbol": symbol, "name": name, "market": market, "asset_type": "etf"}
    created = client.post("/stocks", json=payload)
    assert created.status_code == 200
    stock_id = created.json()["id"]
    assert client.get("/stocks").json()[0]["asset_type"] == "etf"
    assert client.post("/stocks", json=payload).status_code == 400
    account_id = client.post("/accounts", json={"name": "ETF account"}).json()["id"]
    assert client.post("/positions", json={"stock_id": stock_id, "account_id": account_id, "cost_price": 4.123, "quantity": 100}).status_code == 200
    summary = client.get("/portfolio/summary?include_quotes=false&refresh_exchange_rates=false").json()
    position = summary["accounts"][0]["positions"][0]
    assert position["asset_type"] == "etf" and position["quantity"] == 100
    assert position["symbol"] == symbol and position["market"] == market
    alert = client.post("/price-alerts", json={"stock_id": stock_id, "enabled": False, "condition_group": {"op": "and", "items": [{"type": "price", "op": ">=", "value": 5}]}})
    assert alert.status_code == 200
    assert alert.json()["stock_symbol"] == symbol
    assert db.query(Stock).one().asset_type == "etf"


def test_legacy_create_infers_etf_and_rejects_invalid_asset_type(api):
    client, _ = api
    assert client.post("/stocks", json={"symbol": "510300", "name": "沪深300ETF"}).json()["asset_type"] == "etf"
    assert client.post("/stocks", json={"symbol": "600519", "name": "茅台", "asset_type": "bond"}).status_code == 422


def test_legacy_db_migration_backfills_etfs_idempotently():
    from src.platform.persistence.migrations import _m137_stock_asset_type
    engine = create_engine("sqlite://")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE stocks (id INTEGER PRIMARY KEY, name TEXT NOT NULL)"))
        conn.execute(text("INSERT INTO stocks (id, name) VALUES (1, '沪深300ETF'), (2, 'Apple')"))
        _m137_stock_asset_type(conn)
        _m137_stock_asset_type(conn)
        assert conn.execute(text("SELECT asset_type FROM stocks ORDER BY id")).scalars().all() == ["etf", "stock"]
    engine.dispose()


@pytest.mark.parametrize("method", ["get_fundamentals", "get_income_statement", "get_balance_sheet", "get_cashflow"])
def test_etf_research_never_serves_company_financials(method):
    from src.modules.automation.tradingagents.data_context import build_stock_metadata_context
    from src.modules.automation.tradingagents.toolkit_adapter import panwatch_data_context, _serve_from_panwatch
    stock = SimpleNamespace(symbol="02800", name="盈富基金", market="HK", asset_type="etf")
    metadata = build_stock_metadata_context(stock.symbol, stock.name, stock.market, asset_type="etf")
    assert "Fund name: 盈富基金" in metadata and "not an operating company" in metadata
    with panwatch_data_context({"stock": stock, "financial": {"periods": ["20251231"], "indicators": {"营业总收入": {"20251231": 123456789}}}}):
        result = _serve_from_panwatch(method, stock.symbol, {})
    assert "ETF" in result and "not available" in result
    assert "123456789" not in result


@pytest.mark.parametrize("current,cached,hit", [
    ("etf", None, False), ("etf", "stock", False), ("etf", "etf", True),
    ("stock", None, True), ("stock", "stock", True), ("stock", "etf", False),
])
def test_reclassified_fund_does_not_reuse_stock_research(monkeypatch, current, cached, hit):
    from src.modules.automation.tradingagents import agent as agent_module
    from src.platform.runtime.config import StockConfig
    from src.platform.marketdata.models import MarketCode
    raw_data = {"decision": "HOLD"}
    if cached is not None:
        raw_data["asset_type"] = cached
    monkeypatch.setattr(agent_module, "get_analysis", lambda **kwargs: SimpleNamespace(raw_data=raw_data, title="Cached", content="Cached report"))
    stock = StockConfig(symbol="02800", name="盈富基金", market=MarketCode.HK, asset_type=current)
    assert (agent_module.TradingAgentsAgent()._try_cache_hit(stock) is not None) is hit
