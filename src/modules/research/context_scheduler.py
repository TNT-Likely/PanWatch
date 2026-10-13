"""上下文维护：Agent 后验评估、清理及显式启用的 Qlib 调度。"""

from __future__ import annotations

import asyncio
import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from src.platform.marketdata.collectors.kline_collector import kline_source
from src.modules.research.context_store import cleanup_context_data
from src.modules.research.prediction_outcome import evaluate_pending_prediction_outcomes
from src.modules.qlib_research.service import scheduled_tick

logger = logging.getLogger(__name__)


class ContextMaintenanceScheduler:
    def __init__(
        self,
        timezone: str = "UTC",
        evaluation_hour: int = 4,
        evaluation_minute: int = 30,
        snapshot_retention_days: int = 180,
        outcome_retention_days: int = 365,
    ):
        self.scheduler = AsyncIOScheduler(timezone=timezone)
        self.evaluation_hour = max(0, min(23, int(evaluation_hour)))
        self.evaluation_minute = max(0, min(59, int(evaluation_minute)))
        self.snapshot_retention_days = max(30, int(snapshot_retention_days))
        self.outcome_retention_days = max(60, int(outcome_retention_days))
        self._evaluating = False
        self._cleaning = False

    async def _evaluate_job(self):
        if self._evaluating:
            return
        self._evaluating = True
        try:
            stats = await asyncio.to_thread(evaluate_pending_prediction_outcomes)
            logger.debug("Agent prediction evaluation: %s", stats)
        except Exception:
            logger.exception("Agent prediction evaluation failed")
        finally:
            self._evaluating = False

    async def _cleanup_job(self):
        if self._cleaning:
            logger.debug("[上下文维护] 上一轮清理仍在执行，跳过本轮")
            return
        self._cleaning = True
        try:
            deleted = await asyncio.to_thread(
                cleanup_context_data,
                snapshot_days=self.snapshot_retention_days,
                topic_days=self.snapshot_retention_days,
                context_run_days=self.snapshot_retention_days,
                outcome_days=self.outcome_retention_days,
            )
            # deleted 是 dict,任一字段 >0 就是有清理动作
            has_work = bool(deleted and any(deleted.values()) if isinstance(deleted, dict) else deleted)
            level = logging.INFO if has_work else logging.DEBUG
            logger.log(level, "[上下文维护] 清理完成: %s", deleted)
        except Exception as e:
            logger.exception(f"[上下文维护] 清理异常: {e}")
        finally:
            self._cleaning = False

    async def evaluate_once(self) -> dict:
        return {"agent_predictions": await asyncio.to_thread(evaluate_pending_prediction_outcomes)}

    async def _qlib_job(self):
        try:
            await asyncio.to_thread(scheduled_tick)
        except Exception:
            logger.exception("Qlib scheduled task failed; no legacy fallback")

    async def cleanup_once(self) -> dict:
        return await asyncio.to_thread(
            cleanup_context_data,
            snapshot_days=self.snapshot_retention_days,
            topic_days=self.snapshot_retention_days,
            context_run_days=self.snapshot_retention_days,
            outcome_days=self.outcome_retention_days,
        )

    async def _refresh_trading_calendar_job(self):
        """刷新本地近期窗口,不联网拉取历史日历。"""
        from src.platform.scheduling.trading_calendar import refresh

        try:
            await refresh()
        except Exception as e:  # refresh 内部已兜异常,这里只防意外
            logger.exception(f"[上下文维护] 交易日历刷新异常: {e}")

    def start(self):
        self.scheduler.add_job(
            self._evaluate_job,
            "cron",
            hour=self.evaluation_hour,
            minute=self.evaluation_minute,
            jitter=120,  # 错峰,避免与 price_alert/paper_trading(60s)同刻写 SQLite
            id="context_maintenance_evaluate",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )
        self.scheduler.add_job(
            self._cleanup_job,
            "cron",
            hour=4,
            minute=15,
            jitter=120,
            id="context_maintenance_cleanup",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )
        # 交易日历每日刷新 —— 03:00,早于所有盘前通知
        self.scheduler.add_job(
            self._refresh_trading_calendar_job,
            "cron",
            hour=3,
            minute=0,
            jitter=120,
            id="context_maintenance_trading_calendar",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )
        self.scheduler.add_job(
            self._qlib_job, "interval", minutes=15, id="qlib_daily",
            replace_existing=True, coalesce=True, max_instances=1,
        )
        self.scheduler.start()
        from src.platform.scheduling.scheduler_registry import register
        register("context", self.scheduler)
        logger.info(
            "上下文维护调度器已启动（后验评估 %02d:%02d，快照保留 %s 天，后验保留 %s 天，Qlib 显式启用后每 15 分钟检查，交易日历刷新 03:00）",
            self.evaluation_hour,
            self.evaluation_minute,
            self.snapshot_retention_days,
            self.outcome_retention_days,
        )

    def shutdown(self):
        try:
            self.scheduler.shutdown(wait=False)
        except Exception:
            pass
        logger.info("上下文维护调度器已关闭")
