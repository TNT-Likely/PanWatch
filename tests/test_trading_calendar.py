"""交易日历与非交易日通知守卫单元测试。"""

from __future__ import annotations

import asyncio
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from src.platform.scheduling import trading_calendar as tc
from src.platform.marketdata.models import MARKETS, MarketCode

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
def _reset_calendar(monkeypatch):
    """每個用例前後清空日曆快取,避免互相汙染。"""
    tc.reset_cache()
    monkeypatch.setattr(tc, "_fetch_tw_calendar", lambda: (
        frozenset({date(2026, 9, 25)}), frozenset(), 2026,
    ))
    yield
    tc.reset_cache()


@pytest.fixture
def loaded_calendar(monkeypatch):
    """注入固定台股休市表(不走網路)。"""
    monkeypatch.setattr(tc, "_fetch_tw_calendar", lambda: (
        frozenset({date(2026, 10, 1), date(2026, 10, 2), date(2026, 9, 25)}),
        frozenset(), 2026,
    ))
    assert tc.refresh_blocking() is True


# ---------------------------------------------------------------------------
# is_trading_day
# ---------------------------------------------------------------------------


def test_週末不是交易日_無需日曆():
    """週末即使沒有日曆也判為非交易日(零依賴、永遠準確)。"""
    assert tc.is_trading_day(MarketCode.TW, date(2026, 8, 8)) is False  # 週六
    assert tc.is_trading_day(MarketCode.TW, date(2026, 8, 9)) is False  # 週日
    assert tc.is_trading_day(MarketCode.HK, date(2026, 8, 8)) is False
    assert tc.is_trading_day(MarketCode.US, date(2026, 8, 9)) is False


def test_工作日是交易日(loaded_calendar):
    """日曆已載入時,普通工作日判為交易日。"""
    assert tc.is_trading_day(MarketCode.TW, date(2026, 8, 10)) is True  # 週一


def test_法定節假日不是交易日(loaded_calendar):
    """國慶(10/1 週四)靠日曆識別為休市 —— 週末判斷抓不到這一類。"""
    assert tc.is_trading_day(MarketCode.TW, date(2026, 10, 1)) is False
    assert tc.is_trading_day(MarketCode.TW, date(2026, 10, 2)) is False
    assert tc.is_trading_day(MarketCode.TW, date(2026, 10, 9)) is True


def test_日曆缺失時降級為只判週末():
    """拿不到日曆時工作日一律視為交易日 —— 寧可多跑,不可漏發一整天。"""
    assert tc._TW_CLOSED_DATES is None
    assert tc.is_trading_day(MarketCode.TW, date(2026, 10, 1)) is True
    assert tc.is_trading_day(MarketCode.TW, date(2026, 8, 8)) is False


def test_超出日曆覆蓋範圍時降級為只判週末(loaded_calendar):
    """查詢日期超出日曆區間(如跨年未重新整理)時降級,不誤判交易日為休市。"""
    assert tc.is_trading_day(MarketCode.TW, date(2027, 3, 1)) is True


def test_港美股无日历源_只判周末(loaded_calendar):
    """A 股日历不套用到港美股(节假日不同),它们只判周末。"""
    # 10/1 对港股/美股不是中国法定假日,不应被 A 股日历误伤
    assert tc.is_trading_day(MarketCode.US, date(2026, 10, 1)) is True
    assert tc.is_trading_day(MarketCode.HK, date(2026, 10, 1)) is True


def test_接受字串市場碼與datetime(loaded_calendar):
    """market 接受字串,日期接受 datetime(按市場時區歸到當地日)。"""
    assert tc.is_trading_day("TW", date(2026, 10, 1)) is False
    dt = datetime(2026, 10, 1, 10, 0, tzinfo=ZoneInfo("Asia/Taipei"))
    assert tc.is_trading_day("TW", dt) is False


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

    monkeypatch.setattr(tc, "_fetch_tw_calendar", _boom)
    assert tc.refresh_blocking() is False
    assert tc._TW_CLOSED_DATES is None
    assert tc.is_trading_day(MarketCode.TW, date(2026, 8, 10)) is True


def test_台股休市與交易時段(loaded_calendar):
    assert tc.is_trading_day(MarketCode.TW, date(2026, 9, 25)) is False
    assert tc.is_trading_day(MarketCode.TW, date(2026, 9, 24)) is True
    md = MARKETS[MarketCode.TW]
    assert md.is_trading_time(datetime(2026, 9, 24, 10, tzinfo=ZoneInfo("Asia/Taipei")))
    assert not md.is_trading_time(datetime(2026, 9, 24, 14, tzinfo=ZoneInfo("Asia/Taipei")))


def test_非同步重新整理不阻塞(monkeypatch):
    """refresh() 走 to_thread,結果與同步版一致。"""
    monkeypatch.setattr(tc, "_fetch_tw_calendar", lambda: (
        frozenset({date(2026, 10, 1)}), frozenset(), 2026,
    ))
    assert asyncio.run(tc.refresh()) is True
    assert tc.is_trading_day(MarketCode.TW, date(2026, 10, 1)) is False


# ---------------------------------------------------------------------------
# is_trading_time 复用交易日历(一处修复,全线受益)
# ---------------------------------------------------------------------------


def test_交易時段判斷在法定節假日返回False(loaded_calendar):
    """節假日的 10:00 處在時段區間內,但不是交易日 → 非交易時間。"""
    md = MARKETS[MarketCode.TW]
    holiday_10am = datetime(2026, 10, 1, 10, 0, tzinfo=ZoneInfo("Asia/Taipei"))
    assert md.is_trading_time(holiday_10am) is False


