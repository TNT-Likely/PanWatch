"""上下文维护调度器：后验评估 + 过期数据清理。"""

from __future__ import annotations

import logging

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from src.core.context_store import cleanup_context_data
from src.core.prediction_outcome import evaluate_pending_prediction_outcomes

logger = logging.getLogger(__name__)


class ContextMaintenanceScheduler:
    def __init__(
        self,
        timezone: str = "UTC",
        eval_interval_hours: int = 6,
        snapshot_retention_days: int = 180,
        outcome_retention_days: int = 365,
    ):
        self.scheduler = AsyncIOScheduler(timezone=timezone)
        self.eval_interval_hours = max(1, int(eval_interval_hours))
        self.snapshot_retention_days = max(30, int(snapshot_retention_days))
        self.outcome_retention_days = max(60, int(outcome_retention_days))
        self._evaluating = False
        self._cleaning = False

    async def _evaluate_job(self):
        if self._evaluating:
            logger.info("[上下文维护] 上一轮后验评估仍在执行，跳过本轮")
            return
        self._evaluating = True
        try:
            stats = evaluate_pending_prediction_outcomes()
            logger.info(
                "[上下文维护] 后验评估完成: pending=%s eligible=%s evaluated=%s skipped_not_due=%s skipped_no_price=%s",
                stats.get("total_pending", 0),
                stats.get("eligible", 0),
                stats.get("evaluated", 0),
                stats.get("skipped_not_due", 0),
                stats.get("skipped_no_price", 0),
            )
        except Exception as e:
            logger.exception(f"[上下文维护] 后验评估异常: {e}")
        finally:
            self._evaluating = False

    async def _cleanup_job(self):
        if self._cleaning:
            logger.info("[上下文维护] 上一轮清理仍在执行，跳过本轮")
            return
        self._cleaning = True
        try:
            deleted = cleanup_context_data(
                snapshot_days=self.snapshot_retention_days,
                topic_days=self.snapshot_retention_days,
                context_run_days=self.snapshot_retention_days,
                outcome_days=self.outcome_retention_days,
            )
            logger.info("[上下文维护] 清理完成: %s", deleted)
        except Exception as e:
            logger.exception(f"[上下文维护] 清理异常: {e}")
        finally:
            self._cleaning = False

    async def evaluate_once(self) -> dict:
        return evaluate_pending_prediction_outcomes()

    async def cleanup_once(self) -> dict:
        return cleanup_context_data(
            snapshot_days=self.snapshot_retention_days,
            topic_days=self.snapshot_retention_days,
            context_run_days=self.snapshot_retention_days,
            outcome_days=self.outcome_retention_days,
        )

    def start(self):
        self.scheduler.add_job(
            self._evaluate_job,
            "interval",
            hours=self.eval_interval_hours,
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
            id="context_maintenance_cleanup",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )
        self.scheduler.start()
        logger.info(
            "上下文维护调度器已启动（后验评估间隔 %sh，快照保留 %s 天，后验保留 %s 天）",
            self.eval_interval_hours,
            self.snapshot_retention_days,
            self.outcome_retention_days,
        )

    def shutdown(self):
        try:
            self.scheduler.shutdown(wait=False)
        except Exception:
            pass
        logger.info("上下文维护调度器已关闭")
