"""AI 模型运行时 failover 单测（全 mock，不发真实请求）。

覆盖：
- 错误分类分流（摘参重试 / 换模型 / 直接抛）；
- 负缓存冷却与恢复探测；
- 流式路径 failover（首 token 前可切、首 token 后透出）；
- 候选链构建（主模型 + 库内备选）；
- run 记录实际使用的模型（AgentContext.model_label 反映 used_model_label）。
"""

import asyncio

import httpx
import pytest
from openai import (
    APIConnectionError,
    APITimeoutError,
    AuthenticationError,
    BadRequestError,
    InternalServerError,
    RateLimitError,
)
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.core import ai_failover as m
from src.core.ai_failover import (
    ERR_FATAL,
    ERR_PARAM,
    ERR_SWITCH,
    FailoverAIClient,
    build_failover_client,
    classify_ai_error,
    clear_ai_failover_state,
)
from src.web.database import Base
from src.web.models import AIModel, AIService

_REQ = httpx.Request("POST", "http://test")


def _http_err(cls, status, msg="err"):
    return cls(msg, response=httpx.Response(status, request=_REQ), body=None)


class _GenericStatusError(Exception):
    """带 status_code 的普通异常（走 classify 的通用状态码分支）。"""

    def __init__(self, msg, status_code):
        super().__init__(msg)
        self.status_code = status_code


class _FakeClient:
    """脚本化的假 AIClient：每次非流式调用弹出一个脚本项（异常则抛，否则返回）。"""

    def __init__(self, model="m", script=None):
        self.model = model
        self.base_url = "http://b"
        self.api_key = "k"
        self.total_tokens_used = 0
        self._script = list(script or [])
        self.temps: list = []
        self.calls = 0

    def _pop(self, temperature):
        self.temps.append(temperature)
        self.calls += 1
        item = self._script.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def chat_multi(self, messages, temperature=0.4):
        return self._pop(temperature)

    async def chat(self, system_prompt, user_content, images=None, temperature=0.4):
        return self._pop(temperature)

    async def chat_with_tools(self, messages, tools, temperature=0.4):
        return self._pop(temperature)


# ── 错误分类 ────────────────────────────────────────────────────────────


def test_classify_timeout_switch():
    """超时归为「换模型」"""
    assert classify_ai_error(APITimeoutError(request=_REQ)) == ERR_SWITCH


def test_classify_connection_switch():
    """连接错误归为「换模型」"""
    assert classify_ai_error(APIConnectionError(message="c", request=_REQ)) == ERR_SWITCH


def test_classify_rate_limit_switch():
    """限流(429)归为「换模型」"""
    assert classify_ai_error(_http_err(RateLimitError, 429)) == ERR_SWITCH


def test_classify_server_error_switch():
    """服务端 5xx 归为「换模型」"""
    assert classify_ai_error(_http_err(InternalServerError, 500)) == ERR_SWITCH


def test_classify_auth_switch():
    """鉴权失效(401)归为「换模型」（备用服务商 key 可能有效）"""
    assert classify_ai_error(_http_err(AuthenticationError, 401)) == ERR_SWITCH


def test_classify_param_incompatible_retry_same():
    """参数不兼容(400 + temperature 提示)归为「摘参重试同模型」"""
    err = _http_err(BadRequestError, 400, "temperature is not supported for this model")
    assert classify_ai_error(err) == ERR_PARAM


def test_classify_business_400_fatal():
    """明确的 4xx 业务错误(非参数类)归为「直接抛」"""
    err = _http_err(BadRequestError, 400, "content policy violation")
    assert classify_ai_error(err) == ERR_FATAL


def test_classify_generic_4xx_fatal():
    """通用异常带 4xx 状态码归为「直接抛」"""
    assert classify_ai_error(_GenericStatusError("not found", 404)) == ERR_FATAL


def test_classify_generic_5xx_switch():
    """通用异常带 5xx 状态码归为「换模型」"""
    assert classify_ai_error(_GenericStatusError("bad gateway", 503)) == ERR_SWITCH


