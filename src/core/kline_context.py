from __future__ import annotations

from math import sqrt

from src.collectors.kline_collector import KlineCollector
from src.models.market import MarketCode


def _pct(a: float | None, b: float | None) -> float | None:
    if a is None or b is None or b == 0:
        return None
    return (a - b) / b * 100


def _stdev(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    var = sum((x - mean) ** 2 for x in values) / (len(values) - 1)
    return sqrt(max(var, 0))


def build_kline_history_context(
    *,
    symbol: str,
    market: MarketCode,
    lookback_days: int = 120,
) -> dict:
    collector = KlineCollector(market)
    klines = collector.get_klines(symbol, days=max(lookback_days, 80))
    summary = collector.get_kline_summary(symbol)
    if not klines:
        return {
            "available": False,
            "error": "无K线数据",
        }

    closes = [float(k.close) for k in klines if k.close is not None]
    highs = [float(k.high) for k in klines if k.high is not None]
    lows = [float(k.low) for k in klines if k.low is not None]
    current = closes[-1] if closes else None

    ret_5 = _pct(current, closes[-6] if len(closes) >= 6 else None)
    ret_20 = _pct(current, closes[-21] if len(closes) >= 21 else None)
    ret_60 = _pct(current, closes[-61] if len(closes) >= 61 else None)

    daily_rets: list[float] = []
    for i in range(1, len(closes)):
        base = closes[i - 1]
        if base == 0:
            continue
        daily_rets.append((closes[i] - base) / base * 100)
    vol_20 = _stdev(daily_rets[-20:]) if len(daily_rets) >= 20 else _stdev(daily_rets)

    high_20 = max(highs[-20:]) if len(highs) >= 20 else (max(highs) if highs else None)
    low_20 = min(lows[-20:]) if len(lows) >= 20 else (min(lows) if lows else None)

    breakout = "none"
    if current is not None and high_20 is not None and low_20 is not None:
        if current >= high_20 * 0.998:
            breakout = "near_high_breakout"
        elif current <= low_20 * 1.002:
            breakout = "near_low_breakdown"

    trend = str(summary.get("trend") or "未知")
    trend_state = (
        "bullish"
        if "多头" in trend
        else "bearish"
        if "空头" in trend
        else "neutral"
    )

    return {
        "available": True,
        "asof": summary.get("asof"),
        "computed_at": summary.get("computed_at"),
        "trend": trend,
        "trend_state": trend_state,
        "ret_5d": ret_5,
        "ret_20d": ret_20,
        "ret_60d": ret_60,
        "volatility_20d": vol_20,
        "high_20d": high_20,
        "low_20d": low_20,
        "breakout_state": breakout,
        "support_m": summary.get("support_m"),
        "resistance_m": summary.get("resistance_m"),
    }
