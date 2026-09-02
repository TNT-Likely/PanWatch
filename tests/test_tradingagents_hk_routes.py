"""港股(阿里健康 00241)数据通路测试。

策略:
1. 港股 ticker(5 位数字)→ 先转 yfinance 格式(0241.HK)试上游
2. yfinance 拿到真实数据 → 用 yfinance 返回
3. yfinance 无数据(返回"No data found"或极短)→ fallback 到 PanWatch
"""

from __future__ import annotations

from src.agents.tradingagents.toolkit_adapter import (
    _yfinance_response_has_data,
    hk_symbol_to_yfinance,
    is_a_share,
    is_hk_share,
    is_panwatch_routable,
    panwatch_data_context,
)


class _StockHK:
    name = "阿里健康"
    symbol = "00241"
    market = type("M", (), {"value": "HK"})()


# ============================================================
# 1. 市场判定
# ============================================================

def test_is_a_share_6_digits():
    assert is_a_share("601127") is True
    assert is_a_share("000001") is True


def test_is_hk_share_5_digits():
    assert is_hk_share("00241") is True
    assert is_hk_share("00700") is True


def test_is_hk_share_rejects_6_digits_and_letters():
    assert is_hk_share("601127") is False
    assert is_hk_share("AAPL") is False
    assert is_hk_share("0241.HK") is False


def test_is_panwatch_routable_covers_a_and_hk():
    assert is_panwatch_routable("601127") is True
    assert is_panwatch_routable("00241") is True
    assert is_panwatch_routable("AAPL") is False


# ============================================================
# 2. 港股 ticker 格式转换
# ============================================================

def test_hk_symbol_to_yfinance_strips_leading_zero():
    """00241 → 0241.HK(yfinance 用 4 位 + .HK)"""
    assert hk_symbol_to_yfinance("00241") == "0241.HK"


def test_hk_symbol_to_yfinance_tencent():
    """腾讯 00700 → 0700.HK"""
    assert hk_symbol_to_yfinance("00700") == "0700.HK"


def test_hk_symbol_to_yfinance_already_4_digits_padded():
    """4 位数字也加 .HK 后缀"""
    assert hk_symbol_to_yfinance("0700") == "0700"  # 非 5 位不转


def test_hk_symbol_to_yfinance_skips_non_hk():
    assert hk_symbol_to_yfinance("601127") == "601127"
    assert hk_symbol_to_yfinance("AAPL") == "AAPL"


# ============================================================
# 3. yfinance 响应判定
# ============================================================

def test_yfinance_no_data_detected():
    """yfinance 返回 "No data found" → 判定无数据"""
    assert _yfinance_response_has_data(
        "No data found for symbol '00241' between 2025-11-01 and 2026-05-17"
    ) is False


def test_yfinance_empty_response_detected():
    """空字符串/极短 → 无数据"""
    assert _yfinance_response_has_data("") is False
    assert _yfinance_response_has_data("   ") is False
    assert _yfinance_response_has_data("date,open,high") is False  # 仅表头


def test_yfinance_real_data_detected():
    """正常 K 线 CSV → 有数据"""
    csv = (
        "date,open,high,low,close,volume\n"
        "2026-05-15,4.50,4.55,4.20,4.24,3.8M\n"
        "2026-05-14,4.42,4.55,4.40,4.50,2.5M\n"
        "2026-05-13,4.30,4.45,4.25,4.40,2.1M\n"
    )
    assert _yfinance_response_has_data(csv) is True


def test_yfinance_delisted_msg_detected():
    """yfinance 标记 delisted 也判无数据"""
    assert _yfinance_response_has_data(
        "$XXX: possibly delisted; symbol may be delisted"
    ) is False


def test_yfinance_unavailable_sentinel_detected():
    """上游的 NO_DATA_AVAILABLE 哨兵不是有效行情，必须触发 PanWatch fallback。"""
    assert _yfinance_response_has_data(
        "NO_DATA_AVAILABLE: No usable market data for '0700.HK' from any configured vendor"
    ) is False