def test_classify_unknown_defaults_switch():
    """无从判断的未知异常保守归为「换模型」（下一候选可能是不同服务商）"""
    assert classify_ai_error(RuntimeError("mystery")) == ERR_SWITCH


# ── FailoverAIClient 行为 ────────────────────────────────────────────────


def test_failover_param_strip_retry_same_model():
    """参数不兼容：摘掉 temperature 重试同一模型，不切换"""
    clear_ai_failover_state()
    err = _http_err(BadRequestError, 400, "temperature unsupported")
    c = _FakeClient("m1", [err, "OK"])
    fc = FailoverAIClient([(c, "svc/m1")])

    result = asyncio.run(fc.chat_multi([{"role": "user", "content": "hi"}]))

    assert result == "OK"
    assert c.calls == 2
    assert c.temps == [0.4, None]  # 第二次摘除 temperature
    assert fc.used_model_label == "svc/m1"


def test_failover_switch_to_next_and_cooldown():
    """主模型 5xx：降级下一候选，失败候选进冷却，后续调用直接跳过"""
    clear_ai_failover_state()
    c1 = _FakeClient("m1", [_http_err(InternalServerError, 500)])
    c2 = _FakeClient("m2", ["OK2"])
    fc = FailoverAIClient([(c1, "svc/m1"), (c2, "svc/m2")])

    r = asyncio.run(fc.chat_multi([{"role": "user", "content": "x"}]))
    assert r == "OK2"
    assert fc.used_model_label == "svc/m2"
    assert m._is_cooling("svc/m1")  # 主模型进入冷却

    # 第二次调用：m1 仍在冷却窗口内，直接用 m2，不再触碰 m1
    c2._script = ["OK3"]
    r2 = asyncio.run(fc.chat_multi([{"role": "user", "content": "y"}]))
    assert r2 == "OK3"
    assert c1.calls == 1  # m1 未被再次调用


def test_failover_fatal_raises_without_switch():
    """致命错误(内容/prompt 类)：直接抛，不尝试下一候选"""
    clear_ai_failover_state()
    c1 = _FakeClient("m1", [_http_err(BadRequestError, 400, "content filter triggered")])
    c2 = _FakeClient("m2", ["unreachable"])
    fc = FailoverAIClient([(c1, "svc/m1"), (c2, "svc/m2")])

    with pytest.raises(BadRequestError):
        asyncio.run(fc.chat_multi([{"role": "user", "content": "x"}]))
    assert c2.calls == 0  # 未降级到下一候选


def test_failover_all_cooling_recovery_probe():
    """所有候选都在冷却时：用主候选做恢复探测，而非直接失败"""
    clear_ai_failover_state()
    m._mark_fail("svc/m1")
    m._mark_fail("svc/m2")
    c1 = _FakeClient("m1", ["PRIMARY"])
    c2 = _FakeClient("m2", [])
    fc = FailoverAIClient([(c1, "svc/m1"), (c2, "svc/m2")])

    r = asyncio.run(fc.chat_multi([{"role": "user", "content": "x"}]))
    assert r == "PRIMARY"
    assert c1.calls == 1
    assert not m._is_cooling("svc/m1")  # 探测成功后清除冷却


def test_failover_chain_exhausted_raises_last_error():
    """所有候选均失败(可降级类)：抛出最后一个异常"""
    clear_ai_failover_state()
    c1 = _FakeClient("m1", [_http_err(InternalServerError, 500, "boom1")])
    c2 = _FakeClient("m2", [_http_err(RateLimitError, 429, "boom2")])
    fc = FailoverAIClient([(c1, "svc/m1"), (c2, "svc/m2")])

    with pytest.raises(RateLimitError):
        asyncio.run(fc.chat_multi([{"role": "user", "content": "x"}]))


# ── 流式 failover ────────────────────────────────────────────────────────


