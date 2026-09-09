"""A 股(茅台 600519)所有数据通路的端到端单测。

覆盖 TradingAgents 上游工具调用的所有路径,确保 PanWatch 数据真的塞进了上下文:
- get_stock_data        → K 线 CSV
- get_indicators        → 单指标精炼输出(不再返回 5008 字 K 线 8 次重复)
- get_news / get_global_news → 公告/事件列表(或 fallback 明确禁止全球新闻)
- get_fundamentals      → 真实财务摘要(akshare 数据)
- get_balance_sheet     → 真实资产负债数据
- get_cashflow          → 真实经营现金流
- get_income_statement  → 真实利润表数据

每条都验证:
1. 返回内容含正确的公司名 / ticker(茅台 600519)
2. 返回内容含真实业务数据(K线日期 / 指标值 / 财务数字 / 公告标题)
3. fallback 文本明确告诉 LLM 不要瞎编
"""

from __future__ import annotations

from unittest.mock import MagicMock

from src.modules.automation.tradingagents.toolkit_adapter import (
    _serve_from_panwatch,
    panwatch_data_context,
)


class _StockMt:
    """茅台 mock"""
    name = "贵州茅台"
    symbol = "600519"
    market = type("M", (), {"value": "CN"})()


def _quote_mt():
    return {
        "name": "贵州茅台",
        "current_price": 1480.50,
        "change_pct": 1.20,
        "open_price": 1465.00,
        "high_price": 1485.00,
        "low_price": 1460.00,
        "prev_close": 1463.00,
        "volume": 1_500_000,
        "turnover": 2_220_750_000,
        "pe_ratio": 24.5,
        "total_market_value": 1_860_000_000_000,
        "circulating_market_value": 1_860_000_000_000,
        "turnover_rate": 0.12,
        "industry": "白酒",
    }


def _klines_mt():
    return [
        type("K", (), {"date": "2026-05-15", "open": 1460, "high": 1485, "low": 1455, "close": 1480.50, "volume": 1_500_000})(),
        type("K", (), {"date": "2026-05-14", "open": 1450, "high": 1470, "low": 1445, "close": 1463.00, "volume": 1_200_000})(),
    ]


def _events_mt():
    e1 = MagicMock()
    e1.title = "贵州茅台:2026年第一季度营业收入同比增长 12%"
    e1.publish_time = "2026-04-28"
    e2 = MagicMock()
    e2.title = "贵州茅台:关于召开2026年第一次临时股东大会的通知"
    e2.publish_time = "2026-04-15"
    return [e1, e2]


def _financial_mt():
    """模拟 akshare stock_financial_abstract 的输出结构"""
    return {
        "periods": ["20260331", "20251231", "20250930"],
        "indicators": {
            "营业总收入": {"20260331": 50_000_000_000.0, "20251231": 180_000_000_000.0, "20250930": 130_000_000_000.0},
            "归母净利润": {"20260331": 25_000_000_000.0, "20251231": 90_000_000_000.0, "20250930": 65_000_000_000.0},
            "净利润": {"20260331": 26_000_000_000.0, "20251231": 92_000_000_000.0, "20250930": 66_000_000_000.0},
            "扣非净利润": {"20260331": 24_800_000_000.0, "20251231": 89_500_000_000.0, "20250930": 64_500_000_000.0},
            "营业成本": {"20260331": 4_500_000_000.0, "20251231": 16_000_000_000.0, "20250930": 11_500_000_000.0},
            "毛利率": {"20260331": 91.0, "20251231": 91.5, "20250930": 91.2},
            "净资产收益率(ROE)": {"20260331": 8.5, "20251231": 32.0, "20250930": 23.0},
            "资产负债率": {"20260331": 18.0, "20251231": 17.5, "20250930": 17.8},
            "经营现金流量净额": {"20260331": 22_000_000_000.0, "20251231": 80_000_000_000.0, "20250930": 55_000_000_000.0},
            "每股现金流": {"20260331": 17.50, "20251231": 63.60, "20250930": 43.70},
            "基本每股收益": {"20260331": 19.90, "20251231": 71.50, "20250930": 51.70},
            "股东权益合计(净资产)": {"20260331": 290_000_000_000.0, "20251231": 280_000_000_000.0, "20250930": 270_000_000_000.0},
            "每股净资产": {"20260331": 230.50, "20251231": 222.80, "20250930": 215.00},
            "商誉": {"20260331": 0.0, "20251231": 0.0, "20250930": 0.0},
            "销售净利率": {"20260331": 52.0, "20251231": 51.1, "20250930": 50.8},
            "期间费用率": {"20260331": 9.5, "20251231": 9.8, "20250930": 9.7},
            "总资产报酬率(ROA)": {"20260331": 7.0, "20251231": 26.5, "20250930": 19.0},
        },
        "categories": {},
    }


