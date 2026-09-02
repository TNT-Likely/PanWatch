"""TA load_ohlcv 接管:A股/港股走 PanWatch K线,美股透传 yfinance。

新上游 get_verified_market_snapshot → load_ohlcv 直连 yfinance,A股(无 .SS)拉不到
→ NoMarketDataError 整个分析失败。这里验证 PanWatch 接管能为 A股构建 OHLCV,且不误伤美股。
"""

from __future__ import annotations

from datetime import date, timedelta

import pandas as pd

from src.agents.tradingagents import toolkit_adapter as ta
from src.collectors.kline_collector import KlineCollector, KlineData


def _sample_klines(n: int = 40) -> list[KlineData]:
    base = date(2026, 4, 1)
    return [
        KlineData(
            date=str(base + timedelta(days=i)),
            open=1.0 + i,
            close=2.0 + i,
            high=3.0 + i,
            low=0.5 + i,
            volume=100.0 + i,
        )
        for i in range(n)
    ]


def test_build_df_columns_and_date_filter(monkeypatch):
    """构建的 DataFrame 含 Date/OHLCV 列,Date 为 datetime,且按 curr_date 截断。"""
    monkeypatch.setattr(KlineCollector, "get_klines", lambda self, symbol, days=60: _sample_klines(40))
    df = ta._build_panwatch_ohlcv_df("601238", "2026-04-20")
    assert list(df.columns) == ["Date", "Open", "High", "Low", "Close", "Volume"]
    assert str(df["Date"].dtype).startswith("datetime64")
    assert (df["Date"] <= pd.to_datetime("2026-04-20")).all()
    assert len(df) == 20  # 04-01..04-20


def test_load_ohlcv_routes_a_share_to_panwatch(monkeypatch):
    """A股调用走 PanWatch,不触发原生 yfinance load_ohlcv。"""
    monkeypatch.setattr(KlineCollector, "get_klines", lambda self, symbol, days=60: _sample_klines(10))
    real_calls = {"n": 0}

    def fake_real(*a, **k):
        real_calls["n"] += 1
        return pd.DataFrame()

    monkeypatch.setattr(ta, "_real_load_ohlcv", fake_real)
    df = ta._panwatch_load_ohlcv("601238", "2026-06-18")
    assert not df.empty
    assert real_calls["n"] == 0, "A股不应回落到 yfinance"


def test_load_ohlcv_passthrough_for_us(monkeypatch):
    """美股放行原生 load_ohlcv(yfinance),不被 PanWatch 接管。"""
    sentinel = pd.DataFrame({"Date": [pd.to_datetime("2026-01-01")], "Close": [1.0]})
    monkeypatch.setattr(ta, "_real_load_ohlcv", lambda symbol, curr_date, *a, **k: sentinel)
    out = ta._panwatch_load_ohlcv("AAPL", "2026-06-18")
    assert out is sentinel


def test_load_ohlcv_us_rate_limit_falls_back_to_marketdata(monkeypatch):
    """Yahoo 限流时，美股必须使用 MarketData 返回的真实 K 线，而不是中断。"""
    from yfinance.exceptions import YFRateLimitError

    calls = []

    def rate_limited(*args, **kwargs):
        raise YFRateLimitError()

    def marketdata_klines(self, symbol, days=60):
        calls.append((self.market.value, symbol, days))
        return _sample_klines(10)

    monkeypatch.setattr(ta, "_real_load_ohlcv", rate_limited)
    monkeypatch.setattr(KlineCollector, "get_klines", marketdata_klines)

    out = ta._panwatch_load_ohlcv("AAPL", "2026-06-18")

    assert len(out) == 10
    assert calls == [("US", "AAPL", 750)]


def test_load_ohlcv_us_service_error_falls_back_to_marketdata(monkeypatch):
    """Yahoo 503 这类可用性错误也必须走 MarketData，不得中断分析。"""
    monkeypatch.setattr(
        ta,
        "_real_load_ohlcv",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("503 Server Error: Service Unavailable")),
    )
    monkeypatch.setattr(KlineCollector, "get_klines", lambda self, symbol, days=60: _sample_klines(10))

    out = ta._panwatch_load_ohlcv("AAPL", "2026-06-18")

    assert len(out) == 10


