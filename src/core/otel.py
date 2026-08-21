"""OpenTelemetry 导出层(可选,默认关闭)。

在**不改动** PanWatch 自建可观测体系(``log_context`` / ``agent_runs`` /
``tradingagents.progress``)的前提下,额外挂一层标准 OTel 导出,让"自建 + 标准栈"
都能拿到实证。三类桥接:

- Agent 一次运行        -> root span(复用 ``agent_runs`` 的 ``trace_id`` 作关联)
- 单次 LLM 调用         -> gen_ai 子 span(复用 ``ai_client`` 已有的 token 用量)
- TradingAgents 节点    -> 子 span(复用 ``progress.py`` 的节点/LLM 事件)

设计原则(生产项目,增量可回退):

1. **默认零副作用**:未配置 ``OTEL_EXPORTER_OTLP_ENDPOINT``,或未安装 opentelemetry
   SDK 时,``init_otel()`` 直接返回 False,后续所有 span 接口降级为 no-op —— 不抛错、
   不引入运行时依赖、不改变任何既有行为。
2. **薄桥接**:只在既有埋点处包一层 context manager;埋点本身不感知 OTel 细节。
3. **懒加载**:本模块顶层**不** import opentelemetry,只有 ``init_otel()`` 被调用且
   endpoint 已配置时才尝试导入,因此 ``import src.core.otel`` 永远安全、零成本。

GenAI 语义约定(OpenTelemetry Semantic Conventions for Generative AI)让 span 能被
Jaeger / Tempo / Langfuse(OTLP)等标准 APM 直接识别为"一次模型调用"。
"""

from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from typing import Any, Iterator, Optional

logger = logging.getLogger(__name__)


# ---- GenAI 语义约定属性名 -------------------------------------------------
# 参考: OpenTelemetry Semantic Conventions for Generative AI
GEN_AI_SYSTEM = "gen_ai.system"
GEN_AI_OPERATION_NAME = "gen_ai.operation.name"
GEN_AI_REQUEST_MODEL = "gen_ai.request.model"
GEN_AI_RESPONSE_MODEL = "gen_ai.response.model"
GEN_AI_USAGE_INPUT_TOKENS = "gen_ai.usage.input_tokens"
GEN_AI_USAGE_OUTPUT_TOKENS = "gen_ai.usage.output_tokens"

# PanWatch 自定义属性(桥接自建 trace 模型,便于在 APM 里与 agent_runs 对齐)
ATTR_AGENT_NAME = "panwatch.agent.name"
ATTR_TRACE_ID = "panwatch.trace_id"
ATTR_TRIGGER_SOURCE = "panwatch.trigger_source"
ATTR_TA_STAGE = "panwatch.tradingagents.stage"

_SERVICE_NAME = os.getenv("OTEL_SERVICE_NAME", "panwatch")
_INSTRUMENTATION_SCOPE = "panwatch.otel"

# 模块级状态(单进程内单例)
_enabled: bool = False
_initialized: bool = False
_provider: Any = None
_tracer: Any = None


def is_enabled() -> bool:
    """OTel 导出当前是否已启用(endpoint 已配置且 SDK 可用且初始化成功)。"""
    return _enabled


def _import_sdk():
    """尝试导入 OTel SDK。未安装则返回 None(优雅降级)。"""
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        return trace, Resource, TracerProvider, BatchSpanProcessor
    except Exception:  # pragma: no cover - 仅在未装 SDK 时命中
        return None


def _build_otlp_exporter():
    """构造 OTLP span exporter。

    优先 HTTP(``proto/http``,端口约定 4318),回退 gRPC(``proto/grpc``,4317)。
    两者都会自动读取 ``OTEL_EXPORTER_OTLP_ENDPOINT`` 等标准环境变量,因此这里不显式
    传 endpoint,交给 SDK 按标准约定解析(最少惊讶原则)。
    """
    try:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
            OTLPSpanExporter,
        )

        return OTLPSpanExporter()
    except Exception:
        pass
    try:  # pragma: no cover - 环境相关
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
            OTLPSpanExporter as GrpcOTLPSpanExporter,
        )

        return GrpcOTLPSpanExporter()
    except Exception:
        return None