def test_交易時段判斷在正常交易日返回True(loaded_calendar):
    """交易日 10:00 在時段內 → 交易中。"""
    md = MARKETS[MarketCode.TW]
    trading_10am = datetime(2026, 8, 10, 10, 0, tzinfo=ZoneInfo("Asia/Taipei"))
    assert md.is_trading_time(trading_10am) is True


def test_交易日的非時段時間返回False(loaded_calendar):
    """交易日的 08:00 不在時段內 → 非交易時間。"""
    md = MARKETS[MarketCode.TW]
    before_open = datetime(2026, 8, 10, 8, 0, tzinfo=ZoneInfo("Asia/Taipei"))
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
        "src.modules.paper_trading.paper_trading_notifier.send_premarket_plan", _fake_premarket
    )
    monkeypatch.setattr(
        "src.modules.paper_trading.paper_trading_notifier.send_daily_summary", _fake_summary
    )
    return calls


def test_周末不发盘前计划和日终摘要(monkeypatch):
    """周末两条模拟盘定时通知都必须跳过 —— 这是用户报告的 bug。"""
    from src.modules.paper_trading.paper_trading_scheduler import PaperTradingScheduler

    calls = _patch_notifiers(monkeypatch)
    saturday = datetime(2026, 8, 8, 9, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
    monkeypatch.setattr(tc, "_now_in_market_tz", lambda code: saturday)

    sched = PaperTradingScheduler(timezone="Asia/Shanghai")
    asyncio.run(sched._premarket_job())
    asyncio.run(sched._summary_job())

    assert calls == {"premarket": 0, "summary": 0}


def test_法定节假日不发盘前计划和日终摘要(monkeypatch, loaded_calendar):
    """A股国庆期间(美股也休市的那几天)同样跳过。"""
    from src.modules.paper_trading.paper_trading_scheduler import PaperTradingScheduler

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
    from src.modules.paper_trading.paper_trading_scheduler import PaperTradingScheduler

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
    from src.modules.research.context_scheduler import ContextMaintenanceScheduler

    calls = {"n": 0}

    def _fake_refresh(**kwargs):
        calls["n"] += 1
        return {"count": 0}

    monkeypatch.setattr(
        "src.modules.research.context_scheduler.refresh_strategy_signals", _fake_refresh
    )
    saturday = datetime(2026, 8, 8, 9, 15, tzinfo=ZoneInfo("Asia/Shanghai"))
    monkeypatch.setattr(tc, "_now_in_market_tz", lambda code: saturday)

    sched = ContextMaintenanceScheduler(timezone="Asia/Shanghai")
    asyncio.run(sched._refresh_opportunities_job())

    assert calls["n"] == 0


def test_交易日照常刷新机会(monkeypatch, loaded_calendar):
    """交易日机会刷新不受守卫影响。"""
    from src.modules.research.context_scheduler import ContextMaintenanceScheduler

    calls = {"n": 0}

    def _fake_refresh(**kwargs):
        calls["n"] += 1
        return {"count": 3, "snapshot_date": "2026-08-10"}

    monkeypatch.setattr(
        "src.modules.research.context_scheduler.refresh_strategy_signals", _fake_refresh
    )
    monday = datetime(2026, 8, 10, 9, 15, tzinfo=ZoneInfo("Asia/Shanghai"))
    monkeypatch.setattr(tc, "_now_in_market_tz", lambda code: monday)

    sched = ContextMaintenanceScheduler(timezone="Asia/Shanghai")
    asyncio.run(sched._refresh_opportunities_job())

    assert calls["n"] == 1


def test_手动刷新机会不受非交易日守卫影响(monkeypatch):
    """手动触发是用户显式意图,周末也必须能跑。"""
    from src.modules.research.context_scheduler import ContextMaintenanceScheduler

    calls = {"n": 0}

    def _fake_refresh(**kwargs):
        calls["n"] += 1
        return {"count": 1}

    monkeypatch.setattr(
        "src.modules.research.context_scheduler.refresh_strategy_signals", _fake_refresh
    )
    saturday = datetime(2026, 8, 8, 9, 15, tzinfo=ZoneInfo("Asia/Shanghai"))
    monkeypatch.setattr(tc, "_now_in_market_tz", lambda code: saturday)

    sched = ContextMaintenanceScheduler(timezone="Asia/Shanghai")
    asyncio.run(sched.refresh_opportunities_once())

    assert calls["n"] == 1


def test_上下文后验评估仅在夜间运行且不启动补跑(monkeypatch):
    """长时间后验评估不应在 Web 服务启动后立即抢占 SQLite。"""
    from src.modules.research.context_scheduler import ContextMaintenanceScheduler

    sched = ContextMaintenanceScheduler(timezone="Asia/Shanghai")
    monkeypatch.setattr(sched.scheduler, "start", lambda: None)
    monkeypatch.setattr(
        "src.platform.scheduling.scheduler_registry.register", lambda *_args: None
    )

    sched.start()

    jobs = {job.id: job for job in sched.scheduler.get_jobs()}
    assert "context_maintenance_bootstrap_evaluate" not in jobs

    evaluate_job = jobs["context_maintenance_evaluate"]
    fields = {field.name: str(field) for field in evaluate_job.trigger.fields}
    assert fields["hour"] == "4"
    assert fields["minute"] == "30"
