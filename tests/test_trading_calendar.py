"""交易日历与非交易日通知守卫单元测试。"""

from __future__ import annotations

import asyncio
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from src.core import trading_calendar as tc
from src.models.market import MARKETS, MarketCode

# 2026 年真实日历切片:8/8 周六、8/9 周日休市;8/10 周一开市;
# 10/1~10/8 国庆休市(其中 10/1 是周四 —— 工作日却休市,只靠周末判断抓不到)。
_FAKE_CN_DATES = frozenset(
    {
        date(2026, 8, 3),
        date(2026, 8, 4),
        date(2026, 8, 5),
        date(2026, 8, 6),
        date(2026, 8, 7),
        date(2026, 8, 10),
        date(2026, 8, 11),
        date(2026, 8, 12),
        date(2026, 8, 13),
        date(2026, 8, 14),
        date(2026, 9, 28),
        date(2026, 9, 29),
        date(2026, 9, 30),
        date(2026, 10, 9),
    }
)


@pytest.fixture(autouse=True)
def _reset_calendar():
    """每个用例前后清空日历缓存,避免互相污染。"""
    tc.reset_cache()
    yield
    tc.reset_cache()


@pytest.fixture
def loaded_calendar(monkeypatch):
    """注入固定 A 股交易日历(不走网络)。"""
    monkeypatch.setattr(tc, "_fetch_cn_trading_dates", lambda: _FAKE_CN_DATES)
    assert tc.refresh_blocking() is True


# ---------------------------------------------------------------------------
# is_trading_day
# ---------------------------------------------------------------------------


def test_周末不是交易日_无需日历():
    """周末即使没有日历也判为非交易日(零依赖、永远准确)。"""
    assert tc.is_trading_day(MarketCode.CN, date(2026, 8, 8)) is False  # 周六
    assert tc.is_trading_day(MarketCode.CN, date(2026, 8, 9)) is False  # 周日
    assert tc.is_trading_day(MarketCode.HK, date(2026, 8, 8)) is False
    assert tc.is_trading_day(MarketCode.US, date(2026, 8, 9)) is False


def test_工作日是交易日(loaded_calendar):
    """日历已加载时,普通工作日判为交易日。"""
    assert tc.is_trading_day(MarketCode.CN, date(2026, 8, 10)) is True  # 周一


def test_法定节假日不是交易日(loaded_calendar):
    """国庆(10/1 周四)靠日历识别为休市 —— 周末判断抓不到这一类。"""
    assert tc.is_trading_day(MarketCode.CN, date(2026, 10, 1)) is False
    assert tc.is_trading_day(MarketCode.CN, date(2026, 10, 2)) is False
    assert tc.is_trading_day(MarketCode.CN, date(2026, 10, 9)) is True  # 节后首个交易日


def test_日历缺失时降级为只判周末():
    """拿不到日历时工作日一律视为交易日 —— 宁可多跑,不可漏发一整天。"""
    assert tc._CN_TRADING_DATES is None
    assert tc.is_trading_day(MarketCode.CN, date(2026, 10, 1)) is True  # 降级:识别不出国庆
    assert tc.is_trading_day(MarketCode.CN, date(2026, 8, 8)) is False  # 但周末照样拦住


def test_超出日历覆盖范围时降级为只判周末(loaded_calendar):
    """查询日期超出日历区间(如跨年未刷新)时降级,不误判交易日为休市。"""
    assert tc.is_trading_day(MarketCode.CN, date(2027, 3, 1)) is True  # 2027-03-01 是周一


def test_港美股无日历源_只判周末(loaded_calendar):
    """A 股日历不套用到港美股(节假日不同),它们只判周末。"""
    # 10/1 对港股/美股不是中国法定假日,不应被 A 股日历误伤
    assert tc.is_trading_day(MarketCode.US, date(2026, 10, 1)) is True
    assert tc.is_trading_day(MarketCode.HK, date(2026, 10, 1)) is True