def _technical_mt():
    t = MagicMock()
    t.ma5 = 1475.20
    t.ma10 = 1468.00
    t.ma20 = 1455.50
    t.ma60 = 1420.00
    t.macd_dif = 8.50
    t.macd_dea = 6.20
    t.macd_hist = 2.30
    t.macd_cross = "金叉"
    t.rsi6 = 65.0
    t.rsi12 = 60.0
    t.rsi24 = 55.0
    t.rsi_status = "偏强"
    t.kdj_k = 75.0
    t.kdj_d = 70.0
    t.kdj_j = 85.0
    t.kdj_status = "强势"
    t.boll_upper = 1500.0
    t.boll_mid = 1460.0
    t.boll_lower = 1420.0
    t.boll_status = "中轨上方"
    t.volume_ratio = 1.2
    t.volume_trend = "温和放量"
    t.trend = "多头排列"
    return t


def _full_ctx(extras=None):
    ctx = {
        "stock": _StockMt(),
        "quote": _quote_mt(),
        "klines": _klines_mt(),
        "events": _events_mt(),
        "financial": _financial_mt(),
        "technical": _technical_mt(),
        "capital_flow": [],
    }
    if extras:
        ctx.update(extras)
    return ctx


# ============================================================
# 1. get_stock_data → K 线 CSV
# ============================================================

def test_get_stock_data_returns_kline_csv_for_maotai():
    """get_stock_data 工具:返回茅台 K 线 CSV(含日期/收盘价)"""
    with panwatch_data_context(_full_ctx()):
        result = _serve_from_panwatch("get_stock_data", "600519", {})
    assert "600519" in result
    assert "贵州茅台" in result
    assert "2026-05-15" in result
    assert "1480.5" in result  # 收盘价


# ============================================================
# 2. get_indicators → 单指标精炼(不重复 K 线 CSV)
# ============================================================

def test_get_indicators_macd_returns_macd_values_only():
    """get_indicators(symbol, 'macd', ...) 只返回 MACD 数值,不返回 K 线 CSV"""
    with panwatch_data_context(_full_ctx()):
        result = _serve_from_panwatch(
            "get_indicators", "600519", {},
            args=("600519", "macd", "2026-05-17", 30),
        )
    assert "MACD" in result
    assert "8.5" in result  # DIF
    assert "金叉" in result
    # 不应该是完整 K 线 CSV(那是 5008 字)
    assert len(result) < 1000


def test_get_indicators_rsi_returns_rsi_values():
    """get_indicators(symbol, 'rsi', ...) 返回 RSI 6/12/24 + 状态"""
    with panwatch_data_context(_full_ctx()):
        result = _serve_from_panwatch(
            "get_indicators", "600519", {},
            args=("600519", "rsi", "2026-05-17", 30),
        )
    assert "RSI" in result
    assert "65" in result  # RSI(6)
    assert "偏强" in result


def test_get_indicators_kdj_returns_kdj_values():
    """get_indicators(symbol, 'kdj', ...) 返回 K/D/J 值"""
    with panwatch_data_context(_full_ctx()):
        result = _serve_from_panwatch(
            "get_indicators", "600519", {},
            args=("600519", "kdj", "2026-05-17", 30),
        )
    assert "KDJ" in result
    assert "75" in result and "70" in result and "85" in result


def test_get_indicators_boll_returns_band_values():
    """get_indicators(symbol, 'boll', ...) 返回布林带上/中/下轨"""
    with panwatch_data_context(_full_ctx()):
        result = _serve_from_panwatch(
            "get_indicators", "600519", {},
            args=("600519", "boll", "2026-05-17", 30),
        )
    assert "1500" in result  # upper
    assert "1460" in result  # mid
    assert "1420" in result  # lower


def test_get_indicators_no_repeat_full_csv():
    """关键:即使被调 8 次不同 indicator,内容也是 8 份精炼报告而非 8 份相同 K 线 CSV"""
    with panwatch_data_context(_full_ctx()):
        macd = _serve_from_panwatch("get_indicators", "600519", {}, args=("600519", "macd"))
        rsi = _serve_from_panwatch("get_indicators", "600519", {}, args=("600519", "rsi"))
        boll = _serve_from_panwatch("get_indicators", "600519", {}, args=("600519", "boll"))
    # 三次返回应该差异显著
    assert macd != rsi != boll
    # 每个都应小于 1k 字符(K 线 CSV 是 5k+)
    assert max(len(macd), len(rsi), len(boll)) < 1000


# ============================================================
# 3. get_news / get_global_news → 公告事件
# ============================================================