def test_verified_snapshot_returns_unavailable_message_when_all_sources_fail(monkeypatch):
    """行情源全失败时，验证快照应返回不可用提示而非向 LangGraph 抛异常。"""
    from tradingagents.dataflows.errors import NoMarketDataError

    def no_data(*args, **kwargs):
        raise NoMarketDataError("AAPL", "AAPL", "all market data sources failed")

    monkeypatch.setattr(ta, "_real_build_verified_market_snapshot", no_data)

    out = ta._safe_build_verified_market_snapshot("AAPL", "2026-06-18")

    assert "Verified market data unavailable for AAPL" in out
    assert "Do not make exact price, indicator, stop-loss, or trade-action claims" in out


def test_verified_snapshot_preserves_indicators_argument_when_degraded(monkeypatch):
    """安全包装器必须保持上游的 indicators 参数，避免调用方因签名变化中断。"""
    from tradingagents.dataflows.errors import NoMarketDataError

    seen = {}

    def no_data(symbol, curr_date, look_back_days=30, indicators=None):
        seen["indicators"] = indicators
        raise NoMarketDataError(symbol, symbol, "all market data sources failed")

    monkeypatch.setattr(ta, "_real_build_verified_market_snapshot", no_data)

    out = ta._safe_build_verified_market_snapshot(
        "AAPL", "2026-06-18", indicators=("rsi",)
    )

    assert "Verified market data unavailable for AAPL" in out
    assert seen == {"indicators": ("rsi",)}


def test_install_load_ohlcv_patch_updates_yfinance_indicator_import(monkeypatch):
    """技术指标工具持有的 load_ohlcv 引用也必须接入同一个 US fallback。"""
    from tradingagents.dataflows import market_data_validator, stockstats_utils, y_finance

    def upstream_load_ohlcv(*args, **kwargs):
        return pd.DataFrame()

    monkeypatch.setattr(ta, "_LOAD_OHLCV_PATCHED", False)
    monkeypatch.setattr(ta, "_real_load_ohlcv", None)
    for module in (stockstats_utils, y_finance, market_data_validator):
        monkeypatch.setattr(module, "load_ohlcv", upstream_load_ohlcv)

    ta._ensure_load_ohlcv_patched()

    assert y_finance.load_ohlcv is ta._panwatch_load_ohlcv


def test_load_ohlcv_a_share_no_klines_raises_not_fallback(monkeypatch):
    """A股取不到 K线时,直接抛 NoMarketDataError 报清晰错,**不回退 yfinance**。

    A股/港股在 Yahoo 无数据 + 限流,回退只会把"K线获取失败"变成误导的"Yahoo no rows"。
    """
    import pytest
    from tradingagents.dataflows.errors import NoMarketDataError

    monkeypatch.setattr(KlineCollector, "get_klines", lambda self, symbol, days=60: [])
    real_calls = {"n": 0}

    def fake_real(*a, **k):
        real_calls["n"] += 1
        return pd.DataFrame()

    monkeypatch.setattr(ta, "_real_load_ohlcv", fake_real)
    with pytest.raises(NoMarketDataError):
        ta._panwatch_load_ohlcv("601238", "2026-06-18")
    assert real_calls["n"] == 0, "A股拉空不应回退 yfinance"


def test_route_to_vendor_degrades_on_upstream_error(monkeypatch):
    """上游 vendor 失败(如 FRED 无 key、polymarket SSL)应降级返回空,不抛错中断整轮分析。"""

    def boom(method_name, *a, **k):
        raise RuntimeError("FRED_API_KEY environment variable is not set")

    monkeypatch.setattr(ta, "_real_route_to_vendor", boom)
    # get_macro_indicators:首参是指标名(非 A股/港股) → 走上游 passthrough → boom → 降级空
    out = ta._patched_route_to_vendor("get_macro_indicators", "fed_funds_rate", "2026-06-18", 30)
    assert out == ""
