"""TradingAgents 运行时适配：LLM 配置、密钥注入和 LangChain 兼容补丁。

桥接 PanWatch AIClient 配置 → TradingAgents LLM config。

TradingAgents 通过 langchain-openai / langchain-anthropic 等驱动 LLM,
读取 config 字典 + 环境变量(`OPENAI_API_KEY`/`DEEPSEEK_API_KEY` 等)。
本模块把 PanWatch 的 AIClient 配置桥接过去。
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from src.platform.ai.ai_client import AIClient

logger = logging.getLogger(__name__)

# 这是 Agent 入口允许依赖的稳定运行时接口；兼容补丁实现留在本文件下半部。
__all__ = [
    "VALID_ANALYSTS",
    "apply_compat_patches",
    "build_ta_llm_config",
    "inject_api_key_env",
]


# TradingAgents selected_analysts 字段的合法值(见上游 graph/trading_graph.py)
VALID_ANALYSTS = {"market", "social", "news", "fundamentals"}


def build_ta_llm_config(
    ai_client: AIClient,
    *,
    debate_rounds: int = 1,
    selected_analysts: list[str] | None = None,
    output_language: str = "Chinese",
    deep_model: str | None = None,
    quick_model: str | None = None,
    market: str = "",
    enable_sec_edgar: bool = False,
    runtime_dir: str | Path | None = None,
    holding_period_days: int = 5,
    llm_timeout_seconds: int = 120,
    llm_max_retries: int = 0,
    llm_max_tokens: int = 4096,
) -> dict[str, Any]:
    """生成 TradingAgents 期望的 config dict。

    继承 tradingagents.default_config.DEFAULT_CONFIG (含 data_cache_dir / project_dir /
    memory_log_path 等必需字段),再覆盖 PanWatch 配置:
    - llm_provider: 统一走 openrouter 兼容协议(走 chat completions,避开 OpenAI Responses API)
    - backend_url: PanWatch AI 服务的 base_url
    - deep_think_llm: 推理/辩论/风控/PM 用的"强模型"。默认走 ai_client.model;
      可由 deep_model 参数覆盖,允许辩论用 claude-sonnet / o3 这种贵但准的模型
    - quick_think_llm: 分析师工具调用用的"快模型"。默认 deep_model;
      可由 quick_model 参数覆盖,允许分析师用 haiku / gpt-4o-mini 等便宜模型
    - max_debate_rounds: 辩论轮次
    - selected_analysts: ["market", "social", "news", "fundamentals"]
    - output_language: "Chinese" / "English"

    注意:TA 上游 deep + quick 共用 backend_url,所以两个模型必须在**同一个 endpoint** 后面。
    要混 Claude + GPT 推荐 LiteLLM proxy 把多 provider 聚合到一个 endpoint。
    """
    analysts = list(selected_analysts or VALID_ANALYSTS)
    invalid = [a for a in analysts if a not in VALID_ANALYSTS]
    if invalid:
        raise ValueError(
            f"非法 analyst 名: {invalid}; 合法值: {sorted(VALID_ANALYSTS)}"
        )

    # 继承上游默认 config(含 data_cache_dir / project_dir / memory_log_path 等),
    # 否则 TradingAgentsGraph.__init__ 用 os.makedirs(config["data_cache_dir"]) 会 KeyError。
    try:
        from tradingagents.default_config import DEFAULT_CONFIG as _UPSTREAM_DEFAULT
        config = dict(_UPSTREAM_DEFAULT)
    except ImportError:
        config = {}

    # 上游 config 含嵌套 vendor 配置；先复制，避免单次运行污染 DEFAULT_CONFIG。
    config["data_vendors"] = dict(config.get("data_vendors") or {})
    config["tool_vendors"] = dict(config.get("tool_vendors") or {})

    if runtime_dir is not None:
        root = Path(runtime_dir).expanduser().resolve()
        results_dir = root / "results"
        data_cache_dir = root / "cache"
        memory_dir = root / "memory"
        for directory in (results_dir, data_cache_dir, memory_dir):
            directory.mkdir(parents=True, exist_ok=True)
        config.update({
            "results_dir": str(results_dir),
            "data_cache_dir": str(data_cache_dir),
            "memory_log_path": str(memory_dir / "trading_memory.md"),
        })

    # SEC EDGAR 的三张财务报表具备 filing-date 语义，只在美股且用户显式启用时
    # 作为首选；非 SEC 标的或暂时不可用时回退 yfinance。
    statement_vendor = "sec_edgar,yfinance" if enable_sec_edgar and market.upper() == "US" else "yfinance"
    # set_config() 对嵌套 dict 做 merge。即使本次不启用 EDGAR，也必须显式写回
    # yfinance，避免前一次美股运行留下的 tool_vendors 泄漏到 A/HK 分析。
    config["tool_vendors"].update({
        "get_balance_sheet": statement_vendor,
        "get_cashflow": statement_vendor,
        "get_income_statement": statement_vendor,
    })

    # PanWatch 覆盖。
    # ⚠️ llm_provider 故意不用 "openai":TA 检测到 openai 会强制开 use_responses_api=True
    # (OpenAI Responses API,/v1/responses 端点),硅基流动/智谱/Ollama 等第三方 OpenAI 兼容
    # 服务不支持这个端点,会 404。
    # 用 "openrouter" 走标准 chat completions (/v1/chat/completions),同时 backend_url
    # 覆盖默认 openrouter 端点为 PanWatch 配置的真实 base_url。
    # 双模型解析:
    # - deep_model 未指定 → 用 ai_client.model
    # - quick_model 未指定 → 用 deep_model(单模型场景退化)
    deep_llm = (deep_model or ai_client.model or "").strip() or ai_client.model
    quick_llm = (quick_model or deep_llm or "").strip() or deep_llm

    config.update({
        "llm_provider": "openrouter",
        "backend_url": ai_client.base_url,
        "deep_think_llm": deep_llm,
        "quick_think_llm": quick_llm,
        "max_debate_rounds": max(1, int(debate_rounds)),
        "max_risk_discuss_rounds": 1,
        "selected_analysts": analysts,
        "output_language": output_language,
        "online_tools": True,
        "checkpoint_enabled": False,  # 避免 sqlite checkpoint 文件污染
        "holding_period_days": max(1, int(holding_period_days)),
        # TradingAgents 0.5.0 默认把这些交给底层 SDK；不设边界时，供应商
        # 连接断开或模型持续输出会让整个 LangGraph 永久停在当前 analyst。
        "llm_timeout_seconds": max(1, int(llm_timeout_seconds)),
        "llm_max_retries": max(0, int(llm_max_retries)),
        "max_tokens": max(256, int(llm_max_tokens)),
    })
    return config


def inject_api_key_env(ai_client: AIClient) -> None:
    """把 PanWatch AI 服务的 API key 注入到环境变量。

    TradingAgents llm_clients 按 provider 读不同 env var
    (OPENAI_API_KEY / DEEPSEEK_API_KEY / OPENROUTER_API_KEY 等)。
    我们 PanWatch 走 openrouter 兼容模式(chat completions),所以注入
    OPENROUTER_API_KEY。同时也设 OPENAI_API_KEY 作 fallback。

    注意:这是进程级 env var,如果同进程并发跑多个不同 key 的请求,可能竞态。
    P0 假设 max_workers=2 且只用一个 AI service,可接受。
    """
    if not ai_client.api_key:
        logger.warning("[TA] AIClient 没有 api_key,TradingAgents LLM 调用大概率失败")
        return
    # 覆盖多个候选 env var,让 TA 不管走哪条 provider 分支都能取到 key
    os.environ["OPENROUTER_API_KEY"] = ai_client.api_key
    os.environ["OPENAI_API_KEY"] = ai_client.api_key
    os.environ["DEEPSEEK_API_KEY"] = ai_client.api_key


# ============================================================================
# LangChain compatibility patches
# ============================================================================

_PATCH_APPLIED = False


def apply_compat_patches() -> None:
    """应用所有 LangChain 兼容性补丁。幂等。"""
    global _PATCH_APPLIED
    if _PATCH_APPLIED:
        return

    _patch_tool_call_args_coercion()
    _patch_ai_message_init()
    _PATCH_APPLIED = True


def _coerce_tool_calls_args(tool_calls: Any) -> Any:
    """把 tool_calls 列表中每项的 args 字段(若是 JSON 字符串)转成 dict。"""
    if not isinstance(tool_calls, list):
        return tool_calls
    fixed = []
    for tc in tool_calls:
        if isinstance(tc, dict) and "args" in tc:
            raw = tc.get("args")
            if isinstance(raw, str):
                try:
                    parsed = json.loads(raw)
                    if isinstance(parsed, dict):
                        tc = {**tc, "args": parsed}
                    else:
                        tc = {**tc, "args": {}}
                except (json.JSONDecodeError, TypeError):
                    tc = {**tc, "args": {}}
        fixed.append(tc)
    return fixed


def _patch_ai_message_init() -> None:
    """Patch AIMessage.__init__ 让 tool_calls 字段在校验前自动 coerce str args → dict。

    这是直接拦截 AIMessage 构造的可靠路径,不论 tool_calls 走的哪个上游函数。
    """
    try:
        from langchain_core.messages.ai import AIMessage
    except ImportError:
        return

    if getattr(AIMessage, "_panwatch_patched", False):
        return

    original_init = AIMessage.__init__

    def _patched_init(self, *args, **kwargs):
        if "tool_calls" in kwargs:
            kwargs["tool_calls"] = _coerce_tool_calls_args(kwargs["tool_calls"])
        return original_init(self, *args, **kwargs)

    AIMessage.__init__ = _patched_init  # type: ignore[method-assign]
    AIMessage._panwatch_patched = True  # type: ignore[attr-defined]
    logger.info("[TA compat] 已 patch AIMessage.__init__ 容忍 tool_calls.args 字符串")


def _patch_tool_call_args_coercion() -> None:
    """让 ToolCall / AIMessage 接受 string 类型的 args 并自动 json.loads。"""
    try:
        from langchain_core.messages import tool as _tool_module
    except ImportError:
        logger.debug("[TA compat] langchain_core 未装,跳过 tool_call 补丁")
        return

    # 找到 create_tool_call 工厂函数(langchain 1.x);旧版可能叫 ToolCall 类直接构造
    create_func = getattr(_tool_module, "create_tool_call", None)
    if create_func is None:
        logger.debug("[TA compat] create_tool_call 未找到,跳过")
        return

    if getattr(create_func, "_panwatch_patched", False):
        return  # 已经 patched

    original = create_func

    def _patched_create_tool_call(*args, **kwargs):
        # 取出 args 参数(可能位置或关键字)
        raw_args = kwargs.get("args")
        if raw_args is None and len(args) >= 2:
            # 位置参数:create_tool_call(name, args, ...) 顺序假设
            # 实际签名见 langchain_core.messages.tool 源码,这里宽松处理
            try:
                # 重新构造 kwargs 让上游严格 validator 拿到 dict
                pass
            except Exception:
                pass

        # 修正 args 类型
        if isinstance(raw_args, str):
            try:
                parsed = json.loads(raw_args)
                if isinstance(parsed, dict):
                    kwargs["args"] = parsed
                    logger.debug(
                        f"[TA compat] tool_call.args 字符串已自动 parse 成 dict "
                        f"(原始长度 {len(raw_args)})"
                    )
                else:
                    kwargs["args"] = {}
            except (json.JSONDecodeError, TypeError):
                kwargs["args"] = {}
                logger.debug("[TA compat] tool_call.args 不是合法 JSON,降级为 {}")

        return original(*args, **kwargs)

    _patched_create_tool_call._panwatch_patched = True  # type: ignore[attr-defined]

    # 替换模块级符号 + 替换内部 import
    _tool_module.create_tool_call = _patched_create_tool_call
    try:
        # langchain_core.output_parsers.openai_tools 在文件顶部 from . import create_tool_call
        # 但 import 语义是把对象绑定到本地,所以需要也替换那边
        from langchain_core.output_parsers import openai_tools as _ot
        if hasattr(_ot, "create_tool_call"):
            _ot.create_tool_call = _patched_create_tool_call
    except ImportError:
        pass

    logger.info("[TA compat] 已 patch langchain_core.messages.tool.create_tool_call")


def _patch_ai_message_validator() -> None:
    """备用方案:直接 patch AIMessage.model_validate 在 args 是 str 时降级清洗。

    目前不启用,只在 tool_call_coercion 不够用时启用。
    """
    pass
