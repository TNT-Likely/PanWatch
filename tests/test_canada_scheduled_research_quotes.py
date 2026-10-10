"""Production-boundary regressions for scheduled and HTTP listing identity.

HTTP tests use the real routers, marketdata engine, vendor parsers and news
collector. Only external transports and the final model call are controlled.
"""

import asyncio
import json
from datetime import date, datetime, timezone
from types import SimpleNamespace
from urllib.parse import unquote

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from marketdata import MarketData, SourceConfig, StaticConfigProvider
from marketdata.vendors import canada, news, tencent
from src.modules.market.api import quotes
from src.modules.research.api import insights
from src.platform.marketdata import marketdata_client
from src.platform.persistence.database import Base, get_db
from src.platform.persistence.models import AgentConfig, Stock, StockAgent


@pytest.fixture
def boundary_app(monkeypatch):
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    app = FastAPI()
    app.include_router(quotes.router, prefix="/quotes")
    app.include_router(insights.router, prefix="/insights")

    def db_session():
        with sessions() as db:
            yield db

    app.dependency_overrides[get_db] = db_session
    md = MarketData(StaticConfigProvider({
        "quote": [SourceConfig(vendor="yahoo_canada"), SourceConfig(vendor="tencent")],
        "news": [SourceConfig(vendor="eastmoney_news")],
    }))
    monkeypatch.setattr(marketdata_client, "get_market_data", lambda: md)
    now = int(datetime.now(timezone.utc).timestamp())
    requests = []
    unavailable = set()

    def yahoo_transport(url, **kwargs):
        symbol = unquote(url.rsplit("/", 1)[-1])
        requests.append(("yahoo_canada", symbol))
        if symbol in unavailable:
            return {"chart": {"result": None, "error": {"code": "Not Found"}}}
        price = 20 if symbol.endswith(".V") else 10
        return {"chart": {"result": [{
            "meta": {
                "symbol": symbol,
                "exchangeName": "VAN" if symbol.endswith(".V") else "TOR",
                "instrumentType": "EQUITY", "currency": "CAD",
                "regularMarketTime": now, "regularMarketPrice": price,
            },
            "timestamp": [now - 86400, now],
            "indicators": {"quote": [{
                "open": [price - 1, price], "high": [price - 1, price],
                "low": [price - 1, price], "close": [price - 1, price],
                "volume": [100, 100],
            }]},
        }]}}

    def tencent_transport(url, **kwargs):
        lines = []
        for provider_symbol in url.split("q=", 1)[1].split(","):
            requests.append(("tencent", provider_symbol))
            price = 30 if provider_symbol.startswith("us") else 40 if provider_symbol.startswith("hk") else 50
            fields = [""] * 50
            fields[1:7] = [provider_symbol, provider_symbol[2:], str(price), str(price - 1), str(price), "100"]
            fields[30:36] = ["20261009100000", "1", "2", str(price), str(price), "0"]
            lines.append(f'v_{provider_symbol}="' + "~".join(fields) + '";')
        return "".join(lines).encode("gbk")

    monkeypatch.setattr(canada, "market_get", yahoo_transport)
    monkeypatch.setattr(tencent, "market_get", tencent_transport)
    with TestClient(app) as client:
        yield SimpleNamespace(
            client=client, sessions=sessions, requests=requests, unavailable=unavailable, md=md,
        )
    engine.dispose()


