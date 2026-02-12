import logging
import time
from typing import Callable, Awaitable

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from src.agents.base import BaseAgent, AgentContext
from src.core.agent_runs import record_agent_run
from src.core.log_context import log_context
from src.models.market import MARKETS
from src.core.schedule_parser import parse_schedule

logger = logging.getLogger(__name__)


class AgentScheduler:
    """Agent 调度器"""

    def __init__(self, timezone: str = "UTC"):
        self.scheduler = AsyncIOScheduler()
        self.agents: dict[str, BaseAgent] = {}
        self.execution_modes: dict[str, str] = {}
        self.timezone = timezone
        # 改为存储 context 构建函数，而非固定 context
        self.context_builder: Callable[[str], AgentContext] | None = None

    def set_context_builder(self, builder: Callable[[str], AgentContext]):
        """设置 context 构建函数（每次执行时动态构建）"""
        self.context_builder = builder

    def register(self, agent: BaseAgent, schedule: str, execution_mode: str = "batch"):
        """
        注册 Agent 到调度器。

        Args:
            agent: Agent 实例
            schedule: 调度表达式
                - cron 格式: "分 时 日 月 周" (5 部分)
                - interval 格式: "interval:3m" 或 "interval:30s"
            execution_mode: 执行模式 batch/single（single 将逐只股票执行 run_single）
        """
        self.agents[agent.name] = agent
        self.execution_modes[agent.name] = execution_mode or "batch"

        # 解析调度表达式
        # cron 使用 5 段: "分 时 日 月 周"
        # 其中 day_of_week 的数字按 POSIX cron 语义(1-5=周一到周五)，会在内部做一次归一化。
        trigger = parse_schedule(schedule, timezone=self.timezone)

        self.scheduler.add_job(
            self._run_agent,
            trigger=trigger,
            args=[agent.name],
            id=agent.name,
            name=agent.display_name,
            replace_existing=True,
        )

        logger.info(f"注册 Agent: {agent.display_name} (schedule: {schedule})")

    # NOTE: cron/interval 解析逻辑统一放在 src/core/schedule_parser.py

    async def _run_agent(self, agent_name: str):
        """执行指定 Agent（动态构建 context）"""
        if not self.context_builder:
            logger.error("context_builder 未设置")
            return

        agent = self.agents.get(agent_name)
        if not agent:
            logger.error(f"Agent 未找到: {agent_name}")
            return

        start = time.monotonic()
        trace_id = f"sch-{agent_name}-{int(time.time() * 1000)}"
        try:
            with log_context(
                trace_id=trace_id,
                run_id=trace_id,
                agent_name=agent_name,
                event="agent_run",
                tags={"trigger_source": "schedule"},
            ):
                # 每次执行时动态构建 context（获取最新配置）
                context = self.context_builder(agent_name)
                logger.info(f"[调度] 开始执行 Agent: {agent.display_name}")
                mode = self.execution_modes.get(agent_name, "batch")
                if mode == "single" and hasattr(agent, "run_single"):
                    processed = 0
                    skipped = 0
                    errors: list[str] = []
                    for stock in list(context.watchlist):
                        market_def = MARKETS.get(stock.market)
                        if market_def and not market_def.is_trading_time():
                            skipped += 1
                            logger.info(
                                f"[调度] 跳过 {agent.display_name} {stock.symbol}（{market_def.name} 非交易时段）"
                            )
                            continue
                        try:
                            res = await agent.run_single(context, stock.symbol)  # type: ignore[attr-defined]
                            processed += 1
                            try:
                                notify_error = (
                                    (res.raw_data or {}).get("notify_error")
                                    if res
                                    else ""
                                )
                            except Exception:
                                notify_error = ""
                            if notify_error:
                                errors.append(f"{stock.symbol} notify: {notify_error}")
                        except Exception as e:
                            logger.error(
                                f"Agent [{agent_name}] 单只执行失败 {stock.symbol}: {e}",
                                exc_info=True,
                            )
                            errors.append(f"{stock.symbol}: {e}")
                    logger.info(
                        f"[调度] Agent 单只模式执行完成: {agent.display_name}（执行{processed}，跳过{skipped}，共{len(context.watchlist)}）"
                    )
                    duration_ms = int((time.monotonic() - start) * 1000)
                    record_agent_run(
                        agent_name=agent_name,
                        status="failed" if errors else "success",
                        result=f"single mode executed {processed}, skipped {skipped}, total {len(context.watchlist)}",
                        error="; ".join(errors),
                        duration_ms=duration_ms,
                        trace_id=trace_id,
                        trigger_source="schedule",
                        model_label=context.model_label,
                    )
                else:
                    result = await agent.run(context)
                    duration_ms = int((time.monotonic() - start) * 1000)
                    notify_error = ""
                    try:
                        notify_error = (result.raw_data or {}).get("notify_error") or ""
                    except Exception:
                        notify_error = ""
                    raw = result.raw_data or {}
                    record_agent_run(
                        agent_name=agent_name,
                        status="failed" if notify_error else "success",
                        result=(result.content or "")[:2000],
                        error=(notify_error or "")[:2000],
                        duration_ms=duration_ms,
                        trace_id=trace_id,
                        trigger_source="schedule",
                        notify_attempted=(
                            "notified" in raw
                            or "notify_error" in raw
                            or "notify_skipped" in raw
                        ),
                        notify_sent=bool(raw.get("notified", False)),
                        model_label=context.model_label,
                    )
                logger.info(f"[调度] Agent 执行完成: {agent.display_name}")
        except Exception as e:
            logger.error(f"Agent [{agent_name}] 调度执行异常: {e}", exc_info=True)
            duration_ms = int((time.monotonic() - start) * 1000)
            record_agent_run(
                agent_name=agent_name,
                status="failed",
                error=str(e),
                duration_ms=duration_ms,
                trace_id=trace_id,
                trigger_source="schedule",
            )

    async def trigger_now(self, agent_name: str):
        """立即执行某个 Agent（手动触发）"""
        await self._run_agent(agent_name)

    def start(self):
        """启动调度器"""
        self.scheduler.start()
        logger.info(f"调度器已启动，已注册 {len(self.agents)} 个 Agent")

        # 打印所有已注册的任务
        jobs = self.scheduler.get_jobs()
        for job in jobs:
            logger.info(f"  - {job.name}: 下次执行 {job.next_run_time}")

    def shutdown(self):
        """关闭调度器"""
        self.scheduler.shutdown()
        logger.info("调度器已关闭")