def test_get_news_returns_company_announcements():
    """get_news 返回茅台真实公告标题"""
    with panwatch_data_context(_full_ctx()):
        result = _serve_from_panwatch("get_news", "600519", {})
    assert "贵州茅台" in result
    assert "营业收入同比增长" in result or "股东大会" in result


def test_get_global_news_with_empty_events_blocks_unrelated_news():
    """get_global_news 在没事件时返回 fallback,明确禁止 LLM 拉无关全球新闻"""
    with panwatch_data_context(_full_ctx({"events": []})):
        result = _serve_from_panwatch("get_global_news", "600519", {})
    assert "DO NOT pull unrelated global news" in result
    assert "600519" in result


# ============================================================
# 4. get_fundamentals → 真实财务摘要
# ============================================================

def test_get_fundamentals_returns_real_financial_numbers():
    """get_fundamentals 返回真实营收/净利润/ROE(而非空 fallback)"""
    with panwatch_data_context(_full_ctx()):
        result = _serve_from_panwatch("get_fundamentals", "600519", {})
    assert "600519" in result
    assert "贵州茅台" in result
    # 真实财务数据
    assert "Real Financial Data" in result
    # 营业总收入 5000 亿
    assert "500.00 亿" in result or "1800.00 亿" in result
    # ROE
    assert "8.50%" in result or "32.00%" in result
    # 毛利率 91%
    assert "91.00%" in result or "91.50%" in result


def test_get_fundamentals_fallback_when_no_financial():
    """没 financial 数据时降级到 quote 轻量基本面(不能是空文本)"""
    with panwatch_data_context(_full_ctx({"financial": None})):
        result = _serve_from_panwatch("get_fundamentals", "600519", {})
    assert "Lightweight Fundamentals" in result
    # quote 真实数据
    assert "24.5" in result  # PE
    assert "0.12" in result  # 换手率


# ============================================================
# 5. get_balance_sheet → 真实资产负债
# ============================================================

def test_get_balance_sheet_returns_real_equity_and_leverage():
    """get_balance_sheet 返回真实净资产 + 资产负债率"""
    with panwatch_data_context(_full_ctx()):
        result = _serve_from_panwatch("get_balance_sheet", "600519", {})
    assert "Balance Sheet" in result
    # 净资产 2800 亿
    assert "2800.00 亿" in result or "2900.00 亿" in result
    # 资产负债率 ~18%
    assert "18.00%" in result or "17.50%" in result


# ============================================================
# 6. get_cashflow → 真实经营现金流
# ============================================================

def test_get_cashflow_returns_real_operating_cashflow():
    """get_cashflow 返回真实经营现金流量净额(800 亿)"""
    with panwatch_data_context(_full_ctx()):
        result = _serve_from_panwatch("get_cashflow", "600519", {})
    assert "Cash Flow Statement" in result
    # 经营现金流 800 亿
    assert "800.00 亿" in result or "220.00 亿" in result
    # 每股现金流 ~63 元
    assert "63.60" in result or "17.50" in result


def test_get_cashflow_does_not_match_capital_flow_branch():
    """关键 bug 回归:cashflow 不能被路由到"资金流"分支
    (上次 bug:method 含 'flow' 字串就误判为资金流向)"""
    with panwatch_data_context(_full_ctx()):
        result = _serve_from_panwatch("get_cashflow", "600519", {})
    # 资金流分支会返回 "No capital flow data" — 不应该出现
    assert "No capital flow data" not in result
    # 应该是现金流量表
    assert "Cash Flow" in result


# ============================================================
# 7. get_income_statement → 真实利润表
# ============================================================

def test_get_income_statement_returns_real_revenue_and_profit():
    """get_income_statement 返回真实营业收入 + 净利润 + 毛利率"""
    with panwatch_data_context(_full_ctx()):
        result = _serve_from_panwatch("get_income_statement", "600519", {})
    assert "Income Statement" in result
    # 营收 1800 亿
    assert "1800.00 亿" in result or "500.00 亿" in result
    # 毛利率 91%
    assert "91.00%" in result or "91.50%" in result


# ============================================================
# 8. Stock metadata header — 公司名永远在,LLM 不会瞎编
# ============================================================

def test_all_tools_include_stock_metadata_header():
    """所有工具的输出都带 [Stock Metadata] 公司名信息"""
    methods = [
        "get_stock_data", "get_news", "get_global_news",
        "get_fundamentals", "get_balance_sheet", "get_cashflow", "get_income_statement",
    ]
    with panwatch_data_context(_full_ctx()):
        for m in methods:
            args = ("600519", "macd") if m == "get_indicators" else ("600519",)
            result = _serve_from_panwatch(m, "600519", {}, args=args)
            assert "贵州茅台" in result or "600519" in result, f"{m} 缺少公司元信息"
            assert "Stock Metadata" in result or "Technical Indicator" in result, f"{m} 缺少 metadata header"
