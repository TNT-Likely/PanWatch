"""盘前决策流水线 Agent:五阶段编排。

阶段流:collect(交易日守卫+阶段1输入) → analyze(五阶段串行):
1. 宏观三卡(LLM,门禁 STOP/snapshot_only/弱通道封顶)
2. 板块预测(纯 Python 初筛 → LLM 修正 → SectorPrediction upsert)
3. 选股三价(硬过滤 → 三高 → 三价 → V11 → LLM 终评,财务预算受控)
4. 财报核验(fail-closed 六项)
5. 落库推送(WriteSet 两段式)+ 报告/通知

每一阶段 try/except 记 stage_error 降级不中断;trace_id 贯穿并上报阶段进度;
LLM 调用受 llm_timeout_seconds 硬超时;整体受 pipeline_timeout_minutes 约束。
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from typing import Any

from src.modules.automation import premarket_config as pcfg
from src.modules.automation.base import AgentContext, AnalysisResult, BaseAgent
from src.modules.automation.premarket_pipeline import report as report_mod
from src.modules.automation.premarket_pipeline import stages, verifier, writer
from src.modules.automation.premarket_pipeline.types import (
    MacroCards,
    PickCandidate,
    SectorForecast,
)
from src.platform.observability.log_context import log_context

logger = logging.getLogger(__name__)

class PremarketPipelineAgent(BaseAgent):
    name = pcfg.AGENT_NAME
    display_name = pcfg.SUGGESTION_AGENT_LABEL
    description = (
        "盘前五阶段流水线:宏观三卡 → 板块预测 → 选股三价 → 财报核验 → 落库推送。"
        "非交易日/日历为空自动降级,产出三价候选与提醒规则。"
    )

    def __init__(
        self,
        pipeline_timeout_minutes: int = pcfg.DEFAULT_PIPELINE_TIMEOUT_MINUTES,
        llm_timeout_seconds: int = pcfg.DEFAULT_LLM_TIMEOUT_SECONDS,
        emit_paper_trading_signal: bool = pcfg.DEFAULT_EMIT_PAPER_TRADING_SIGNAL,
        auto_create_alerts: bool = pcfg.DEFAULT_AUTO_CREATE_ALERTS,
        max_candidates: int = pcfg.DEFAULT_MAX_CANDIDATES,
        board_top_n: int = pcfg.DEFAULT_BOARD_TOP_N,
        mv_min_e8: float = pcfg.DEFAULT_MV_MIN_E8,
        mv_max_e8: float = pcfg.DEFAULT_MV_MAX_E8,
    ):
        self.pipeline_timeout_minutes = max(1, int(pipeline_timeout_minutes))
        self.llm_timeout_seconds = max(5, int(llm_timeout_seconds))
        self.emit_paper_trading_signal = bool(emit_paper_trading_signal)
        self.auto_create_alerts = bool(auto_create_alerts)
        self.max_candidates = max(1, int(max_candidates))
        self.board_top_n = max(1, int(board_top_n))
        self.mv_min_e8 = float(mv_min_e8)
        self.mv_max_e8 = float(mv_max_e8)

    # ─────────────────── BaseAgent 接口 ───────────────────

    async def collect(self, context: AgentContext) -> dict:
        """交易日守卫 + 阶段1 输入采集(四通道 fail-soft)。"""
        trace_id = self._trace_id(context)
        with log_context(trace_id=trace_id, agent_name=self.name, event="pm_progress"):
            if not stages.is_trading_day_cn():
                logger.info("[盘前流水线][%s] 非交易日,跳过(不产报告)", trace_id)
                return {
                    "skip": True,
                    "skip_reason": "非交易日(A 股休市)",
                    "trace_id": trace_id,
                }
            return await asyncio.to_thread(self._collect_channels, trace_id)

    def build_prompt(self, data: dict, context: AgentContext) -> tuple[str, str]:
        # 本 agent 不走单次 prompt;三段 prompt 由 stages 按配置渲染。
        return "", ""

    async def analyze(self, context: AgentContext, data: dict) -> AnalysisResult:
        """五阶段编排(覆写 BaseAgent.analyze)。"""
        trace_id = str(data.get("trace_id") or self._trace_id(context))
        raw: dict[str, Any] = {"trace_id": trace_id, "stages": [], "stage_errors": []}

        if data.get("skip"):
            raw["skipped"] = True
            raw["skip_reason"] = data.get("skip_reason") or ""
            return AnalysisResult(
                agent_name=self.name,
                title=f"【{self.display_name}】已跳过",
                content=f"今日非交易日,流水线未运行({raw['skip_reason']})。",
                raw_data=raw,
            )

        # 双跑互斥:同 agent 仍有 running 生命周期记录则跳过(45 分钟 TTL 内,排除自身 trace)。
        # 本 agent 的两条生产触发路径(AgentScheduler 批量 / trigger_agent 手动)都只在
        # 结束时写终态、全程不写 running 行,因此管线自管 running 标记:
        # 进入管线时 start_agent_run 插入 running 行,终态(成功/失败/超时)必经
        # _finish_run_marker 落库——互斥因此对两条路径都真实生效。
        active = self._find_running_run(exclude_trace=trace_id)
        if active:
            raw["skipped"] = True
            raw["skip_reason"] = f"同日已有运行中的流水线(trace={active})"
            logger.warning("[盘前流水线][%s] 双跑互斥命中: %s", trace_id, active)
            return AnalysisResult(
                agent_name=self.name,
                title=f"【{self.display_name}】已跳过",
                content=f"检测到同日已有运行中的流水线({active}),本次跳过。",
                raw_data=raw,
            )
        self._start_run_marker(trace_id)

        with log_context(trace_id=trace_id, agent_name=self.name, event="pm_progress"):
            started = time.monotonic()
            try:
                result = await asyncio.wait_for(
                    self._run_pipeline(context, data, trace_id, raw),
                    timeout=self.pipeline_timeout_minutes * 60,
                )
            except asyncio.TimeoutError:
                elapsed = int((time.monotonic() - started) * 1000)
                logger.error(
                    "[盘前流水线][%s] 整体超时(>%s 分钟),产出降级报告",
                    trace_id,
                    self.pipeline_timeout_minutes,
                )
                raw["stage_errors"].append(
                    {"stage": "pipeline", "error": f"整体超时 {self.pipeline_timeout_minutes}m"}
                )
                content = report_mod.render_report(
                    snapshot_date=data.get("today") or stages._today_str(),
                    cards=MacroCards(status="snapshot_only", gate_reason="整体超时,仅保留数据快照。"),
                    forecast=SectorForecast(),
                    candidates=[],
                    declarations=[{"item": "pipeline", "source": "timeout", "as_of": stages._now_iso(), "caliber": "", "degrade_level": 3}],
                    events_future=[],
                    global_rows=[],
                    us_rows=[],
                    stage_errors=raw["stage_errors"],
                )
                result = AnalysisResult(
                    agent_name=self.name,
                    title=f"【{self.display_name}】超时降级报告",
                    content=content,
                    notify_content=f"盘前流水线超时(>{self.pipeline_timeout_minutes} 分钟),已产出降级报告。",
                    raw_data=raw,
                )
                self._finish_run_marker(
                    trace_id, status="failed",
                    error=f"整体超时 {self.pipeline_timeout_minutes}m",
                    duration_ms=elapsed,
                )
            except Exception as e:
                self._finish_run_marker(
                    trace_id, status="failed", error=f"{type(e).__name__}: {e}",
                    duration_ms=int((time.monotonic() - started) * 1000),
                )
                raise
            else:
                self._finish_run_marker(
                    trace_id, status="success",
                    result=(result.content or "")[:2000],
                    duration_ms=int((time.monotonic() - started) * 1000),
                )
            raw["elapsed_ms"] = int((time.monotonic() - started) * 1000)
            return result

    # ─────────────────── 五阶段管线 ───────────────────

    async def _run_pipeline(
        self, context: AgentContext, data: dict, trace_id: str, raw: dict
    ) -> AnalysisResult:
        from src.platform.persistence.database import SessionLocal

        chat_fn = self._make_chat_fn(context)
        cards = MacroCards(status="snapshot_only", gate_reason="阶段1未运行。")
        forecast = SectorForecast()
        candidates: list[PickCandidate] = []
        declarations: list[dict] = []
        events_future: list[dict] = []
        global_rows: list[dict] = []
        us_rows: list[dict] = []

        async def run_stage(name: str, coro_factory):
            t0 = time.monotonic()
            try:
                value = await coro_factory()
                elapsed = int((time.monotonic() - t0) * 1000)
                raw["stages"].append(
                    {
                        "stage": name,
                        "elapsed_ms": elapsed,
                        "sources": _stage_sources(name, value),
                    }
                )
                self._emit_progress(trace_id, name, "done", elapsed)
                return value
            except Exception as e:
                elapsed = int((time.monotonic() - t0) * 1000)
                logger.error("[盘前流水线][%s] 阶段 %s 失败(降级): %s", trace_id, name, e)
                raw["stage_errors"].append({"stage": name, "error": f"{type(e).__name__}: {e}"})
                raw["stages"].append(
                    {"stage": name, "elapsed_ms": elapsed, "sources": [{"name": name, "ok": False, "degrade_level": 3}]}
                )
                return None

        # 阶段1 输入已在 collect 完成;此处跑宏观三卡 + 板块 + 选股 + 核验 + 写库
        pipeline_deadline = time.monotonic() + self.pipeline_timeout_minutes * 60
        channels = data.get("channels") or {}
        macro: MacroCards | None = await run_stage(
            "macro",
            lambda: stages.run_macro_stage(
                channels, chat_fn, llm_timeout_seconds=self.llm_timeout_seconds
            ),
        )
        if macro is not None:
            cards = macro

        if cards.status == "stopped":
            # STOP 硬停:产降级报告+导入指引,不进后续阶段
            content = report_mod.render_report(
                snapshot_date=channels.get("today") or stages._today_str(),
                cards=cards,
                forecast=forecast,
                candidates=candidates,
                declarations=[{
                    "item": "事件日历",
                    "source": "event_calendar_items",
                    "as_of": stages._now_iso(),
                    "caliber": "近7日+未来5交易日窗口",
                    "degrade_level": 3,
                    "note": cards.gate_reason,
                }],
                events_future=[],
                global_rows=channels.get("global", {}).get("data") or [],
                us_rows=channels.get("us_indices", {}).get("data") or [],
                stage_errors=raw["stage_errors"],
            )
            return AnalysisResult(
                agent_name=self.name,
                title=f"【{self.display_name}】已硬停(STOP)",
                content=content,
                notify_content=report_mod.render_notify(
                    snapshot_date=channels.get("today") or stages._today_str(),
                    cards=cards, forecast=forecast, candidates=[], cards_stopped=True,
                ),
                raw_data=raw,
            )

        snapshot_date = channels.get("today") or stages._today_str()
        policy_events = channels.get("calendar", {}).get("data") or []

        async def _sector_stage():
            db = SessionLocal()
            try:
                fc = await stages.run_sector_stage(
                    db,
                    snapshot_date=snapshot_date,
                    chat_fn=chat_fn,
                    llm_timeout_seconds=self.llm_timeout_seconds,
                    top_n=self.board_top_n,
                    policy_events=policy_events,
                )
                await asyncio.to_thread(
                    stages.upsert_sector_predictions, db, fc, snapshot_date
                )
                return fc
            finally:
                db.close()

        sector_result = await run_stage("sector", _sector_stage)
        if sector_result is not None:
            forecast = sector_result

        async def _pick_stage():
            db = SessionLocal()
            try:
                # 财务按需拉取预算 = 流水线剩余时间 - 核验/写库预留
                budget = max(
                    60.0,
                    pipeline_deadline
                    - time.monotonic()
                    - pcfg.FINANCE_BUDGET_RESERVE_SEC * 2,
                )
                return await asyncio.to_thread(
                    stages.pick_candidates,
                    db,
                    [it for it in forecast.items],
                    policy_events,
                    max_candidates=self.max_candidates,
                    budget_seconds=budget,
                )
            finally:
                db.close()

        pick_result = await run_stage("pick", _pick_stage)
        if pick_result is not None:
            candidates, _pick_sources = pick_result
            await run_stage(
                "pick_review",
                lambda: stages.run_pick_llm_review(
                    candidates, chat_fn, llm_timeout_seconds=self.llm_timeout_seconds
                ),
            )

        # 阶段4 财报核验(纯 Python fail-closed;含逐候选三路 akshare 抓取,须离 loop)
        disclosure_events = list(
            (channels.get("calendar", {}) or {}).get("data") or []
        ) + list(channels.get("events_recent") or [])

        async def _verify_stage():
            db = SessionLocal()
            try:
                for c in candidates:
                    if not c.v11_pass:
                        continue
                    # 摘要优先用阶段3已拉取的(避免二次 IO);无则回退缓存查询
                    summary = c.fundamentals or await asyncio.to_thread(
                        self._load_summary_for_verify, db, c
                    )
                    # 双源抓取是同步 akshare HTTP → to_thread,防阻塞事件循环
                    ver = await asyncio.to_thread(
                        verifier.verify_candidate,
                        db,
                        symbol=c.symbol,
                        summary=summary,
                        events=disclosure_events,
                    )
                    c.verified = ver.passed
                    c.verification = {
                        "passed": ver.passed,
                        "checks": {
                            k: {"status": v.get("status"), "detail": v.get("detail")}
                            for k, v in ver.checks.items()
                        },
                        "warnings": ver.warnings,
                    }
                    if ver.warnings:
                        c.confidence = max(1.0, (c.confidence or 5.0) - 1.0)
                return candidates
            finally:
                db.close()

        await run_stage("verify", _verify_stage)

        # 阶段5 落库推送
        write_stats: dict = {}
        result: AnalysisResult | None = None

        async def _write_stage():
            nonlocal result
            db = SessionLocal()
            try:
                events_future = _events_future_slice(channels)
                global_rows = channels.get("global", {}).get("data") or []
                us_rows = channels.get("us_indices", {}).get("data") or []
                declarations = _collect_declarations(channels, forecast, candidates)
                report_md = report_mod.render_report(
                    snapshot_date=snapshot_date,
                    cards=cards,
                    forecast=forecast,
                    candidates=candidates,
                    declarations=declarations,
                    events_future=events_future,
                    global_rows=global_rows,
                    us_rows=us_rows,
                    stage_errors=raw["stage_errors"],
                )
                ws = writer.build_write_set(
                    snapshot_date=snapshot_date,
                    cards=cards,
                    forecast=forecast,
                    candidates=candidates,
                    report_md=report_md,
                    notify_content=report_mod.render_notify(
                        snapshot_date=snapshot_date,
                        cards=cards, forecast=forecast, candidates=candidates,
                    ),
                    declarations=declarations,
                    stage_errors=raw["stage_errors"],
                )
                # 删除+批量写入是同步 DB 段 → to_thread,不阻塞事件循环
                stats = await asyncio.to_thread(
                    writer.execute_write_set,
                    db, ws,
                    auto_create_alerts=self.auto_create_alerts,
                )
                write_stats.update(stats)
                for outcome in ws.prediction_outcomes:
                    from src.modules.research.context_store import save_agent_prediction_outcome

                    save_agent_prediction_outcome(
                        agent_name=self.name,
                        **{k: v for k, v in outcome.items()},
                    )
                if ws.alert_errors:
                    raw["stage_errors"].append({"stage": "alerts", "error": ";".join(ws.alert_errors)})

                from src.modules.research.analysis_history import save_analysis

                save_analysis(
                    agent_name=self.name,
                    stock_symbol="*",
                    content=report_md,
                    title=ws.title,
                    raw_data={
                        "stages": raw["stages"],
                        "stage_errors": raw["stage_errors"],
                        "write_stats": write_stats,
                        "macro_cards": cards.cards,
                        "macro_status": cards.status,
                        "sector_items": [
                            {
                                "board_code": it.board_code,
                                "board_name": it.board_name,
                                "direction": it.direction,
                                "confidence": it.confidence,
                                "stage": it.stage,
                                "source": it.source,
                            }
                            for it in forecast.items
                        ],
                        "candidates": [_candidate_summary(c) for c in candidates],
                        "declarations": declarations,
                    },
                )
                result = AnalysisResult(
                    agent_name=self.name,
                    title=ws.title,
                    content=report_md,
                    notify_content=ws.notify_content,
                    raw_data=raw,
                )
                return ws
            finally:
                db.close()

        await run_stage("write", _write_stage)

        if result is None:  # write 阶段失败也要返回报告内容
            content = report_mod.render_report(
                snapshot_date=snapshot_date,
                cards=cards,
                forecast=forecast,
                candidates=candidates,
                declarations=_collect_declarations(channels, forecast, candidates),
                events_future=_events_future_slice(channels),
                global_rows=channels.get("global", {}).get("data") or [],
                us_rows=channels.get("us_indices", {}).get("data") or [],
                stage_errors=raw["stage_errors"],
            )
            result = AnalysisResult(
                agent_name=self.name,
                title=f"【{self.display_name}】{snapshot_date}(落库失败降级)",
                content=content,
                raw_data=raw,
            )

        if context.model_label:
            result.content = result.content.rstrip() + f"\n\n---\nAI: {context.model_label}"
        raw["write_stats"] = write_stats
        raw["should_alert"] = bool([c for c in candidates if c.v11_pass and c.verified])
        raw["notified"] = False  # 由 BaseAgent.run 通知流程覆写
        return result

    # ─────────────────── 私有辅助 ───────────────────

    def _collect_channels(self, trace_id: str) -> dict:
        from src.platform.persistence.database import SessionLocal

        db = SessionLocal()
        try:
            channels = stages.collect_channels(db)
            logger.info(
                "[盘前流水线][%s] 采集完成: %s",
                trace_id,
                {k: (channels[k]["available"] if isinstance(channels.get(k), dict) else channels[k])
                 for k in ("calendar", "macro", "global", "us_indices")},
            )
            return {
                "skip": False,
                "today": channels.get("today"),
                "channels": channels,
                "trace_id": trace_id,
            }
        finally:
            db.close()

    def _make_chat_fn(self, context: AgentContext):
        async def chat(system_prompt: str, user_content: str) -> str:
            return await context.ai_client.chat(system_prompt, user_content)

        return chat

    def _trace_id(self, context: AgentContext) -> str:
        existing = getattr(context, "_trace_id", "")
        if isinstance(existing, str) and existing:
            return existing
        return f"pm-{int(datetime.now().timestamp())}"

    def _find_running_run(self, exclude_trace: str | None = None) -> str | None:
        """双跑互斥:同 agent 仍在 TTL 内的 running 生命周期记录(排除自身 trace)。"""
        from src.modules.automation.agent_runs import ACTIVE_RUN_TTL_SEC, _as_utc
        from src.platform.persistence.database import SessionLocal
        from src.platform.persistence.models import AgentRun
        from src.platform.scheduling.timezone import utc_now

        db = SessionLocal()
        try:
            query = db.query(AgentRun).filter(
                AgentRun.agent_name == self.name, AgentRun.status == "running"
            )
            if exclude_trace:
                query = query.filter(AgentRun.trace_id != exclude_trace)
            row = query.order_by(AgentRun.created_at.desc(), AgentRun.id.desc()).first()
            if not row:
                return None
            created = _as_utc(row.created_at)
            now = utc_now()
            if created is None or (now - created).total_seconds() > ACTIVE_RUN_TTL_SEC:
                return None
            return row.trace_id or str(row.id)
        except Exception as e:
            logger.warning("[盘前流水线] 双跑互斥检查失败(放行): %s", e)
            return None
        finally:
            db.close()

    def _start_run_marker(self, trace_id: str) -> None:
        """插入 running 生命周期行(start_agent_run 按 trace 幂等),支撑互斥与进度恢复。"""
        try:
            from src.modules.automation.agent_runs import start_agent_run

            start_agent_run(
                agent_name=self.name, trace_id=trace_id, trigger_source="pipeline"
            )
        except Exception as e:
            logger.warning("[盘前流水线][%s] 写 running 标记失败(不阻断): %s", trace_id, e)

    def _finish_run_marker(
        self,
        trace_id: str,
        *,
        status: str,
        result: str = "",
        error: str = "",
        duration_ms: int = 0,
    ) -> None:
        """把自管的 running 行落终态(record_agent_run 按 trace 幂等更新)。"""
        try:
            from src.modules.automation.agent_runs import record_agent_run

            record_agent_run(
                agent_name=self.name,
                status=status,
                result=result,
                error=error,
                duration_ms=duration_ms,
                trace_id=trace_id,
                trigger_source="pipeline",
            )
        except Exception as e:
            logger.warning("[盘前流水线][%s] 写运行终态失败(不阻断): %s", trace_id, e)

    def _load_summary_for_verify(self, db, candidate: PickCandidate):
        """核验用的最新财务摘要(缓存优先,不触发按需拉取:预算已在阶段3管控)。"""
        from src.platform.persistence.models import FundamentalsCache

        row = (
            db.query(FundamentalsCache)
            .filter(FundamentalsCache.symbol == candidate.symbol)
            .order_by(FundamentalsCache.report_period.desc(), FundamentalsCache.id.desc())
            .first()
        )
        return row.summary if row and isinstance(row.summary, dict) else None

    def _emit_progress(self, trace_id: str, stage: str, status: str, elapsed_ms: int) -> None:
        logger.info(
            "[pm_progress] stage=%s status=%s elapsed_ms=%s",
            stage, status, elapsed_ms,
        )


def _stage_sources(name: str, value: Any) -> list[dict]:
    if value is None:
        return [{"name": name, "ok": False, "degrade_level": 3}]
    if isinstance(value, MacroCards):
        return value.sources or [{"name": "macro", "ok": True, "degrade_level": 0}]
    if isinstance(value, SectorForecast):
        return value.sources or [{"name": "sector", "ok": True, "degrade_level": 0}]
    if isinstance(value, tuple) and value and isinstance(value[0], list):
        # pick_candidates 返回 (candidates, sources)
        return value[1] if len(value) > 1 else []
    if hasattr(value, "sources") and isinstance(value.sources, list):
        return value.sources
    return [{"name": name, "ok": True, "degrade_level": 0}]


def _events_future_slice(channels: dict) -> list[dict]:
    """事件对表:未来 5 个交易日窗口(来自日历通道)。"""
    from src.modules.automation.premarket_pipeline.stages import next_trading_days

    future = next_trading_days(pcfg.CALENDAR_FUTURE_TRADING_DAYS)
    if not future:
        return []
    start = datetime.now().date().isoformat()
    end = future[-1].isoformat()
    items = (channels.get("calendar", {}) or {}).get("data") or []
    return [ev for ev in items if start <= str(ev.get("event_date") or "") <= end]


def _collect_declarations(
    channels: dict, forecast: SectorForecast, candidates: list[PickCandidate]
) -> list[dict]:
    """数据声明:逐项 source + as_of + 口径 + degrade_level(缺项也声明)。"""
    decls: list[dict] = []
    name_map = {
        "calendar": "事件日历",
        "macro": "宏观指标",
        "global": "全球指数",
        "us_indices": "隔夜美股",
    }
    for key, label in name_map.items():
        ch = channels.get(key) or {}
        decls.append(
            {
                "item": label,
                "source": "db:event_calendar_items" if key == "calendar"
                else "db:macro_indicator_values" if key == "macro"
                else "marketdata.global_markets" if key == "global"
                else "marketdata.index_quotes(tencent)",
                "as_of": ch.get("as_of") or "",
                "caliber": "近7日+未来5交易日窗口" if key == "calendar"
                else "库内最新期" if key == "macro"
                else "实时快照" if key == "global"
                else "T-1 美东收盘",
                "degrade_level": 0 if ch.get("available") else 3,
                "note": "" if ch.get("available") else "通道不可用",
            }
        )
    for src in forecast.sources or []:
        decls.append(
            {
                "item": "板块快照/动量",
                "source": src.get("name") or "",
                "as_of": stages._now_iso(),
                "caliber": "快照库/实时/合成",
                "degrade_level": src.get("degrade_level", 0),
                "note": "",
            }
        )
    for c in candidates:
        if c.valuation:
            decls.append(
                {
                    "item": f"估值入场区 {c.symbol}",
                    "source": c.valuation.get("source") or "",
                    "as_of": stages._now_iso(),
                    "caliber": f"PE p30/p50({c.valuation.get('obs')} 点)" if c.valuation.get("obs") else "技术面支撑",
                    "degrade_level": c.valuation.get("degrade_level", 0),
                    "note": c.valuation.get("reason") or "",
                }
            )
        if c.budget_exhausted:
            decls.append(
                {
                    "item": f"财务摘要 {c.symbol}",
                    "source": "akshare(按需)",
                    "as_of": stages._now_iso(),
                    "caliber": "",
                    "degrade_level": 3,
                    "note": "budget_exhausted:财务拉取预算不足,维度缺失",
                }
            )
    return decls


def _candidate_summary(c: PickCandidate) -> dict:
    return {
        "symbol": c.symbol,
        "name": c.stock_name,
        "board": c.board_name,
        "entry": [c.entry_low, c.entry_high],
        "stop": c.stop_loss,
        "target": c.target_price,
        "rr": c.rr,
        "grade": c.grade,
        "leader_score": c.leader_score,
        "verified": c.verified,
        "v11_pass": c.v11_pass,
        "action": c.action,
        "llm_ok": c.llm_ok,
    }