def init_otel(*, force: bool = False) -> bool:
    """从环境变量初始化 OTel 导出。幂等;返回是否成功启用。

    仅当 ``OTEL_EXPORTER_OTLP_ENDPOINT`` 非空**且** opentelemetry SDK/exporter 均可
    导入时才真正启用;任一缺失都静默降级为 no-op(不影响现有部署)。
    """
    global _enabled, _initialized, _provider, _tracer

    if _initialized and not force:
        return _enabled

    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
    if not endpoint:
        # 未配置 endpoint —— 默认关闭,零副作用。
        _initialized = True
        _enabled = False
        return False

    mods = _import_sdk()
    if mods is None:
        logger.warning(
            "OTEL_EXPORTER_OTLP_ENDPOINT 已配置(%s),但未安装 opentelemetry SDK,"
            "OTel 导出跳过。安装: pip install -r requirements-otel.txt",
            endpoint,
        )
        _initialized = True
        _enabled = False
        return False

    trace, Resource, TracerProvider, BatchSpanProcessor = mods
    exporter = _build_otlp_exporter()
    if exporter is None:
        logger.warning(
            "opentelemetry SDK 已装但缺少 OTLP exporter,OTel 导出跳过。"
            "安装: pip install -r requirements-otel.txt"
        )
        _initialized = True
        _enabled = False
        return False

    try:
        resource = Resource.create({"service.name": _SERVICE_NAME})
        provider = TracerProvider(resource=resource)
        provider.add_span_processor(BatchSpanProcessor(exporter))
        # 设为全局 provider(供上下文传播);span 创建仍走本模块持有的 tracer。
        trace.set_tracer_provider(provider)
        _provider = provider
        _tracer = provider.get_tracer(_INSTRUMENTATION_SCOPE)
        _enabled = True
        _initialized = True
        logger.info("OTel 导出已启用,endpoint=%s service=%s", endpoint, _SERVICE_NAME)
        return True
    except Exception as e:  # pragma: no cover - 初始化异常兜底
        logger.warning("OTel 初始化失败,降级为 no-op: %s", e)
        _enabled = False
        _initialized = True
        return False


# ---- 供测试:用 InMemorySpanExporter 同步导出 -----------------------------

def install_test_exporter():
    """测试专用:重置并安装 InMemorySpanExporter(SimpleSpanProcessor 同步导出)。

    返回 exporter 实例,可直接 ``get_finished_spans()`` 断言。生产代码不应调用。
    """
    global _enabled, _initialized, _provider, _tracer

    from opentelemetry import trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    exporter = InMemorySpanExporter()
    resource = Resource.create({"service.name": _SERVICE_NAME})
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    # 测试内可能重复安装:直接覆盖本模块持有的 provider/tracer;
    # 全局 provider 只在首次设置(OTel 不允许覆盖,重复设置会告警),故不强设全局。
    try:
        trace.set_tracer_provider(provider)
    except Exception:
        pass
    _provider = provider
    _tracer = provider.get_tracer(_INSTRUMENTATION_SCOPE)
    _enabled = True
    _initialized = True
    return exporter


def reset() -> None:
    """重置模块状态(测试 teardown 用)。"""
    global _enabled, _initialized, _provider, _tracer
    _enabled = False
    _initialized = False
    _provider = None
    _tracer = None


# ---- span 接口(全部在关闭时 no-op) --------------------------------------

