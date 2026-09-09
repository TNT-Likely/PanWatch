"""标的元信息注入单测 — 修复 LLM 在 A 股 ticker 上瞎编公司的问题。"""

from __future__ import annotations

from src.modules.automation.tradingagents.portfolio_context import build_stock_metadata_context
from src.modules.automation.tradingagents.toolkit_adapter import (
    _stock_meta_header,
    _serve_from_panwatch,
    panwatch_data_context,
)


class _FakeStock:
    def __init__(self, name, symbol, market_value):
        self.name = name
        self.symbol = symbol
        self.market = type("M", (), {"value": market_value})()


def test_metadata_context_has_company_name():
    """meta context 必须包含公司中文名,LLM 不会瞎编"""
    ctx = build_stock_metadata_context(
        stock_symbol="601127",
        stock_name="赛力斯",
        market="CN",
        current_price=83.26,
    )
    assert "赛力斯" in ctx
    assert "601127" in ctx
    assert "A 股" in ctx
    assert "83.26" in ctx
    assert "DO NOT guess" in ctx  # 强制约束


def test_metadata_context_includes_industry():
    """如有行业,加进上下文"""
    ctx = build_stock_metadata_context(
        stock_symbol="601127",
        stock_name="赛力斯",
        market="CN",
        industry="汽车制造",
    )
    assert "汽车制造" in ctx


def test_metadata_context_empty_symbol_returns_empty():
    """空 symbol → 空串"""
    assert build_stock_metadata_context(stock_symbol="") == ""


def test_metadata_context_us_market_label():
    """美股市场标签正确渲染"""
    ctx = build_stock_metadata_context(stock_symbol="AAPL", stock_name="Apple", market="US")
    assert "美股" in ctx


def test_stock_meta_header_from_cache():
    """工具返回前缀包含公司名(来自 panwatch_data_context 注入的数据)"""
    stock = _FakeStock("赛力斯", "601127", "CN")
    quote = {"current_price": 83.26, "change_pct": -2.5, "industry": "汽车"}
    with panwatch_data_context({"stock": stock, "quote": quote}):
        header = _stock_meta_header("601127")
    assert "赛力斯" in header
    assert "601127" in header
    assert "中国 A 股" in header
    assert "83.26" in header
    assert "汽车" in header
    assert "DO NOT guess" in header


def test_serve_fundamentals_includes_company_name():
    """fundamentals 工具返回必须带公司名,避免 LLM 把 601127 当中国平安"""
    stock = _FakeStock("赛力斯", "601127", "CN")
    with panwatch_data_context({"stock": stock, "quote": {"current_price": 83.26}}):
        result = _serve_from_panwatch("get_fundamentals_openai", "601127", {})
    assert "赛力斯" in result
    assert "601127" in result


def test_serve_news_empty_does_not_leak_global_news():
    """新闻为空时,工具返回明确说"没有个股新闻",阻止 LLM 拉无关全球新闻"""
    stock = _FakeStock("广汽集团", "601238", "CN")
    with panwatch_data_context({"stock": stock, "events": []}):
        result = _serve_from_panwatch("get_news", "601238", {})
    assert "广汽集团" in result
    assert "DO NOT pull unrelated global news" in result


def test_serve_klines_empty_returns_company_aware_message():
    """K 线为空时返回明确空提示,带公司名"""
    stock = _FakeStock("赛力斯", "601127", "CN")
    with panwatch_data_context({"stock": stock, "klines": []}):
        result = _serve_from_panwatch("get_stockstats_indicators", "601127", {})
    assert "赛力斯" in result
    assert "601127" in result


def test_serve_get_balance_sheet_hits_with_balance_keyword():
    """get_balance_sheet 必须命中(之前没 balance 关键词,会 MISS)"""
    stock = _FakeStock("赛力斯", "601127", "CN")
    with panwatch_data_context({"stock": stock, "quote": {"current_price": 83.26}}):
        result = _serve_from_panwatch("get_balance_sheet", "601127", {})
    assert "601127" in result
    assert "Balance sheet" in result
    assert "Avoid invented" in result


def test_serve_get_cashflow_distinct_from_balance_sheet():
    """get_cashflow 返回独立内容,不和 balance sheet 复用同一段文字"""
    stock = _FakeStock("赛力斯", "601127", "CN")
    with panwatch_data_context({"stock": stock, "quote": {"current_price": 83.26}}):
        bs = _serve_from_panwatch("get_balance_sheet", "601127", {})
        cf = _serve_from_panwatch("get_cashflow", "601127", {})
    assert "Cash flow" in cf
    assert bs != cf  # 不能完全一样


def test_serve_get_stock_data_hits():
    """get_stock_data 必须命中(之前 method 关键词缺 stock_data)"""
    stock = _FakeStock("赛力斯", "601127", "CN")
    klines = [type("K", (), {"date": "2026-05-15", "open": 80, "high": 85, "low": 79, "close": 83, "volume": 1000})()]
    with panwatch_data_context({"stock": stock, "klines": klines, "quote": {}}):
        result = _serve_from_panwatch("get_stock_data", "601127", {})
    assert "2026-05-15" in result  # CSV 命中