@pytest.mark.parametrize("raw,market,canonical", [
    ("REI.UN", "TSX", "REI-UN"),
    ("RY.TO", "TSX", "RY"),
    ("ENB.PR.V", "TSX", "ENB-PV"),
    ("RCK.V", "TSXV", "RCK"),
    ("BBD.B", "TSX", "BBD-B"),
    ("ZSP.U.TO", "TSX", "ZSP-U"),
    (" rei.un ", "TSX", "REI-UN"),
])
@pytest.mark.parametrize("alias_first", [False, True])
def test_http_quote_alias_uses_canonical_provider_result(boundary_app, raw, market, canonical, alias_first):
    client = boundary_app.client
    requested = [raw, canonical] if alias_first else [canonical, raw]
    responses = [client.get(f"/quotes/{symbol}", params={"market": market}) for symbol in requested]
    assert all(response.status_code == 200 for response in responses)
    rows = [response.json() for response in responses]
    price = 20 if market == "TSXV" else 10
    for symbol, row in zip(requested, rows):
        assert row["symbol"] == symbol  # Retain the public request/response correlation key.
        assert row["market"] == market
        assert row["current_price"] == price
        assert row["currency"] == "CAD"
        assert row["source"] == "yahoo_canada"
        assert row["security_type"] == "EQUITY"
        assert row["source_timestamp"]
        assert row["is_delayed"] is True
    assert {k: v for k, v in rows[0].items() if k != "symbol"} == {
        k: v for k, v in rows[1].items() if k != "symbol"
    }
    batch = client.post("/quotes/batch", json={"items": [
        {"symbol": raw, "market": market}, {"symbol": canonical, "market": market},
    ]})
    assert batch.status_code == 200
    assert [row["symbol"] for row in batch.json()] == [raw, canonical]
    assert [row["current_price"] for row in batch.json()] == [price, price]
    suffix = ".V" if market == "TSXV" else ".TO"
    assert boundary_app.requests
    assert set(boundary_app.requests) == {("yahoo_canada", canonical + suffix)}


def test_http_mixed_batch_keeps_duplicate_aliases_and_same_symbol_exchanges_separate(boundary_app):
    items = [
        {"symbol": "T.TO", "market": "TSX"},
        {"symbol": "T", "market": "US"},
        {"symbol": "T.V", "market": "TSXV"},
        {"symbol": "T", "market": "TSX"},
        {"symbol": "600519", "market": "CN"},
        {"symbol": "00700", "market": "HK"},
        {"symbol": "T.TO", "market": "TSX"},
    ]
    response = boundary_app.client.post("/quotes/batch", json={"items": items})
    assert response.status_code == 200
    rows = response.json()
    assert [{"symbol": row["symbol"], "market": row["market"]} for row in rows] == items
    assert [row["current_price"] for row in rows] == [10, 30, 20, 10, 50, 40, 10]
    assert [row["source"] for row in rows] == [
        "yahoo_canada", "tencent", "yahoo_canada", "yahoo_canada", "tencent", "tencent", "yahoo_canada",
    ]
    for item, batch_row in zip(items, rows):
        single = boundary_app.client.get(f"/quotes/{item['symbol']}", params={"market": item["market"]})
        assert single.status_code == 200
        assert single.json() == batch_row
    assert set(boundary_app.requests) == {
        ("yahoo_canada", "T.TO"), ("yahoo_canada", "T.V"),
        ("tencent", "usT"), ("tencent", "sh600519"), ("tencent", "hk00700"),
    }


def test_http_missing_canadian_quote_never_uses_same_symbol_other_exchange(boundary_app):
    boundary_app.unavailable.add("T.TO")
    response = boundary_app.client.post("/quotes/batch", json={"items": [
        {"symbol": "T", "market": "US"}, {"symbol": "T.TO", "market": "TSX"},
        {"symbol": "T.V", "market": "TSXV"},
    ]})
    assert response.status_code == 200
    assert [row["current_price"] for row in response.json()] == [30, None, 20]
    assert response.json()[1]["symbol"] == "T.TO"
    assert response.json()[1]["market"] == "TSX"
    assert response.json()[1]["currency"] is None
    single = boundary_app.client.get("/quotes/T.TO", params={"market": "TSX"})
    assert single.status_code == 404
    assert single.json()["detail"]["code"] == "quote_not_found"
    assert boundary_app.client.post("/quotes/batch", json={"items": []}).json() == []


@pytest.mark.parametrize("symbol,market", [
    ("RY.TO", "US"), ("RCK.V", "TSX"), ("RY.TO", "TSXV"),
    ("ENB.PR.V", "TSXV"), ("ENB.PF.V", "TSX"), ("BAD!", "TSX"),
])
@pytest.mark.parametrize("batch", [False, True])
def test_http_invalid_or_conflicting_identity_is_rejected_before_collection(boundary_app, symbol, market, batch):
    if batch:
        response = boundary_app.client.post("/quotes/batch", json={"items": [
            {"symbol": "T", "market": "US"}, {"symbol": symbol, "market": market},
        ]})
    else:
        response = boundary_app.client.get(f"/quotes/{symbol}", params={"market": market})
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "stock_symbol_invalid"
    assert boundary_app.requests == []


