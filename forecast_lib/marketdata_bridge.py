"""8010 规范化接入 PanWatch 市场数据源层(marketdata 包) — 扩展维度。

只接海外可达且准确的维度:
- dragon_tiger (ftshare vendor): 龙虎榜, 海外可达, 数据完整 ✅
- (待排查) northbound (ths): 北向, 当前返回 0, 需确认参数/时段
- hot_boards (discovery/东财): 海外 502 不可达, 暂不接

注意: 直接 import marketdata 包(宿主机可 import, 路径已加), 不走 8000 HTTP。
数据源 vendor 选择遵循页面 data_sources 配置(enabled+priority)。
"""
from __future__ import annotations

import logging
import sys

logger = logging.getLogger(__name__)

_MARKETDATA_SRC = "/tmp/PanWatch/packages/marketdata/src"
_DT_CACHE: dict = {}  # date -> list (进程内缓存, 龙虎榜日频)


def _ensure_path():
    if _MARKETDATA_SRC not in sys.path:
        sys.path.insert(0, _MARKETDATA_SRC)


def _build_md_dragon_tiger():
    """构造只含 ftshare dragon_tiger 的 MarketData(轻量, 不触发其他 vendor)。"""
    _ensure_path()
    from marketdata import MarketData
    from marketdata.ports import SourceConfig
    from marketdata.defaults import StaticConfigProvider
    mapping = {
        "dragon_tiger": [SourceConfig(
            vendor="ftshare", priority=0, enabled=True,
            config={}, supports_batch=False, key_pool=[],
        )],
    }
    return MarketData(StaticConfigProvider(mapping))


def get_dragon_tiger(date: str | None = None, symbol: str | None = None) -> list:
    """获取龙虎榜(经 marketdata ftshare vendor, 海外可达)。

    date: YYYYMMDD, 不传则用最近一个交易日(由调用方传, 因需交易日历)
    symbol: 可选, 过滤某只股票是否在榜

    返回 list of dict:
      [{trade_date, symbol, name, close, change_pct, net_buy, buy_amt, sell_amt, on_list(bool)}]
    """
    if date is None:
        date = _latest_trade_date()
    if date in _DT_CACHE:
        items = _DT_CACHE[date]
    else:
        try:
            md = _build_md_dragon_tiger()
            raw = md.dragon_tiger(date=date, market="CN")
            items = [{
                "trade_date": getattr(i, "trade_date", date),
                "symbol": getattr(i, "symbol", ""),
                "name": getattr(i, "name", ""),
                "close": getattr(i, "close", None),
                "change_pct": getattr(i, "change_pct", None),
                "net_buy": getattr(i, "net_buy", None),
                "buy_amt": getattr(i, "buy_amt", None),
                "sell_amt": getattr(i, "sell_amt", None),
            } for i in (raw or [])]
            _DT_CACHE[date] = items
            logger.info(f"龙虎榜({date})获取 {len(items)} 条")
        except Exception as e:
            logger.warning(f"龙虎榜获取失败 [{date}]: {e}")
            return []

    if symbol:
        sym_norm = symbol.replace(".SZ", "").replace(".SH", "")
        hit = [r for r in items if r["symbol"].replace(".SZ", "").replace(".SH", "") == sym_norm]
        return hit
    return items


def _latest_trade_date() -> str:
    """简单最近交易日: 回退到上周五(周末场景), 实际应由交易日历算。"""
    from datetime import datetime, timedelta
    d = datetime.now()
    # 回退到最近的一个周五(简化: 非交易日取前一个工作日)
    while d.weekday() >= 5:  # 5=Sat, 6=Sun
        d -= timedelta(days=1)
    return d.strftime("%Y%m%d")