def test_serve_get_indicators_without_args_fallback_to_kline_csv():
    """get_indicators 没传 indicator 参数时(罕见),fallback 到 K 线 CSV"""
    stock = _FakeStock("赛力斯", "601127", "CN")
    klines = [type("K", (), {"date": "2026-05-15", "open": 80, "high": 85, "low": 79, "close": 83, "volume": 1000})()]
    # args 为空 → 不命中单指标分支,落到 stockstats/yfin 分支返回完整 CSV
    with panwatch_data_context({"stock": stock, "klines": klines, "quote": {}}):
        result = _serve_from_panwatch("get_indicators", "601127", {}, args=())
    # 因为没匹配到单指标,降级走 stockstats 分支 → 返回完整 K 线 CSV
    assert "2026-05-15" in result


def test_serve_fundamentals_uses_real_quote_data():
    """get_fundamentals 用 quote 真实数据(PE/市值)填充,而不是纯空文本"""
    stock = _FakeStock("赛力斯", "601127", "CN")
    quote = {
        "current_price": 83.26,
        "pe_ratio": 25.5,
        "total_market_value": 125_000_000_000,
        "turnover_rate": 3.2,
    }
    with panwatch_data_context({"stock": stock, "quote": quote}):
        result = _serve_from_panwatch("get_fundamentals", "601127", {})
    assert "25.5" in result  # PE
    assert "125000000000" in result or "1.25e" in result.lower()  # 市值
    assert "3.2" in result  # 换手率
    assert "Lightweight Fundamentals" in result


def test_serve_klines_with_data_returns_csv():
    """K 线有数据时返回 CSV,前缀带公司名"""
    stock = _FakeStock("赛力斯", "601127", "CN")
    klines = [type("K", (), {"date": "2026-05-15", "open": 80, "high": 85, "low": 79, "close": 83.26, "volume": 1000})()]
    with panwatch_data_context({"stock": stock, "klines": klines}):
        result = _serve_from_panwatch("get_stockstats_indicators", "601127", {})
    assert "赛力斯" in result
    assert "2026-05-15,80,85,79,83.26,1000" in result


def test_patch_route_to_vendor_handles_positional_args():
    """根因修复:上游 route_to_vendor(method, ticker, ...) 是 positional 调用,
    patch 必须接 *args,否则 TypeError 直接放行到 yfinance"""
    import sys
    from unittest.mock import MagicMock
    from src.modules.automation.tradingagents.toolkit_adapter import patch_route_to_vendor

    # 构造一个假的 tradingagents.dataflows.interface 模块用于测试
    fake_ti = MagicMock()
    captured_calls = []

    def original_func(method, *args, **kwargs):
        captured_calls.append((method, args, kwargs))
        return "ORIGINAL_RESULT"

    fake_ti.route_to_vendor = original_func
    fake_module = type(sys)("tradingagents.dataflows.interface")
    fake_module.route_to_vendor = original_func

    # patch sys.modules 让 toolkit_adapter import 拿到我们的假模块
    sys.modules["tradingagents"] = type(sys)("tradingagents")
    sys.modules["tradingagents.dataflows"] = type(sys)("tradingagents.dataflows")
    sys.modules["tradingagents.dataflows"].interface = fake_module
    sys.modules["tradingagents.dataflows.interface"] = fake_module

    try:
        stock = _FakeStock("赛力斯", "601127", "CN")
        with panwatch_data_context({"stock": stock, "klines": [], "quote": {}}):
            with patch_route_to_vendor():
                # 模拟上游 positional 调用:route_to_vendor("get_fundamentals", "601127", "2026-05-17")
                result = fake_module.route_to_vendor("get_fundamentals", "601127", "2026-05-17")

        # 我们的 patch 必须能识别 positional ticker,不能 TypeError
        assert "赛力斯" in result
        assert "601127" in result
        # 不应该放行到 original(那会触发 captured_calls 增加)
        assert len(captured_calls) == 0
    finally:
        for k in ["tradingagents.dataflows.interface", "tradingagents.dataflows", "tradingagents"]:
            sys.modules.pop(k, None)


def test_patch_route_to_vendor_intercepts_global_news_with_cache():
    """get_global_news(curr_date, look_back_days, limit) 不带 symbol,
    但 cache 里有 A 股标的时,必须拦截,避免拉 Yahoo 无关全球新闻"""
    import sys
    from src.modules.automation.tradingagents.toolkit_adapter import patch_route_to_vendor

    def original_func(method, *args, **kwargs):
        return "GLOBAL_SHOE_NEWS_LEAKED"

    fake_module = type(sys)("tradingagents.dataflows.interface")
    fake_module.route_to_vendor = original_func
    sys.modules["tradingagents"] = type(sys)("tradingagents")
    sys.modules["tradingagents.dataflows"] = type(sys)("tradingagents.dataflows")
    sys.modules["tradingagents.dataflows"].interface = fake_module
    sys.modules["tradingagents.dataflows.interface"] = fake_module

    try:
        stock = _FakeStock("赛力斯", "601127", "CN")
        with panwatch_data_context({"stock": stock, "events": [], "quote": {}}):
            with patch_route_to_vendor():
                # get_global_news 第一个参数是日期,不是 ticker
                result = fake_module.route_to_vendor(
                    "get_global_news", "2026-05-17", 7, 20
                )
        assert "GLOBAL_SHOE_NEWS_LEAKED" not in result
        assert "DO NOT pull unrelated global news" in result
    finally:
        for k in ["tradingagents.dataflows.interface", "tradingagents.dataflows", "tradingagents"]:
            sys.modules.pop(k, None)
