"""OTel 导出层测试。

覆盖三条主线:
1. 未配置 endpoint / 未装 SDK 时,otel.py 全程 no-op —— 不抛错、不产 span;
2. Agent 运行映射为 root span,LLM 调用映射为带 GenAI 语义约定属性的子 span,且子 span
   正确挂在 root span 之下(trace 关联);
3. gen_ai span 回填 token 用量(复用 ai_client 已有的 usage 数据)。

用 InMemorySpanExporter 断言,不接触真实 endpoint。
"""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from src.core import otel

# 未装 opentelemetry SDK 时,涉及 exporter 的用例整体跳过(no-op 用例不需要 SDK)。
_otel_sdk = pytest.importorskip("opentelemetry.sdk")


@pytest.fixture
def in_memory_exporter():
    """安装 InMemorySpanExporter(同步导出),用例结束后重置 otel 状态。"""
    exporter = otel.install_test_exporter()
    try:
        yield exporter
    finally:
        exporter.clear()
        otel.reset()


def _fake_openai_response(content: str, prompt_tokens: int, completion_tokens: int):
    """构造一个最小的 OpenAI ChatCompletion 响应桩。"""
    return SimpleNamespace(
        model="test-model",
        usage=SimpleNamespace(
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=prompt_tokens + completion_tokens,
        ),
        choices=[SimpleNamespace(message=SimpleNamespace(content=content))],
    )


# ---- no-op 降级 -----------------------------------------------------------

def test_未配置endpoint时init返回False且不启用():
    """未配置 OTEL_EXPORTER_OTLP_ENDPOINT 时 init_otel 返回 False 且保持关闭。"""
    otel.reset()
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("OTEL_EXPORTER_OTLP_ENDPOINT", None)
        assert otel.init_otel() is False
    assert otel.is_enabled() is False
    otel.reset()


def test_关闭时span接口全部no_op不报错():
    """OTel 关闭时,所有 span 接口均为 no-op,既不抛错也不产 span。"""
    otel.reset()
    assert otel.is_enabled() is False
    # 上下文管理器返回 None / no-op 句柄,均可安全使用
    with otel.agent_run_span("daily_report", trace_id="t-1") as span:
        assert span is None
    with otel.llm_span("gpt-x", operation="chat") as handle:
        handle.set_response(model="gpt-x", input_tokens=1, output_tokens=2)  # 不报错
    # 游离 span 接口
    assert otel.capture_context() is None
    s = otel.start_detached_span("x", attributes={"a": 1})
    assert s is None
    otel.set_span_attributes(s, {"b": 2})  # None 安全
    otel.end_span(s)  # None 安全


def test_关闭时ai_client正常工作无span():
    """OTel 关闭时,ai_client.chat 正常返回且不产生任何 span。"""
    otel.reset()
    from src.core.ai_client import AIClient

    client = AIClient(base_url="http://x", api_key="k", model="test-model")
    fake = _fake_openai_response("你好", 10, 5)
    with patch.object(
        client.client.chat.completions, "create", AsyncMock(return_value=fake)
    ):
        out = asyncio.run(client.chat("sys", "user"))
    assert out == "你好"
    assert client.total_tokens_used == 15


# ---- 启用后的 span 断言 ---------------------------------------------------

def test_agent运行映射为root_span(in_memory_exporter):
    """Agent 一次运行映射为 root span,带 agent 名与 trace_id 属性。"""
    with otel.agent_run_span("daily_report", trace_id="trace-123", trigger_source="schedule"):
        pass
    spans = in_memory_exporter.get_finished_spans()
    assert len(spans) == 1
    root = spans[0]
    assert root.name == "agent.run daily_report"
    assert root.parent is None  # 是 root
    assert root.attributes[otel.ATTR_AGENT_NAME] == "daily_report"
    assert root.attributes[otel.ATTR_TRACE_ID] == "trace-123"
    assert root.attributes[otel.ATTR_TRIGGER_SOURCE] == "schedule"


def test_llm调用产生带genai属性的子span(in_memory_exporter):
    """LLM 调用映射为 gen_ai 子 span,带 GenAI 语义约定属性,且挂在 root span 之下。"""
    from src.core.ai_client import AIClient

    client = AIClient(base_url="http://x", api_key="k", model="test-model")
    fake = _fake_openai_response("分析结果", 100, 40)

    async def _call():
        with otel.agent_run_span("daily_report", trace_id="trace-abc"):
            with patch.object(
                client.client.chat.completions, "create", AsyncMock(return_value=fake)
            ):
                return await client.chat("sys", "user")

    out = asyncio.run(_call())

    assert out == "分析结果"
    spans = in_memory_exporter.get_finished_spans()
    # 子 span 先结束、root 后结束
    assert len(spans) == 2
    llm = next(s for s in spans if s.name.startswith("chat"))
    root = next(s for s in spans if s.name.startswith("agent.run"))

    # GenAI 语义约定属性
    assert llm.attributes[otel.GEN_AI_SYSTEM] == "openai"
    assert llm.attributes[otel.GEN_AI_OPERATION_NAME] == "chat"
    assert llm.attributes[otel.GEN_AI_REQUEST_MODEL] == "test-model"
    assert llm.attributes[otel.GEN_AI_USAGE_INPUT_TOKENS] == 100
    assert llm.attributes[otel.GEN_AI_USAGE_OUTPUT_TOKENS] == 40

    # 子 span 挂在 root span 之下(同一 trace)
    assert llm.parent is not None
    assert llm.parent.span_id == root.context.span_id
    assert llm.context.trace_id == root.context.trace_id


def test_游离span可挂到捕获的父上下文(in_memory_exporter):
    """start_detached_span 用捕获的父上下文,可把节点 span 挂到 root span 下(模拟跨线程)。"""
    with otel.agent_run_span("tradingagents", trace_id="ta-1"):
        parent_ctx = otel.capture_context()
    # 在 root span 结束后,用捕获的上下文仍能建立父子关系(模拟 to_thread 场景)
    span = otel.start_detached_span(
        "tradingagents.stage market_analyst",
        parent_context=parent_ctx,
        attributes={otel.ATTR_TA_STAGE: "market_analyst"},
    )
    otel.end_span(span)

    spans = in_memory_exporter.get_finished_spans()
    root = next(s for s in spans if s.name.startswith("agent.run"))
    stage = next(s for s in spans if s.name.startswith("tradingagents.stage"))
    assert stage.attributes[otel.ATTR_TA_STAGE] == "market_analyst"
    assert stage.parent is not None
    assert stage.parent.span_id == root.context.span_id
