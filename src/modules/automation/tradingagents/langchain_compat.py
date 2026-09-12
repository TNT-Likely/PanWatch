"""LangChain 兼容性补丁:让小模型(Qwen 7B 等)返回的 tool_calls 也能通过校验。

问题:
LangChain 1.x ToolCall.args 严格要求是 dict;但部分 OpenAI 兼容服务
(硅基流动 Qwen2.5-7B 等)在响应里返回 args 是 JSON 字符串而不是 dict。
原生 OpenAI / DeepSeek / Claude 都返回 dict,小模型不严谨。

修法:
猴补 langchain_core.messages.tool 的 create_tool_call(或同等位置),如果
args 收到字符串就 json.loads 转 dict。失败则 fallback 空 dict + 记录。

调用方:`_run_tradingagents_sync` 入口处 `apply_compat_patches()`,worker 线程
局部生效。幂等 — 多次调用没问题(已 patch 过就 skip)。
"""

from __future__ import annotations

import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

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