@contextmanager
def agent_run_span(
    agent_name: str,
    trace_id: str = "",
    trigger_source: str = "",
) -> Iterator[Any]:
    """一次 Agent 运行的 root span。关闭时 no-op(yield None)。

    复用 ``agent_runs`` 的 ``trace_id`` 作为 span 属性,方便在 APM 里与 run 表对齐。
    """
    if not _enabled or _tracer is None:
        yield None
        return
    with _tracer.start_as_current_span(f"agent.run {agent_name}") as span:
        try:
            span.set_attribute(ATTR_AGENT_NAME, agent_name)
            if trace_id:
                span.set_attribute(ATTR_TRACE_ID, trace_id)
            if trigger_source:
                span.set_attribute(ATTR_TRIGGER_SOURCE, trigger_source)
        except Exception:
            pass
        yield span


class _LLMSpan:
    """gen_ai span 的薄句柄:调用返回后回填 token 用量/响应模型。"""

    __slots__ = ("_span",)

    def __init__(self, span: Any):
        self._span = span

    def set_response(
        self,
        *,
        model: Optional[str] = None,
        input_tokens: Optional[int] = None,
        output_tokens: Optional[int] = None,
    ) -> None:
        if self._span is None:
            return
        try:
            if model:
                self._span.set_attribute(GEN_AI_RESPONSE_MODEL, model)
            if input_tokens is not None:
                self._span.set_attribute(GEN_AI_USAGE_INPUT_TOKENS, int(input_tokens))
            if output_tokens is not None:
                self._span.set_attribute(GEN_AI_USAGE_OUTPUT_TOKENS, int(output_tokens))
        except Exception:
            pass


@contextmanager
def llm_span(
    model: str,
    *,
    system: str = "openai",
    operation: str = "chat",
) -> Iterator[_LLMSpan]:
    """单次 LLM 调用的 gen_ai 子 span。关闭时 yield 一个 no-op 句柄。

    span 名遵循 GenAI 约定 ``{operation} {model}``;请求侧属性在进入时写入,响应侧
    (token/响应模型)由调用方拿到 usage 后通过返回句柄回填。
    """
    if not _enabled or _tracer is None:
        yield _LLMSpan(None)
        return
    span_name = f"{operation} {model}".strip() if model else operation
    with _tracer.start_as_current_span(span_name) as span:
        try:
            span.set_attribute(GEN_AI_SYSTEM, system)
            span.set_attribute(GEN_AI_OPERATION_NAME, operation)
            if model:
                span.set_attribute(GEN_AI_REQUEST_MODEL, model)
        except Exception:
            pass
        yield _LLMSpan(span)


def capture_context() -> Any:
    """捕获当前 OTel 上下文(供跨线程传播 root span 关系)。关闭时返回 None。

    TradingAgents 在 ``asyncio.to_thread`` 里同步执行,OTel 上下文不会自动跨线程,
    需在异步侧捕获、在工作线程侧显式作为 parent 传入。
    """
    if not _enabled:
        return None
    try:
        from opentelemetry import context as otel_context

        return otel_context.get_current()
    except Exception:
        return None


def start_detached_span(
    name: str,
    *,
    parent_context: Any = None,
    attributes: Optional[dict] = None,
) -> Any:
    """启动一个"游离" span(不设为 current,需手动 ``end``)。关闭时返回 None。

    用于 callback 式埋点(如 TradingAgents 节点)——start/end 分处两次回调、且可能
    运行在工作线程,无法用 with 语法。传入 ``capture_context()`` 的结果作为 parent
    以挂到 root span 下。
    """
    if not _enabled or _tracer is None:
        return None
    try:
        span = _tracer.start_span(name, context=parent_context)
        if attributes:
            for k, v in attributes.items():
                try:
                    span.set_attribute(k, v)
                except Exception:
                    pass
        return span
    except Exception:
        return None


def set_span_attributes(span: Any, attributes: dict) -> None:
    """给游离 span 补属性。span 为 None 时 no-op。"""
    if span is None:
        return
    for k, v in attributes.items():
        try:
            span.set_attribute(k, v)
        except Exception:
            pass


def end_span(span: Any) -> None:
    """结束一个游离 span。span 为 None 时 no-op。"""
    if span is None:
        return
    try:
        span.end()
    except Exception:
        pass
