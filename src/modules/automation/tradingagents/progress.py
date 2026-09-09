"""TradingAgents 进度回调。

走两个机制:
1. LangChain `BaseCallbackHandler`:LLM 每次调用前后的 hook
2. LangGraph 节点切换:通过 debug=True 流式输出捕获(可选)

进度写入 PanWatch 的 `log_context`,前端轮询 `/api/agents/runs/{trace_id}/progress`
聚合返回阶段。
"""

from __future__ import annotations

import logging
import time
from typing import Any

from src.core.log_context import log_context
from src.core import otel

logger = logging.getLogger(__name__)


# 默认阶段映射:TradingAgents 4 个 analyst + 辩论 + 风控 + PM
STAGES_ORDER = [
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

    def __init__(self, trace_id: str, agent_name: str = "tradingagents"):
        # langchain_core BaseCallbackHandler 没有 __init__ 参数,直接 super 安全
        try:
            super().__init__()
        except TypeError:
            # 某些版本要求无参,某些要求带参,兜底
            pass
        self.trace_id = trace_id
        self.agent_name = agent_name
        self._started_at = time.monotonic()
        self._total_cost = 0.0
        self._completed_stages: set[str] = set()
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
            logger.info(f"[TA进度] stage={stage} action={action} {extra}")

    # ---- LangChain callbacks 接口 ----

    # 关键:LLM 默认按 token 估算成本(deepseek-chat 单价),后续可由调用方注入更精确单价
    _PRICE_PER_M_PROMPT = 0.14
    _PRICE_PER_M_COMPLETION = 0.28

    def on_llm_start(self, serialized, prompts, **kwargs):
        self._llm_call_count = getattr(self, "_llm_call_count", 0) + 1
        self._emit("llm_call", "llm_start", call_n=self._llm_call_count)
        # OTel:TA 的一次 LLM 调用 -> gen_ai 子 span(遵循 GenAI 语义约定)。
        model = ""
        try:
            model = (
                (kwargs.get("invocation_params") or {}).get("model")
                or (serialized or {}).get("name")
                or ""
            )
        except Exception:
            model = ""
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
        self._emit(
            "llm_call",
            "llm_end",
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            call_cost=round(cost, 6),
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
        # LangGraph 节点切换;name 形如 "Market Analyst" / "Bull Researcher" 等
        name = (
            (kwargs.get("name") or "")
            or (serialized or {}).get("name", "")
            or "unknown"
        )
        stage = _normalize_stage(name)
        if not stage:
            return
        if stage not in self._completed_stages:
            self._emit(stage, "stage_start", langgraph_node=name)
            # OTel:TradingAgents 节点 -> 子 span(挂到 root span 下)。
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
        name = (kwargs.get("name") or "").strip()
        stage = _normalize_stage(name)
        if stage:
            self._completed_stages.add(stage)
            self._emit(stage, "stage_end", langgraph_node=name)
            # OTel:结束该节点 span。
            span = self._otel_stage_spans.pop(stage, None)
            if span is not None:
                otel.end_span(span)

    def on_llm_error(self, error, **kwargs):
        self._emit("error", "llm_error", error=str(error)[:200])

    def on_chain_error(self, error, **kwargs):
        self._emit("error", "chain_error", error=str(error)[:200])

    # ---- 公共方法 ----

    def record_cost(self, usd: float) -> None:
        self._total_cost += usd

    def _guess_stage(self, serialized: dict, kwargs: dict) -> str:
        name = (serialized.get("name") or kwargs.get("name") or "unknown").lower()
        return _normalize_stage(name) or "unknown"


def _normalize_stage(name: str) -> str:
    """把 LangGraph 节点名标准化到 STAGES_ORDER 里的一个值。"""
    n = (name or "").lower().replace(" ", "_")
    for stage in STAGES_ORDER:
        if stage in n or n in stage:
            return stage
    return ""


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
    started_at = None

    for entry in log_entries:
        tags = entry.get("tags") or {}
        stage = tags.get("stage")
        action = tags.get("action")
        ts = entry.get("timestamp")
        if started_at is None and ts:
            started_at = ts

        if not stage or stage not in stage_state:
            continue

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
        "stages": [stage_state[s] for s in STAGES_ORDER],
    }
