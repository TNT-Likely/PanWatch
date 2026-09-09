"""回测数据适配:KlineCollector → PriceBar,交易日历对齐。

- PriceBar 定义在本模块顶层,且 **不在顶层 import KlineCollector**(延迟导入),
  使回测内核与单测不被 httpx/网络库耦合,可离线运行。
- KlineCollector 返回的已是前复权(qfq)日线,停牌日天然无 bar,交易日历 = 实际 bar 序列。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PriceBar:
    """单根日 K(前复权)。"""

    date: str  # YYYY-MM-DD
    open: float
    high: float
    low: float
    close: float
    volume: float


def from_klines(klines) -> list[PriceBar]:
    """KlineData 列表 → 按日期升序的 PriceBar 列表。"""
    out: list[PriceBar] = []
    for k in klines or []:
        try:
            out.append(
                PriceBar(
                    date=str(k.date)[:10],
                    open=float(k.open),
                    high=float(k.high),
                    low=float(k.low),
                    close=float(k.close),
                    volume=float(k.volume or 0),
                )
            )
        except Exception:
            continue
    out.sort(key=lambda b: b.date)
    return out


def load_price_history(symbol: str, market, days: int = 250) -> list[PriceBar]:
    """走 KlineCollector 拉历史(延迟导入,避免顶层耦合网络库)。"""
    from src.collectors.kline_collector import KlineCollector
    from src.models.market import MarketCode

    try:
        mc = market if isinstance(market, MarketCode) else MarketCode(str(market).upper())
    except Exception:
        mc = MarketCode.CN
    try:
        klines = KlineCollector(mc).get_klines(symbol, days=days)
    except Exception as e:
        logger.warning(f"[回测] 拉取 {symbol} K线失败: {e}")
        return []
    return from_klines(klines)


def first_index_after(bars: list[PriceBar], date: str) -> int | None:
    """返回第一个 date 严格大于给定日期的 bar 下标(下一交易日,防 look-ahead)。"""
    for i, b in enumerate(bars):
        if b.date > date:
            return i
    return None