def test_接受字符串市场码与datetime(loaded_calendar):
    """market 接受字符串,日期接受 datetime(按市场时区归到当地日)。"""
    assert tc.is_trading_day("CN", date(2026, 10, 1)) is False
    dt = datetime(2026, 10, 1, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    assert tc.is_trading_day("CN", dt) is False


def test_any_market_trading_day(loaded_calendar):
    """周末三市场全休 → False;工作日至少一个开市 → True。"""
    assert tc.any_market_trading_day(date(2026, 8, 8)) is False  # 周六
    assert tc.any_market_trading_day(date(2026, 8, 10)) is True  # 周一
    # A股国庆休市但美股开市 → 仍为 True
    assert tc.any_market_trading_day(date(2026, 10, 1)) is True


def test_刷新失败不抛异常且保持降级(monkeypatch):
    """日历拉取抛异常时 refresh 返回 False,缓存保持空,行为降级而非崩溃。"""

    def _boom():
        raise RuntimeError("network down")

    monkeypatch.setattr(tc, "_fetch_cn_trading_dates", _boom)
    assert tc.refresh_blocking() is False
    assert tc._CN_TRADING_DATES is None
    assert tc.is_trading_day(MarketCode.CN, date(2026, 8, 10)) is True


def test_异步刷新不阻塞(monkeypatch):
    """refresh() 走 to_thread,结果与同步版一致。"""
    monkeypatch.setattr(tc, "_fetch_cn_trading_dates", lambda: _FAKE_CN_DATES)
    assert asyncio.run(tc.refresh()) is True
    assert tc.is_trading_day(MarketCode.CN, date(2026, 10, 1)) is False


# ---------------------------------------------------------------------------
# is_trading_time 复用交易日历(一处修复,全线受益)
# ---------------------------------------------------------------------------


def test_交易时段判断在法定节假日返回False(loaded_calendar):
    """节假日的 10:00 处在时段区间内,但不是交易日 → 非交易时间。"""
    md = MARKETS[MarketCode.CN]
    holiday_10am = datetime(2026, 10, 1, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    assert md.is_trading_time(holiday_10am) is False


def test_交易时段判断在正常交易日返回True(loaded_calendar):
    """交易日 10:00 在时段内 → 交易中。"""
    md = MARKETS[MarketCode.CN]
    trading_10am = datetime(2026, 8, 10, 10, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    assert md.is_trading_time(trading_10am) is True


def test_交易日的非时段时间返回False(loaded_calendar):
    """交易日的 08:00 不在时段内 → 非交易时间。"""
    md = MARKETS[MarketCode.CN]
    before_open = datetime(2026, 8, 10, 8, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    assert md.is_trading_time(before_open) is False


# ---------------------------------------------------------------------------
# 模拟盘定时通知的非交易日守卫(用户报告的 bug)
# ---------------------------------------------------------------------------


def _patch_notifiers(monkeypatch) -> dict[str, int]:
    """把两个通知函数替换成计数器,用于断言是否被调用。"""
    calls = {"premarket": 0, "summary": 0}

    async def _fake_premarket():
        calls["premarket"] += 1

    async def _fake_summary():
        calls["summary"] += 1

    monkeypatch.setattr(
        "src.core.paper_trading_notifier.send_premarket_plan", _fake_premarket
    )
    monkeypatch.setattr(
        "src.core.paper_trading_notifier.send_daily_summary", _fake_summary
    )
    return calls


def test_周末不发盘前计划和日终摘要(monkeypatch):
    """周末两条模拟盘定时通知都必须跳过 —— 这是用户报告的 bug。"""
    from src.core.paper_trading_scheduler import PaperTradingScheduler

    calls = _patch_notifiers(monkeypatch)
    saturday = datetime(2026, 8, 8, 9, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    monkeypatch.setattr(tc, "_now_in_market_tz", lambda code: saturday)

    sched = PaperTradingScheduler(timezone="Asia/Shanghai")
    asyncio.run(sched._premarket_job())
    asyncio.run(sched._summary_job())

    assert calls == {"premarket": 0, "summary": 0}


def test_法定节假日不发盘前计划和日终摘要(monkeypatch, loaded_calendar):
    """A股国庆期间(美股也休市的那几天)同样跳过。"""
    from src.core.paper_trading_scheduler import PaperTradingScheduler

    calls = _patch_notifiers(monkeypatch)
    # 10/3 是周六:三市场全休 → 必须跳过
    holiday = datetime(2026, 10, 3, 9, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    monkeypatch.setattr(tc, "_now_in_market_tz", lambda code: holiday)

    sched = PaperTradingScheduler(timezone="Asia/Shanghai")
    asyncio.run(sched._premarket_job())
    asyncio.run(sched._summary_job())

    assert calls == {"premarket": 0, "summary": 0}


def test_交易日照常发盘前计划和日终摘要(monkeypatch, loaded_calendar):
    """交易日不受守卫影响,通知照常发送。"""
    from src.core.paper_trading_scheduler import PaperTradingScheduler

    calls = _patch_notifiers(monkeypatch)
    monday = datetime(2026, 8, 10, 9, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    monkeypatch.setattr(tc, "_now_in_market_tz", lambda code: monday)

    sched = PaperTradingScheduler(timezone="Asia/Shanghai")
    asyncio.run(sched._premarket_job())
    asyncio.run(sched._summary_job())

    assert calls == {"premarket": 1, "summary": 1}


# ---------------------------------------------------------------------------
# 机会刷新的非交易日守卫(周末重算全市场只是白烧资源)
# ---------------------------------------------------------------------------


def test_周末跳过机会刷新(monkeypatch):
    """周末不重算机会池 —— 行情没变,扫全市场纯属浪费。"""
    from src.core.context_scheduler import ContextMaintenanceScheduler

    calls = {"n": 0}

    def _fake_refresh(**kwargs):
        calls["n"] += 1
        return {"count": 0}

    monkeypatch.setattr(
        "src.core.context_scheduler.refresh_strategy_signals", _fake_refresh
    )
    saturday = datetime(2026, 8, 8, 9, 15, tzinfo=ZoneInfo("Asia/Shanghai"))
    monkeypatch.setattr(tc, "_now_in_market_tz", lambda code: saturday)

    sched = ContextMaintenanceScheduler(timezone="Asia/Shanghai")
    asyncio.run(sched._refresh_opportunities_job())

    assert calls["n"] == 0


def test_交易日照常刷新机会(monkeypatch, loaded_calendar):
    """交易日机会刷新不受守卫影响。"""
    from src.core.context_scheduler import ContextMaintenanceScheduler

    calls = {"n": 0}

    def _fake_refresh(**kwargs):
        calls["n"] += 1
        return {"count": 3, "snapshot_date": "2026-08-10"}

    monkeypatch.setattr(
        "src.core.context_scheduler.refresh_strategy_signals", _fake_refresh
    )
    monday = datetime(2026, 8, 10, 9, 15, tzinfo=ZoneInfo("Asia/Shanghai"))
    monkeypatch.setattr(tc, "_now_in_market_tz", lambda code: monday)

    sched = ContextMaintenanceScheduler(timezone="Asia/Shanghai")
    asyncio.run(sched._refresh_opportunities_job())

    assert calls["n"] == 1


def test_手动刷新机会不受非交易日守卫影响(monkeypatch):
    """手动触发是用户显式意图,周末也必须能跑。"""
    from src.core.context_scheduler import ContextMaintenanceScheduler

    calls = {"n": 0}

    def _fake_refresh(**kwargs):
        calls["n"] += 1
        return {"count": 1}

    monkeypatch.setattr(
        "src.core.context_scheduler.refresh_strategy_signals", _fake_refresh
    )
    saturday = datetime(2026, 8, 8, 9, 15, tzinfo=ZoneInfo("Asia/Shanghai"))
    monkeypatch.setattr(tc, "_now_in_market_tz", lambda code: saturday)

    sched = ContextMaintenanceScheduler(timezone="Asia/Shanghai")
    asyncio.run(sched.refresh_opportunities_once())

    assert calls["n"] == 1