class _StreamRaiseBefore:
    """流开始前即抛错的假客户端（首 anext 抛异常）。"""

    model = "m1"
    base_url = "http://b"
    api_key = "k"
    total_tokens_used = 0

    async def chat_stream(self, messages, tools=None, temperature=0.4):
        raise _http_err(InternalServerError, 500, "stream boom")
        yield  # 使函数成为异步生成器（不可达）


class _StreamOK:
    """正常产出一段 token 的假客户端。"""

    model = "m2"
    base_url = "http://b"
    api_key = "k"
    total_tokens_used = 0

    async def chat_stream(self, messages, tools=None, temperature=0.4):
        yield ("token", "hi")
        yield ("message", {"content": "hi", "tool_calls": []})


class _StreamRaiseAfter:
    """产出一个 token 后才抛错的假客户端。"""

    model = "m1"
    base_url = "http://b"
    api_key = "k"
    total_tokens_used = 0

    async def chat_stream(self, messages, tools=None, temperature=0.4):
        yield ("token", "par")
        raise _http_err(InternalServerError, 500, "mid-stream boom")


def test_failover_stream_switch_before_first_token():
    """流式：首 token 前失败可安全切换到下一候选"""
    clear_ai_failover_state()
    fc = FailoverAIClient([(_StreamRaiseBefore(), "svc/m1"), (_StreamOK(), "svc/m2")])

    async def run():
        return [ev async for ev in fc.chat_stream([{"role": "user", "content": "x"}])]

    events = asyncio.run(run())
    assert ("token", "hi") in events
    assert fc.used_model_label == "svc/m2"
    assert m._is_cooling("svc/m1")


def test_failover_stream_raise_after_started():
    """流式：已产出 token 后失败无法回滚，直接透出异常"""
    clear_ai_failover_state()
    fc = FailoverAIClient([(_StreamRaiseAfter(), "svc/m1"), (_StreamOK(), "svc/m2")])

    async def run():
        out = []
        async for ev in fc.chat_stream([{"role": "user", "content": "x"}]):
            out.append(ev)
        return out

    with pytest.raises(InternalServerError):
        asyncio.run(run())


# ── 候选链构建 ───────────────────────────────────────────────────────────


def _mem_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_build_failover_client_chain_from_db():
    """构建候选链：主模型置首，其余库内模型按优先级补齐为备选"""
    clear_ai_failover_state()
    db = _mem_session()
    svc = AIService(name="S", base_url="http://b", api_key="k")
    db.add(svc)
    db.commit()
    m1 = AIModel(name="M1", service_id=svc.id, model="glm-4", is_default=True)
    m2 = AIModel(name="M2", service_id=svc.id, model="glm-4-flash", is_default=False)
    db.add_all([m1, m2])
    db.commit()

    fc = build_failover_client(m1, svc, db=db)
    labels = [lbl for _, lbl in fc.candidates]
    assert labels[0] == "S/glm-4"  # 主模型置首
    assert "S/glm-4-flash" in labels  # 备选补齐
    db.close()


def test_build_failover_client_env_fallback():
    """无库内模型时回退环境变量单候选"""
    clear_ai_failover_state()
    db = _mem_session()
    fc = build_failover_client(None, None, db=db)
    assert len(fc.candidates) == 1
    assert fc.candidates[0][1].startswith("env/")
    db.close()


# ── run 记录实际使用的模型 ───────────────────────────────────────────────


def test_agent_context_model_label_reflects_used_model():
    """AgentContext.model_label 反映 failover 实际使用的模型（供 agent_runs 落库）"""
    from src.agents.base import AgentContext

    fc = FailoverAIClient([(_FakeClient("m1"), "svc/m1"), (_FakeClient("m2"), "svc/m2")])
    ctx = AgentContext(ai_client=fc, notifier=None, config=None, model_label="svc/m1")

    # 未发生切换：返回主模型标签
    assert ctx.model_label == "svc/m1"

    # 发生 failover 后：反映实际跑通的模型
    fc.used_model_label = "svc/m2"
    assert ctx.model_label == "svc/m2"
