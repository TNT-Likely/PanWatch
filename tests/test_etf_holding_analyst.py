"""ETF 成分股分析 Agent 测试 —— 采集、prompt 构建、结构化建议解析。"""

import asyncio
from unittest.mock import AsyncMock, MagicMock

from src.agents.base import AgentContext, AppConfig, PortfolioInfo
from src.config import Settings, StockConfig
from src.models.market import MarketCode


def _ctx(monkeypatch, watchlist, portfolio=None) -> AgentContext:
    """构造最小 AgentContext,AI client mock。"""
    ai = MagicMock()
    ai.chat = AsyncMock(return_value="")
    settings = Settings()
    config = AppConfig(settings=settings, watchlist=watchlist)
    return AgentContext(
        ai_client=ai,
        notifier=MagicMock(),
        config=config,
        portfolio=portfolio or PortfolioInfo(),
        suppress_notify=True,
    )


def _etf_stock(symbol="510300", name="沪深300ETF华泰柏瑞") -> StockConfig:
    return StockConfig(symbol=symbol, name=name, market=MarketCode.CN, security_type="etf")


def test_collect_fetches_holdings_and_overlaps(monkeypatch):
    """collect 取 ETF 成分股,并计算与用户持仓的重叠。"""
    from src.agents import etf_holding_analyst as mod

    monkeypatch.setattr(
        mod,
        "get_etf_holdings",
        lambda sym, top=30: [
            {"symbol": "600519", "name": "贵州茅台", "weight_pct": 4.74},
            {"symbol": "300750", "name": "宁德时代", "weight_pct": 3.23},
        ],
    )
    monkeypatch.setattr(
        mod,
        "get_etf_spot",
        lambda sym: {"symbol": sym, "price": 5.04, "iopv": 5.05, "premium_pct": 0.13, "total_value": 1.2e11},
    )

    agent = mod.EtfHoldingAnalystAgent()
    ctx = _ctx(monkeypatch, [_etf_stock()])
    # 用户持仓含 600519 → 应识别为重叠
    ctx.portfolio = MagicMock()
    ctx.portfolio.all_positions = [
        MagicMock(symbol="600519", name="贵州茅台", market=MarketCode.CN, cost_price=1600, quantity=100, account_name="A")
    ]

    data = asyncio.run(agent.collect(ctx))

    assert data["symbol"] == "510300"
    assert len(data["holdings"]) == 2
    assert data["spot"]["price"] == 5.04
    overlap_syms = {h["symbol"] for h in data["holdings"] if h.get("in_portfolio")}
    assert "600519" in overlap_syms


def test_build_prompt_marks_etf_and_no_financials(monkeypatch):
    """prompt 标注标的为 ETF 且无个股财报(防 AI 虚构基本面)。"""
    from src.agents import etf_holding_analyst as mod

    agent = mod.EtfHoldingAnalystAgent()
    data = {
        "symbol": "510300",
        "name": "沪深300ETF华泰柏瑞",
        "spot": {"price": 5.04, "iopv": 5.05, "premium_pct": 0.13, "total_value": 1.2e11},
        "holdings": [{"symbol": "600519", "name": "贵州茅台", "weight_pct": 4.74, "in_portfolio": True}],
    }
    ctx = _ctx(monkeypatch, [_etf_stock()])
    system_prompt, user_content = agent.build_prompt(data, ctx)

    combined = system_prompt + user_content
    assert "ETF" in combined
    assert "财报" in combined or "基本面" in combined
    assert "600519" in user_content
    assert "5.04" in user_content


def test_analyze_parses_suggestions_and_saves(monkeypatch):
    """analyze 解析结构化建议 JSON 并落 suggestion_pool。"""
    from src.agents import etf_holding_analyst as mod

    saved: list[dict] = []
    monkeypatch.setattr(mod, "save_suggestion", lambda **kw: saved.append(kw) or True)
    monkeypatch.setattr(mod, "save_analysis", lambda **kw: True)

    agent = mod.EtfHoldingAnalystAgent()
    tagged = (
        '<!--PANWATCH_JSON-->\n{"suggestions":[{"symbol":"510300",'
        '"action":"hold","action_label":"持有","reason":"折价收窄且成分股稳健"}]}\n<!--/PANWATCH_JSON-->'
    )
    ctx = _ctx(monkeypatch, [_etf_stock()])
    ctx.ai_client.chat = AsyncMock(return_value=tagged)

    result = asyncio.run(agent.analyze(ctx, {
        "symbol": "510300",
        "name": "沪深300ETF华泰柏瑞",
        "spot": {"price": 5.04},
        "holdings": [{"symbol": "600519", "name": "贵州茅台", "weight_pct": 4.74}],
    }))

    assert saved, "应落库一条建议"
    assert saved[0]["stock_symbol"] == "510300"
    assert saved[0]["action"] == "hold"
    assert saved[0]["stock_market"] == "CN"
    assert result.raw_data.get("suggestions")


def test_collect_skips_non_etf_watchlist(monkeypatch):
    """非 ETF 标的(股票)被跳过,不采集成分股。"""
    from src.agents import etf_holding_analyst as mod

    called = []
    monkeypatch.setattr(mod, "get_etf_holdings", lambda sym, top=30: called.append(sym) or [])
    monkeypatch.setattr(mod, "get_etf_spot", lambda sym: None)

    agent = mod.EtfHoldingAnalystAgent()
    stock = StockConfig(symbol="600519", name="贵州茅台", market=MarketCode.CN, security_type="stock")
    ctx = _ctx(monkeypatch, [stock])

    data = asyncio.run(agent.collect(ctx))

    assert called == []
    assert data.get("holdings") == []
