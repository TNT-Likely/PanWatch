"""交易日历:回答「这一天开不开市」。

与 `MarketDef.is_trading_time()`(回答「当下是否在交易时段内」)互补 ——
盘前计划、日终摘要这类定时任务本身就发生在交易时段之外,只能用「是不是交易日」
来守卫,用时段判断会把它们永久拦死。

数据源
- **A 股**:akshare 交易日历(`tool_trade_date_hist_sina`),含法定节假日,权威。
  结果缓存在内存,由 `refresh()` 更新(启动预热 + 每日凌晨刷新)。
- **离线兜底**:内置交易所公布的 2026 年 CN/HK/US 休市和半日市安排。

降级原则
拿不到在线日历时优先使用内置年度日历;未覆盖年份退回「只判周末」。
少发一条是遗憾,漏发一整天是事故。

并发安全
同步接口只读内存缓存,**永不发起网络请求**;网络拉取集中在 `refresh()`
(内部 `asyncio.to_thread`)和 `refresh_blocking()`,避免阻塞事件循环。
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime
from zoneinfo import ZoneInfo

from src.platform.scheduling.exchange_calendar_data import HOLIDAYS, EARLY_CLOSES

logger = logging.getLogger(__name__)

# A 股交易日集合;None = 尚未加载或加载失败(此时使用内置年度日历兜底)
_CN_TRADING_DATES: frozenset[date] | None = None
# 日历覆盖区间,用于判断查询日期是否落在可信范围内(跨年未刷新时会超出)
_CN_RANGE: tuple[date, date] | None = None

_FALLBACK_TZ = "Asia/Shanghai"


def reset_cache() -> None:
    """清空日历缓存(配置变更或测试用)。"""
    global _CN_TRADING_DATES, _CN_RANGE
    _CN_TRADING_DATES = None
    _CN_RANGE = None


def _fetch_cn_trading_dates() -> frozenset[date]:
    """阻塞拉取 A 股交易日历。仅由 `refresh_blocking()` 调用。"""
    import akshare as ak

    df = ak.tool_trade_date_hist_sina()
    out: set[date] = set()
    for raw in df["trade_date"]:
        if isinstance(raw, datetime):
            out.add(raw.date())
        elif isinstance(raw, date):
            out.add(raw)
        else:
            out.add(date.fromisoformat(str(raw)[:10]))
    return frozenset(out)


def refresh_blocking() -> bool:
    """同步刷新 A 股交易日历。返回是否成功;失败不抛异常(保持降级行为)。"""
    global _CN_TRADING_DATES, _CN_RANGE
    try:
        dates = _fetch_cn_trading_dates()
    except Exception as e:
        logger.warning("[交易日历] A股日历拉取失败,保留缓存并使用内置日历兜底: %s", e)
        return False
    if not dates:
        logger.warning("[交易日历] A股日历为空,保留缓存并使用内置日历兜底")
        return False
    _CN_TRADING_DATES = dates
    _CN_RANGE = (min(dates), max(dates))
    logger.info(
        "[交易日历] A股日历已加载: %s 个交易日 (%s ~ %s)",
        len(dates),
        _CN_RANGE[0],
        _CN_RANGE[1],
    )
    return True


async def refresh() -> bool:
    """异步刷新日历(走线程池,不阻塞事件循环)。"""
    return await asyncio.to_thread(refresh_blocking)


def _to_market_code(market):
    """把 MarketCode / 字符串归一化为 MarketCode;无法识别返回 None。"""
    from src.platform.marketdata.models import MarketCode

    if isinstance(market, MarketCode):
        return market
    try:
        return MarketCode(str(market).strip().upper())
    except ValueError:
        return None


def _market_tz(code) -> ZoneInfo:
    from src.platform.marketdata.models import MARKETS

    md = MARKETS.get(code) if code else None
    return md.get_tz() if md else ZoneInfo(_FALLBACK_TZ)


def _now_in_market_tz(code) -> datetime:
    """该市场时区的当前时间。独立成函数便于测试注入。"""
    return datetime.now(_market_tz(code))


def _resolve_date(code, d: date | datetime | None) -> date:
    """把入参归一化为「该市场当地日期」。"""
    if d is None:
        return _now_in_market_tz(code).date()
    if isinstance(d, datetime):
        if d.tzinfo is not None:
            d = d.astimezone(_market_tz(code))
        return d.date()
    return d


def is_trading_day(market, d: date | datetime | None = None) -> bool:
    """给定市场的某一天是否开市。

    Args:
        market: `MarketCode` 或市场码字符串(CN/HK/US)。
        d: 目标日期;`None` 表示该市场时区的今天。带时区的 `datetime`
           会先换算到市场时区再取日期。
    """
    from src.platform.marketdata.models import MarketCode

    code = _to_market_code(market)
    target = _resolve_date(code, d)

    # 周末:三个市场都不开。零依赖、永远准确,放在最前面。
    if target.weekday() >= 5:
        return False

    # A 股:日历已加载且覆盖该日期时按日历判(含法定节假日)。
    if code == MarketCode.CN and _CN_TRADING_DATES and _CN_RANGE:
        if _CN_RANGE[0] <= target <= _CN_RANGE[1]:
            return target in _CN_TRADING_DATES
        logger.debug("[交易日历] %s 超出A股在线日历覆盖范围,尝试内置年度日历", target)

    # 港美股或在线日历未覆盖:使用交易所公布的年度休市安排。
    holidays = HOLIDAYS.get((code.value, target.year)) if code else None
    if holidays is not None:
        return target not in holidays

    # 未覆盖年份:只判周末。
    return True


def trading_sessions(market, d: date | datetime | None = None) -> list:
    """该市场当天的常规交易时段,包含半日市;休市日返回空列表。"""
    from src.platform.marketdata.models import MARKETS, TradingSession

    code = _to_market_code(market)
    target = _resolve_date(code, d)
    if code not in MARKETS or not is_trading_day(code, target):
        return []
    sessions = MARKETS[code].sessions
    close = EARLY_CLOSES.get((code.value, target))
    if close is None:
        return sessions
    return [TradingSession(s.start, min(s.end, close)) for s in sessions if s.start < close]


def market_status(market, dt: datetime | None = None) -> str:
    """按当地交易日和当日时段统一判断状态,请求路径只读内存。"""
    code = _to_market_code(market)
    if code is None:
        return "unknown"
    now = dt.astimezone(_market_tz(code)) if dt is not None else _now_in_market_tz(code)
    sessions = trading_sessions(code, now.date())
    if not sessions:
        return "closed"
    current = now.time()
    if any(s.start <= current <= s.end for s in sessions):
        return "trading"
    if current < sessions[0].start:
        return "pre_market"
    if current > sessions[-1].end:
        return "after_hours"
    return "break"


def any_market_trading_day(d: date | datetime | None = None) -> bool:
    """CN/HK/US 任一为交易日即 `True`。全市场休市(如周末)返回 `False`。"""
    from src.platform.marketdata.models import MarketCode

    return any(
        is_trading_day(m, d) for m in (MarketCode.CN, MarketCode.HK, MarketCode.US)
    )
