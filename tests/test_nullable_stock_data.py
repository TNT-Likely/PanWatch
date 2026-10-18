"""Missing vendor quote fields stay unknown through reports and notifications."""

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from marketdata import Quote

from src.modules.automation import intraday_monitor
from src.modules.automation.base import AccountInfo, AgentContext, PortfolioInfo, PositionInfo
from src.modules.automation.daily_report import DailyReportAgent
from src.modules.automation.intraday_monitor import IntradayMonitorAgent
from src.modules.research.signals.signal_pack import SignalPack
from src.platform.marketdata import marketdata_client
from src.platform.marketdata.models import MarketCode, StockData
from src.platform.runtime.config import StockConfig


QUOTE_FIELDS = (
    "current_price", "change_pct", "change_amount", "open_price", "high_price",
    "low_price", "prev_close", "volume", "turnover",
)


def _stock(market=MarketCode.CN, **values):
    fields = dict.fromkeys(QUOTE_FIELDS)
    fields.update(values)
    return StockData(symbol="T", name="Test stock", market=market, **fields)


def _context(market=MarketCode.CN, *, language="zh-CN"):
    position = PositionInfo(1, "Test account", 1, "T", "Test stock", market, 10, 100)
    portfolio = PortfolioInfo([AccountInfo(1, "Test account", 1000, [position])])
    return AgentContext(
        ai_client=None,
        notifier=None,
        config=SimpleNamespace(watchlist=[StockConfig("T", "Test stock", market)]),
        portfolio=portfolio,
        report_language=language,
    )


def _daily_prompt(stock, context=None):
    context = context or _context()
    market = context.watchlist[0].market
    pack = SignalPack("T", "Test stock", market, "", quote=stock)
    return DailyReportAgent().build_prompt({
        "indices": [],
        "signal_packs": {f"{market.value}:T": pack},
        # Keep these quote-only regressions independent of reference-FX I/O.
        "portfolio_valuation": {
            "accounts": [], "positions": {}, "fx": {},
            "total_cost_cny": None, "valuation_incomplete": True,
        },
    }, context)[1]


@pytest.mark.parametrize("value", [None, 0.0, 12.5])
def test_adapter_preserves_nullable_fields_and_real_zero(monkeypatch, value):
    stamp = datetime(2026, 10, 9, 19, 30, tzinfo=timezone.utc)
    quote = Quote(
        symbol="T", name="Test stock", market="TSX",
        **dict.fromkeys(QUOTE_FIELDS, value),
        currency="CAD", security_type="EQUITY", source="yahoo_canada",
        source_timestamp=stamp, delayed=True,
    )
    quotes = Mock(return_value=[quote])
    monkeypatch.setattr(marketdata_client, "get_market_data", lambda: SimpleNamespace(quotes=quotes))

    result, = marketdata_client.md_stock_data(["T"], "TSX")

    quotes.assert_called_once_with(["T"], market="TSX")
    assert isinstance(result, StockData)
    assert {field: getattr(result, field) for field in QUOTE_FIELDS} == dict.fromkeys(QUOTE_FIELDS, value)
    assert result.market is MarketCode.TSX
    assert result.currency == "CAD" and result.security_type == "EQUITY"
    assert result.source == "yahoo_canada" and result.source_timestamp == stamp
    assert result.delayed is True


@pytest.mark.parametrize("market", [MarketCode.CN, MarketCode.US, MarketCode.TSX])
def test_intraday_missing_quote_fields_do_not_create_price_or_position_alerts(market):
    text = IntradayMonitorAgent().build_prompt({
        "stock_data": _stock(market),
        "kline_summary": {"atr": 0.6, "atr_pct": 2.0},
    }, _context(market))[1]

    for label in ("现价", "涨跌幅", "涨跌额", "今开", "最高", "最低", "昨收", "成交量", "成交额", "持仓市值", "浮动盈亏"):
        assert f"{label}：不可用" in text
    assert "无法判断（涨跌幅缺失）" in text
    assert "今日涨跌幅不可用" in text
    assert "触发止损预警" not in text and "触发止盈提醒" not in text
    assert "-100.0%" not in text and "+0.00%" not in text