ISSUERS = [
    ("T", "US", "AT&T Inc."),
    ("T", "TSX", "TELUS Corporation"),
    ("T", "TSXV", "Venture fixture issuer"),
    ("600519", "CN", "贵州茅台"),
    ("00700", "HK", "腾讯控股"),
]


@pytest.mark.parametrize("symbol,market,name", ISSUERS)
@pytest.mark.parametrize("reverse_insert", [False, True])
def test_add_position_news_uses_requested_issuer_and_keeps_fractional_metrics(
    boundary_app, monkeypatch, symbol, market, name, reverse_insert,
):
    with boundary_app.sessions() as db:
        for code, exchange, issuer in (list(reversed(ISSUERS)) if reverse_insert else ISSUERS):
            db.add(Stock(symbol=code, market=exchange, name=issuer))
            db.flush()
        db.commit()
    keywords, prompts = _capture_evaluation(monkeypatch)
    response = _evaluate(boundary_app.client, symbol, market)
    assert response.status_code == 200
    # The configured news vendor supports CN only. Do not silently relabel its
    # ticker/keyword search as coverage of another exchange.
    assert keywords == ([name] if market == "CN" else [])
    assert len(prompts) == 1
    assert f"标的 {market}:{symbol}" in prompts[0]
    assert (f"{name} fixture announcement" in prompts[0]) == (market == "CN")
    assert all(f"{issuer} fixture announcement" not in prompts[0] for _, _, issuer in ISSUERS if issuer != name)
    result = response.json()
    assert result["symbol"] == symbol and result["market"] == market
    assert result["total_quantity"] == 1.5
    assert result["total_invested"] == 14
    assert result["new_cost"] == 9.3333
    assert result["dilute_abs"] == -0.3333
    assert result["dilute_pct"] == -3.7037
    assert result["verdict"] == "谨慎"


def test_add_position_without_matching_listing_does_not_borrow_other_issuer(boundary_app, monkeypatch):
    with boundary_app.sessions() as db:
        db.add(Stock(symbol="T", market="US", name="AT&T Inc."))
        db.commit()
    keywords, prompts = _capture_evaluation(monkeypatch)
    response = _evaluate(boundary_app.client, "T", "TSX")
    assert response.status_code == 200
    assert keywords == []
    assert "AT&T" not in prompts[0]


def _evaluate(client, symbol, market):
    return client.post("/insights/add-position-eval", json={
        "symbol": symbol, "market": market, "current_quantity": 1,
        "current_cost": 9, "add_quantity": 0.5, "add_price": 10,
    })


def _capture_evaluation(monkeypatch):
    keywords, prompts = [], []

    def search_transport(url, **kwargs):
        keyword = json.loads(kwargs["params"]["param"])["keyword"]
        keywords.append(keyword)
        return "jQuery(" + json.dumps({"code": 0, "result": {"cmsArticleWebOld": [{
            "code": "fixture-news", "title": keyword + " fixture announcement",
            "content": "fixture", "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "url": "https://example.com/fixture-news",
        }]}}) + ")"

    async def no_technical(*args, **kwargs):
        return ""

    class Model:
        async def chat(self, system, user, **kwargs):
            prompts.append(user)
            return "结论: 谨慎"

    monkeypatch.setattr(news, "market_get", search_transport)
    monkeypatch.setattr(insights, "fetch_technical_context", no_technical)
    monkeypatch.setattr(insights, "get_configured_failover_client", lambda *args, **kwargs: Model())
    return keywords, prompts


