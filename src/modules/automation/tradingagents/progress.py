"""TradingAgents 进度回调。

走一个统一回调链:
1. LangChain `BaseCallbackHandler`:捕获 LangGraph 节点、LLM 和工具的真实生命周期
2. `agent.py` 将同一个 handler 注入 `Propagator.get_graph_args(callbacks=...)`，不依赖 debug 文本解析

进度写入 PanWatch 的 `log_context`,前端轮询 `/api/agents/runs/{trace_id}/progress`
聚合返回阶段。
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

from src.platform.observability import otel
from src.platform.observability.log_context import log_context

logger = logging.getLogger(__name__)


# 默认阶段映射:TradingAgents 4 个 analyst + 辩论 + 风控 + PM
STAGES_ORDER = [
    "data_collection",
    "market_analyst",
    "social_analyst",
    "news_analyst",
    "fundamentals_analyst",
    "bull_bear_debate",
    "research_manager",
    "trader",
    "risk_judge",
    "final_decision",
]

# TradingAgents 0.5.0 的 LangGraph 节点名不是界面阶段名的一一映射。
# 这里集中维护别名，而不是在每个 callback 分支里散落字符串判断；上游节点改名时只需改这一张表。
NODE_STAGE_ALIASES = {
    "market_analyst": "market_analyst",
    "sentiment_analyst": "social_analyst",
    "social_analyst": "social_analyst",
    "news_analyst": "news_analyst",
    "fundamentals_analyst": "fundamentals_analyst",
    "bull_researcher": "bull_bear_debate",
    "bear_researcher": "bull_bear_debate",
    "research_manager": "research_manager",
    "trader": "trader",
    "aggressive_analyst": "risk_judge",
    "conservative_analyst": "risk_judge",
    "neutral_analyst": "risk_judge",
    "risk_judge": "risk_judge",
    "portfolio_manager": "final_decision",
    "final_decision": "final_decision",
}


try:
    from langchain_core.callbacks import BaseCallbackHandler as _LCBaseCallbackHandler
    _LANGCHAIN_AVAILABLE = True
except ImportError:  # tradingagents 未装时仍允许 import 本模块,测试不依赖
    _LANGCHAIN_AVAILABLE = False

    class _LCBaseCallbackHandler:  # type: ignore[no-redef]
        """Fallback stub when langchain_core 未安装。"""
        pass


class PanWatchProgressHandler(_LCBaseCallbackHandler):
    """LangChain BaseCallbackHandler 兼容的进度处理器。

    新版 langchain (1.x) 把 callbacks 字段用 pydantic 校验为 BaseCallbackHandler 实例,
    所以必须继承上游基类才能被接受。

    覆盖核心 hook:
    - on_llm_start: 某个 LLM 调用开始(可推断当前在哪个 analyst)
    - on_llm_end: LLM 调用结束,带成本
    - on_chain_start/end: LangGraph 节点切换

    P0 简单实现:把所有事件都 logger.info 出来,带 trace_id 标签。
    前端通过过滤 log_entries 表的 trace_id + event=ta_progress 拿到时间线。
    """

    def __init__(
        self,
        trace_id: str,
        agent_name: str = "tradingagents",
        cancel_event: threading.Event | None = None,
    ):
        # langchain_core BaseCallbackHandler 没有 __init__ 参数,直接 super 安全
        try:
            super().__init__()
        except TypeError:
            # 某些版本要求无参,某些要求带参,兜底
            pass
        self.trace_id = trace_id
        self.agent_name = agent_name
        self.cancel_event = cancel_event
        self._started_at = time.monotonic()
        self._total_cost = 0.0
        self._completed_stages: set[str] = set()
        # LangChain 1.x 的 on_chain_end 不保证携带 name/metadata，因此必须保存
        # start 时的 run_id -> 节点信息，才能把结束事件关回正确阶段。
        self._chain_runs: dict[str, dict[str, str]] = {}
        self._llm_runs: dict[str, dict[str, str]] = {}
        self._tool_runs: dict[str, dict[str, str]] = {}
        # OTel 桥接:handler 在异步侧构造(to_thread 之前),此处捕获当前上下文,
        # 供工作线程里的 callback 把节点/LLM 子 span 挂到 root span 下(关闭时为 None)。
        self._otel_parent = otel.capture_context()
        self._otel_stage_spans: dict[str, Any] = {}
        self._otel_llm_span: Any = None

    @property
    def elapsed_sec(self) -> float:
        return time.monotonic() - self._started_at

    def _emit(self, stage: str, action: str, **extra):
        """写一条进度日志。前端按 trace_id + event=ta_progress 拉。"""
        if self.cancel_event is not None and self.cancel_event.is_set():
            return
        with log_context(
            trace_id=self.trace_id,
            agent_name=self.agent_name,
            event="ta_progress",
            tags={
                "stage": stage,
                "action": action,
                "elapsed_sec": round(self.elapsed_sec, 2),
                "total_cost_usd": round(self._total_cost, 6),
                **extra,
            },
        ):
            agent = extra.get("agent") or extra.get("langgraph_node") or ""
            detail = f" agent={agent}" if agent else ""
            logger.info(f"[TA进度] stage={stage} action={action}{detail} {extra}")

    def emit(self, stage: str, action: str, **extra) -> None:
        """向采集等非 LangChain 阶段发出同一格式的进度事件。"""
        self._emit(stage, action, **extra)

    # ---- LangChain callbacks 接口 ----

    # 关键:LLM 默认按 token 估算成本(deepseek-chat 单价),后续可由调用方注入更精确单价
    _PRICE_PER_M_PROMPT = 0.14
    _PRICE_PER_M_COMPLETION = 0.28

    def on_llm_start(self, serialized, prompts, **kwargs):
        self._llm_call_count = getattr(self, "_llm_call_count", 0) + 1
        model = ""
        try:
            model = (
                (kwargs.get("invocation_params") or {}).get("model")
                or (serialized or {}).get("name")
                or ""
            )
        except Exception:
            model = ""
        agent = _callback_agent(kwargs, self._chain_runs)
        operation_id = str(kwargs.get("run_id") or f"llm:{self._llm_call_count}")
        self._llm_runs[operation_id] = {"agent": agent, "model": model}
        self._emit(
            "llm_call",
            "llm_start",
            call_n=self._llm_call_count,
            model=model,
            operation_id=operation_id,
            **({"agent": agent, "langgraph_node": agent} if agent else {}),
        )
        # OTel:TA 的一次 LLM 调用 -> gen_ai 子 span(遵循 GenAI 语义约定)。
        self._otel_llm_span = otel.start_detached_span(
            f"chat {model}".strip() if model else "chat",
            parent_context=self._otel_parent,
            attributes={
                otel.GEN_AI_SYSTEM: "tradingagents",
                otel.GEN_AI_OPERATION_NAME: "chat",
                **({otel.GEN_AI_REQUEST_MODEL: model} if model else {}),
            },
        )

    def on_llm_end(self, response, **kwargs):
        # langchain LLMResult.llm_output 含 token_usage
        usage = {}
        try:
            usage = (response.llm_output or {}).get("token_usage") or {}
        except Exception:
            pass
        prompt_tokens = usage.get("prompt_tokens") or 0
        completion_tokens = usage.get("completion_tokens") or 0
        # 累加成本估算
        cost = (
            prompt_tokens / 1_000_000 * self._PRICE_PER_M_PROMPT
            + completion_tokens / 1_000_000 * self._PRICE_PER_M_COMPLETION
        )
        self.record_cost(cost)
        operation_id = str(kwargs.get("run_id") or "")
        operation = self._llm_runs.pop(operation_id, {})
        agent = operation.get("agent") or _callback_agent(kwargs, self._chain_runs)
        self._emit(
            "llm_call",
            "llm_end",
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            call_cost=round(cost, 6),
            operation_id=operation_id,
            **({"agent": agent, "langgraph_node": agent} if agent else {}),
        )
        # OTel:回填 token 用量并结束 gen_ai span。
        if self._otel_llm_span is not None:
            otel.set_span_attributes(
                self._otel_llm_span,
                {
                    otel.GEN_AI_USAGE_INPUT_TOKENS: int(prompt_tokens),
                    otel.GEN_AI_USAGE_OUTPUT_TOKENS: int(completion_tokens),
                },
            )
            otel.end_span(self._otel_llm_span)
            self._otel_llm_span = None

    def on_chain_start(self, serialized, inputs, **kwargs):
        # LangGraph 节点切换。节点名优先取 kwargs.name/metadata.langgraph_node，
        # 因为 serialized 在不同 LangChain 版本里可能只有 runnable 类型名称。
        name = _callback_name(serialized, kwargs)
        stage = _normalize_stage(name)
        if not stage:
            return
        run_id = _run_id(kwargs)
        if run_id:
            self._chain_runs[run_id] = {
                "name": name,
                "stage": stage,
                "parent_run_id": _parent_run_id(kwargs),
            }
        self._emit(
            stage,
            "stage_start",
            langgraph_node=name,
            run_id=run_id,
            parent_run_id=_parent_run_id(kwargs),
        )
        # OTel 节点 span 只保留一个当前阶段，重复的并行/重试节点仍会产生进度事件，
        # 但不会因为重复 span 让追踪树无限膨胀。
        if stage not in self._otel_stage_spans:
            span = otel.start_detached_span(
                f"tradingagents.stage {stage}",
                parent_context=self._otel_parent,
                attributes={
                    otel.ATTR_TA_STAGE: stage,
                    otel.ATTR_AGENT_NAME: self.agent_name,
                },
            )
            if span is not None:
                self._otel_stage_spans[stage] = span

    def on_chain_end(self, outputs, **kwargs):
        self._finish_chain("stage_end", kwargs)

    def on_llm_error(self, error, **kwargs):
        self._emit(
            "llm_call",
            "llm_error",
            error=str(error)[:200],
            operation_id=str(kwargs.get("run_id") or ""),
        )
        self._emit("error", "llm_error", error=str(error)[:200])

    def on_chain_error(self, error, **kwargs):
        self._finish_chain("stage_error", kwargs, error=str(error)[:200])
        self._emit("error", "chain_error", error=str(error)[:200], run_id=_run_id(kwargs))

    def on_tool_start(self, serialized, input_str, **kwargs):
        """记录 LangGraph ToolNode 当前正在执行的工具。"""
        name = ""
        try:
            name = kwargs.get("name") or (serialized or {}).get("name") or "unknown"
        except Exception:
            name = kwargs.get("name") or "unknown"
        operation_id = str(kwargs.get("run_id") or f"tool:{name}")
        agent = _callback_agent(kwargs, self._chain_runs)
        self._tool_runs[operation_id] = {"agent": agent, "tool": str(name)}
        self._emit(
            "llm_call",
            "tool_start",
            tool=str(name),
            operation_id=operation_id,
            **({"agent": agent, "langgraph_node": agent} if agent else {}),
        )

    def on_tool_end(self, output, **kwargs):
        operation_id = str(kwargs.get("run_id") or "")
        operation = self._tool_runs.pop(operation_id, {})
        name = kwargs.get("name") or kwargs.get("tool_name") or operation.get("tool") or "unknown"
        agent = operation.get("agent") or _callback_agent(kwargs, self._chain_runs)
        self._emit(
            "llm_call",
            "tool_end",
            tool=str(name),
            operation_id=operation_id,
            **({"agent": agent, "langgraph_node": agent} if agent else {}),
        )

    def on_tool_error(self, error, **kwargs):
        operation_id = str(kwargs.get("run_id") or "")
        operation = self._tool_runs.pop(operation_id, {})
        name = kwargs.get("name") or kwargs.get("tool_name") or operation.get("tool") or "unknown"
        agent = operation.get("agent") or _callback_agent(kwargs, self._chain_runs)
        self._emit(
            "llm_call",
            "tool_error",
            tool=str(name),
            error=str(error)[:200],
            operation_id=operation_id,
            **({"agent": agent, "langgraph_node": agent} if agent else {}),
        )

    # ---- 公共方法 ----

    def record_cost(self, usd: float) -> None:
        self._total_cost += usd

    def _guess_stage(self, serialized: dict, kwargs: dict) -> str:
        name = _callback_name(serialized, kwargs) or "unknown"
        return _normalize_stage(name) or "unknown"

    def _finish_chain(self, action: str, kwargs: dict, **extra: Any) -> None:
        """按 run_id 找回节点并发出结束事件；上游未携带节点名时也能正确闭环。"""
        run_id = _run_id(kwargs)
        record = self._chain_runs.pop(run_id, None) if run_id else None
        name = (record or {}).get("name") or _callback_name(None, kwargs)
        stage = (record or {}).get("stage") or _normalize_stage(name)
        if not stage:
            return
        self._completed_stages.add(stage)
        self._emit(
            stage,
            action,
            langgraph_node=name,
            run_id=run_id,
            parent_run_id=(record or {}).get("parent_run_id") or _parent_run_id(kwargs),
            **extra,
        )
        if action in {"stage_end", "stage_error"}:
            span = self._otel_stage_spans.pop(stage, None)
            if span is not None:
                otel.end_span(span)


def _normalize_stage(name: str) -> str:
    """把 LangGraph 节点名标准化到 STAGES_ORDER 里的一个值。"""
    n = "_".join(str(name or "").strip().lower().replace("-", " ").split())
    if not n:
        return ""
    if n in NODE_STAGE_ALIASES:
        return NODE_STAGE_ALIASES[n]
    for stage in STAGES_ORDER:
        if stage in n:
            return stage
    return ""


def _callback_name(serialized: Any, kwargs: dict[str, Any]) -> str:
    """兼容 LangChain callback 的 name/metadata/serialized 三种节点来源。"""
    metadata = kwargs.get("metadata") or {}
    return str(
        kwargs.get("name")
        or metadata.get("langgraph_node")
        or (serialized or {}).get("name", "")
        or ""
    ).strip()


def _run_id(kwargs: dict[str, Any]) -> str:
    return str(kwargs.get("run_id") or "")


def _parent_run_id(kwargs: dict[str, Any]) -> str:
    return str(kwargs.get("parent_run_id") or "")


def _callback_agent(kwargs: dict[str, Any], chain_runs: dict[str, dict[str, str]]) -> str:
    metadata = kwargs.get("metadata") or {}
    agent = str(metadata.get("langgraph_node") or kwargs.get("name") or "").strip()
    if agent:
        return agent
    parent = chain_runs.get(_parent_run_id(kwargs))
    return str((parent or {}).get("name") or "")


def aggregate_progress(log_entries: list[dict]) -> dict:
    """读 log_entries 表里 event=ta_progress 的记录,聚合成阶段进度。

    log_entries 行结构(参考 src/web/log_handler.py):
    {timestamp, level, logger_name, message, trace_id, agent_name, event, tags, ...}
    tags 是 dict,含 stage / action / elapsed_sec / total_cost_usd 等。

    返回结构(给前端):
    {
        "current_stage": "bull_bear_debate",
        "completed_stages": [...],
        "started_at": ...,
        "elapsed_sec": 123.4,
        "total_cost_usd": 0.018,
        "stages": [
            {"name": "market_analyst", "status": "done", "duration_sec": 12.3, "cost_usd": 0.004},
            ...
        ]
    }
    """
    stage_state: dict[str, dict] = {s: {"name": s, "status": "pending"} for s in STAGES_ORDER}
    total_cost = 0.0
    current_stage = None
    active_operations: dict[str, dict] = {}
    started_at = None
    collection_sources: dict[str, dict] = {}

    for entry in log_entries:
        tags = entry.get("tags") or {}
        stage = tags.get("stage")
        action = tags.get("action") or ""
        source = tags.get("source")
        ts = entry.get("timestamp")
        if started_at is None and ts:
            started_at = ts

        if not stage or stage not in stage_state:
            # LLM/工具事件不属于独立阶段，但需要保留当前活动操作，
            # 这样外部数据请求卡住时 UI 能显示具体工具名。
            if stage == "llm_call":
                kind = "tool" if action.startswith("tool_") else "llm"
                name = tags.get("tool") if kind == "tool" else tags.get("model")
                operation_id = str(tags.get("operation_id") or f"{kind}:{name or action}")
                agent = str(tags.get("agent") or tags.get("langgraph_node") or "")
                if action == "llm_start":
                    operation = {"kind": "llm", "name": tags.get("model") or "LLM 调用"}
                    if agent:
                        operation["agent"] = agent
                    active_operations[operation_id] = operation
                elif action == "tool_start":
                    operation = {"kind": "tool", "name": tags.get("tool") or "工具调用"}
                    if agent:
                        operation["agent"] = agent
                    active_operations[operation_id] = operation
                elif action in {"llm_end", "tool_end", "llm_error", "tool_error"}:
                    if tags.get("operation_id"):
                        active_operations.pop(operation_id, None)
                    else:
                        # 兼容旧日志/上游未传 run_id 的回调：只移除同类型同名称
                        # 的一个操作，不影响并行执行的其它工具。
                        expected_name = name or ("工具调用" if kind == "tool" else "LLM 调用")
                        for key, operation in list(active_operations.items()):
                            if operation["kind"] == kind and operation["name"] == expected_name:
                                active_operations.pop(key, None)
                                break
            continue

        if stage == "data_collection" and source:
            source_state = collection_sources.setdefault(
                source,
                {"name": source, "status": "pending"},
            )
            if action == "source_start":
                source_state["status"] = "running"
            elif action == "source_end":
                source_state["status"] = "done"
            elif action == "source_error":
                source_state["status"] = "error"
                if tags.get("error"):
                    source_state["error"] = str(tags["error"])[:200]

        # cost 累积取最后一条的 total_cost_usd
        cost = tags.get("total_cost_usd")
        if cost is not None:
            total_cost = max(total_cost, float(cost))

        if action == "stage_start":
            stage_state[stage]["status"] = "running"
            stage_state[stage]["started_at"] = ts
            current_stage = stage
        elif action == "stage_end":
            stage_state[stage]["status"] = "done"
            if "started_at" in stage_state[stage] and ts:
                # 简略时长(实际 ts 是 datetime,这里依赖调用方转换)
                pass

    return {
        "current_stage": current_stage,
        "completed_stages": [s for s, v in stage_state.items() if v["status"] == "done"],
        "started_at": started_at,
        "elapsed_sec": float(log_entries[-1].get("tags", {}).get("elapsed_sec", 0))
        if log_entries
        else 0,
        "total_cost_usd": round(total_cost, 6),
        "active_operation": next(reversed(active_operations.values()), None)
        if active_operations
        else None,
        "stages": [stage_state[s] for s in STAGES_ORDER],
        "data_sources": list(collection_sources.values()),
    }
