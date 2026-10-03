"""模拟盘调度器：60 秒间隔扫描建仓/平仓。"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from src.modules.paper_trading.paper_trading_engine import ENGINE
from src.platform.scheduling import trading_calendar as calendar
from src.platform.scheduling.exchange_calendar_data import EARLY_CLOSES
from src.platform.marketdata.models import MARKETS, MarketCode

logger = logging.getLogger(__name__)


def _any_market_trading() -> bool:
    """CN/HK/US 任一在交易时段即为 True。全休市时行情不动,扫描可跳过(行为中性)。"""
    for m in (MarketCode.CN, MarketCode.HK, MarketCode.US):
        md = MARKETS.get(m)
        if md and md.is_trading_time():
            return True
    return False


class PaperTradingScheduler:
    def __init__(self, timezone: str = "UTC", interval_seconds: int = 60):
        self.scheduler = AsyncIOScheduler(timezone=timezone)
        self.interval_seconds = max(15, int(interval_seconds))
        self._running = False

    async def _scan_job(self):
        if self._running:
            logger.debug("[模拟盘] 上轮扫描仍在执行，跳过本轮")
            return
        if not _any_market_trading():
            logger.debug("[模拟盘] 全市场休市,跳过本轮扫描")
            return
        self._running = True
        try:
            result = await ENGINE.scan_once()
            opened = result.get("opened", 0)
            closed = result.get("closed", 0)
            status = result.get("status", "?")
            # 有实际开/平仓才是业务事件,否则只是心跳。
            level = logging.INFO if (opened or closed) else logging.DEBUG
            logger.log(
                level,
                "[模拟盘] 扫描完成: opened=%s closed=%s status=%s",
                opened,
                closed,
                status,
            )
        except Exception as e:
            logger.exception(f"[模拟盘] 扫描异常: {e}")
        finally:
            self._running = False

    async def _premarket_job(self, market: str | None = None):
        """盘前计划通知。非交易日(周末/节假日)跳过。"""
        markets = calendar.eligible_markets([market] if market else None)
        if not markets:
            logger.debug("[模拟盘] 非交易日,跳过盘前计划通知")
            return
        try:
            from src.modules.paper_trading.paper_trading_notifier import send_premarket_plan
            await send_premarket_plan(markets=markets)
        except Exception as e:
            logger.exception(f"[模拟盘] 盘前计划通知异常: {e}")

    async def _summary_job(self, market: str | None = None):
        """日终摘要通知。非交易日(周末/节假日)跳过。"""
        markets = calendar.eligible_markets([market] if market else None)
        if not markets:
            logger.debug("[模拟盘] 非交易日,跳过日终摘要通知")
            return
        if market:
            now = calendar._now_in_market_tz(calendar._to_market_code(market))
            sessions = calendar.trading_sessions(market, now)
            due = datetime.combine(now.date(), sessions[-1].end, now.tzinfo) + timedelta(minutes=30)
            if (now.hour, now.minute) != (due.hour, due.minute):
                return
        try:
            from src.modules.paper_trading.paper_trading_notifier import send_daily_summary
            await send_daily_summary(markets=markets)
        except Exception as e:
            logger.exception(f"[模拟盘] 日终摘要通知异常: {e}")

    def start(self):
        self.scheduler.add_job(
            self._scan_job,
            "interval",
            seconds=self.interval_seconds,
            jitter=20,  # 抖动错峰,避免与价格提醒扫描每 60s 同刻并发写 SQLite
            id="paper_trading_scan",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )
        # Built-in paper notifications use each exchange's local clock. Agent
        # schedules and the configured scan interval are deliberately unchanged.
        for code, definition in MARKETS.items():
            self.scheduler.add_job(
                self._premarket_job, "cron", hour=9, minute=0,
                timezone=definition.timezone, args=[code.value],
                id=f"paper_trading_premarket_{code.value}", replace_existing=True,
                coalesce=True, max_instances=1,
            )
            closes = {definition.sessions[-1].end}
            closes.update(close for (market, _day), close in EARLY_CLOSES.items() if market == code.value)
            for close in closes:
                due = datetime.combine(datetime.today(), close) + timedelta(minutes=30)
                self.scheduler.add_job(
                    self._summary_job, "cron", hour=due.hour, minute=due.minute,
                    timezone=definition.timezone, args=[code.value],
                    id=f"paper_trading_summary_{code.value}_{due:%H%M}", replace_existing=True,
                    coalesce=True, max_instances=1,
                )
        self.scheduler.start()
        from src.platform.scheduling.scheduler_registry import register
        register("paper_trading", self.scheduler)
        logger.info(f"模拟盘调度器已启动，扫描间隔 {self.interval_seconds}s")

    def shutdown(self):
        try:
            self.scheduler.shutdown(wait=False)
        except Exception:
            pass
        logger.info("模拟盘调度器已关闭")