@pytest.mark.parametrize("reverse_insert", [False, True])
@pytest.mark.parametrize("day,expected_markets", [
    (date(2026, 10, 9), {"US", "TSX", "TSXV", "CN", "HK"}),
    (date(2026, 10, 12), {"US", "CN", "HK"}),  # Thanksgiving closes only Canada here.
])
def test_inherited_single_schedule_collects_each_eligible_listing_once(
    boundary_app, monkeypatch, reverse_insert, day, expected_markets,
):
    from src.modules.automation import agent_scheduler, scheduling_policy
    from src.modules.automation.base import (
        AccountInfo, AgentContext, AnalysisResult, PortfolioInfo, PositionInfo,
    )
    from src.modules.automation.tradingagents import agent as ta, data_context
    from src.platform.marketdata.collectors.kline_collector import KlineCollector
    from src.platform.marketdata.models import MarketCode
    from src.platform.runtime.config import AppConfig, StockConfig
    from src.platform.scheduling import trading_calendar

    listings = list(reversed(ISSUERS)) if reverse_insert else ISSUERS
    with boundary_app.sessions() as db:
        config = AgentConfig(name="tradingagents", display_name="Deep analysis", execution_mode="single", schedule="0 12 * * 1-5")
        db.add(config)
        for symbol, market, name in listings:
            stock = Stock(symbol=symbol, market=market, name=name)
            db.add(stock)
            db.flush()
            db.add(StockAgent(stock_id=stock.id, agent_name=config.name))
        db.commit()
        plans = scheduling_policy.schedule_plans(db, config)
    assert len(plans) == 1 and plans[0].stock_agent_id is None
    assert set(plans[0].stock_keys) == {(market, symbol) for symbol, market, _ in ISSUERS}
    stocks = [StockConfig(symbol, name, MarketCode(market)) for symbol, market, name in listings]
    positions = [PositionInfo(
        account_id=1, account_name="fixture", stock_id=index, symbol=stock.symbol,
        name=stock.name, market=stock.market, cost_price=9, quantity=index + 0.5,
    ) for index, stock in enumerate(stocks, start=1)]
    context = AgentContext(
        None, None, AppConfig(SimpleNamespace(), stocks),
        portfolio=PortfolioInfo([AccountInfo(1, "fixture", 100, positions)]),
        suppress_notify=True, model_label="fixture-model",
    )
    original_config, original_portfolio = context.config, context.portfolio
    agent = ta.TradingAgentsAgent()
    observed = []

    class Progress:
        def __init__(self, *args, **kwargs):
            pass

        def emit(self, *args, **kwargs):
            pass

    async def analyze(scoped, data):
        stock = data["stock"]
        observed.append((stock.market.value, stock.symbol))
        assert scoped.config is not original_config
        assert scoped.config.settings is original_config.settings
        assert scoped.portfolio is not original_portfolio
        assert scoped.watchlist == [stock]
        assert [(p.market, p.symbol) for p in scoped.portfolio.all_positions] == [(stock.market, stock.symbol)]
        assert data["quote"]["market"] == stock.market.value
        assert data["quote"]["symbol"] == stock.symbol
        assert data["quote"]["current_price"] == {"TSX": 10, "TSXV": 20, "US": 30, "HK": 40, "CN": 50}[stock.market.value]
        return AnalysisResult(agent.name, stock.name, "fixture analysis", raw_data={})

    # Preserve schedule planning, run_single, BaseAgent.run and actual collect.
    # Empty non-quote sources avoid external data; stop only at the model boundary.
    monkeypatch.setattr(ta, "PanWatchProgressHandler", Progress)
    monkeypatch.setattr(ta, "get_market_data", lambda: boundary_app.md)
    monkeypatch.setattr(KlineCollector, "get_technical_indicators", lambda *args, **kwargs: None)
    monkeypatch.setattr(data_context, "fetch_financial_abstract", lambda *args, **kwargs: None)
    monkeypatch.setattr(agent, "analyze", analyze)
    monkeypatch.setattr(agent_scheduler, "market_allowed", lambda name, market: trading_calendar.is_trading_day(market, day))
    recorded = []
    monkeypatch.setattr(agent_scheduler, "record_agent_run", lambda **kwargs: recorded.append(kwargs))
    scheduler = agent_scheduler.AgentScheduler()
    scheduler.set_context_builder(lambda name: context)
    plan = plans[0]
    scheduler.register(agent, plan.schedule, "single", plan.stock_keys, plan.stock_agent_id)
    job = scheduler.scheduler.get_job(agent.name)
    asyncio.run(job.func(*job.args))

    expected = [(market, symbol) for symbol, market, _ in listings if market in expected_markets]
    assert observed == expected
    assert context.config is original_config and context.portfolio is original_portfolio
    assert context.watchlist == stocks and context.portfolio.all_positions == positions
    assert len(recorded) == 1
    record = recorded[0]
    assert record["status"] == "success" and record["error"] == ""
    assert record["result"] == f"single mode executed {len(expected)}, skipped {len(stocks) - len(expected)}, total {len(stocks)}"
    assert record["notify_attempted"] is True and record["notify_sent"] is False
    assert record["model_label"] == "fixture-model"