def test_intraday_partial_quote_keeps_known_price_without_inventing_change():
    text = IntradayMonitorAgent().build_prompt({
        "stock_data": _stock(current_price=12),
        "kline_summary": {"atr": 0.6, "atr_pct": 2.0},
    }, _context())[1]

    assert "现价：12.00" in text
    assert "涨跌幅：不可用" in text
    assert "持仓市值：1200 元" in text
    assert "浮动盈亏：+20.0%（触发止盈提醒）" in text


def test_intraday_real_zero_remains_observed_data():
    stock = _stock(**dict.fromkeys(QUOTE_FIELDS, 0.0))
    text = IntradayMonitorAgent().build_prompt({"stock_data": stock}, _context())[1]

    assert "现价：0.00" in text and "涨跌幅：+0.00%" in text
    assert "成交量：0 手" in text and "成交额：0 万" in text
    assert "持仓市值：0 元" in text
    assert "浮动盈亏：-100.0%（触发止损预警）" in text


@pytest.mark.parametrize("market", [MarketCode.CN, MarketCode.US, MarketCode.TSX])
@pytest.mark.parametrize("quote_present", [False, True])
def test_daily_missing_price_does_not_create_total_loss(market, quote_present):
    text = _daily_prompt(_stock(market) if quote_present else None, _context(market))

    assert "浮盈不可用" in text and "浮盈-100.0%" not in text
    if quote_present:
        assert "今日：不可用" in text
        assert "振幅：不可用" in text
        assert "最高不可用 最低不可用" in text
        assert "成交额：不可用" in text
        assert "+0.00%" not in text


@pytest.mark.parametrize("missing, value", [
    ("high_price", None), ("low_price", None), ("prev_close", None), ("prev_close", 0.0),
])
def test_daily_amplitude_requires_complete_ohlc_and_positive_previous_close(missing, value):
    fields = {"current_price": 12, "high_price": 13, "low_price": 11, "prev_close": 10}
    fields[missing] = value
    text = _daily_prompt(_stock(**fields))

    assert "今日：12.00" in text
    assert "振幅：不可用" in text
    assert "浮盈+20.0%" in text


def test_daily_real_zero_change_amplitude_and_turnover_remain_available():
    text = _daily_prompt(_stock(
        current_price=10, change_pct=0, high_price=10, low_price=10, prev_close=10,
        volume=0, turnover=0,
    ))

    assert "今日：10.00 → +0.00%" in text
    assert "振幅：0.0%  最高10.00 最低10.00" in text
    assert "成交额：0.00亿" in text
    assert "浮盈+0.0%" in text


@pytest.mark.parametrize("language", ["zh-CN", "en-US"])
@pytest.mark.parametrize("value, price, change", [(None, "N/A", "N/A"), (0.0, "0.00", "+0.00%")])
def test_notification_content_distinguishes_missing_quote_from_zero(language, value, price, change):
    content = IntradayMonitorAgent()._format_human_readable_content(
        _stock(current_price=value, change_pct=value),
        {"action": "watch"}, '{"action":"watch"}', language,
    )
    expected = f"Current price: {price}  Change: {change}" if language == "en-US" else f"现价：{price}  涨跌：{change}"
    assert expected in content


@pytest.mark.parametrize("language", ["zh-CN", "en-US"])
@pytest.mark.parametrize("change, expected", [(None, "N/A"), (0.0, "+0.00%")])
def test_analyze_accepts_missing_change_and_preserves_notification_metadata(monkeypatch, language, change, expected):
    from src.modules.automation.tradingagents import operations

    save = Mock()
    monkeypatch.setattr(intraday_monitor, "save_suggestion", save)
    monkeypatch.setattr(intraday_monitor, "save_agent_prediction_outcome", Mock())
    monkeypatch.setattr(intraday_monitor, "save_agent_context_run", Mock())
    monkeypatch.setattr(operations, "try_auto_trigger", Mock())
    context = _context(language=language)
    context.ai_client = SimpleNamespace(chat=AsyncMock(return_value='{"action":"watch","reason":"Incomplete quote"}'))
    stock = _stock(current_price=10, change_pct=change)

    result = asyncio.run(IntradayMonitorAgent().analyze(context, {"stock_data": stock}))

    assert result.title.endswith(f"Test stock {expected}")
    assert result.raw_data["stock"]["change_pct"] is change
    assert save.call_args.kwargs["meta"]["quote"]["change_pct"] is change
