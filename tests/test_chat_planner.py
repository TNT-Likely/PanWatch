"""Planning 试点(全面诊断持仓)测试。

全 mock ai_client 与工具执行器,不触网。覆盖:意图识别、计划解析容错、
正常逐步推进、步骤失败重规划、计划生成失败降级默认计划。
"""

import asyncio

from src.core.chat_planner import (
    build_default_plan,
    normalize_steps,
    parse_plan,
    run_portfolio_diagnosis,
    should_use_planning,
)


class FakeStream:
    """记录 publish 事件的假 SSE 流。"""

    def __init__(self):
        self.events: list[tuple[str, dict]] = []

    async def publish(self, event, data):
        self.events.append((event, data))

    def plan_events(self):
        return [d for e, d in self.events if e == "plan"]

    def tokens(self):
        return "".join(d.get("text", "") for e, d in self.events if e == "token")


class FakeAI:
    """脚本化假 AI:chat_multi 从队列弹出(异常则抛),chat_stream 产出固定 token。"""

    def __init__(self, multi_queue, stream_tokens=("诊", "断", "完")):
        self.multi_queue = list(multi_queue)
        self.stream_tokens = list(stream_tokens)
        self.multi_calls = 0

    async def chat_multi(self, messages, temperature=0.4):
        self.multi_calls += 1
        item = self.multi_queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return item

    async def chat_stream(self, messages, tools=None, temperature=0.4):
        for t in self.stream_tokens:
            yield ("token", t)
        yield ("message", {"content": "".join(self.stream_tokens), "tool_calls": []})


def _exec_ok(db, name, args):
    async def _inner():
        return {
            "get_portfolio": "持仓:贵州茅台(600519) 100股",
            "get_technical_analysis": "技术面:多头",
            "get_stock_suggestions": "建议:持有",
        }.get(name, "")

    return _inner()


# ── 意图识别 ────────────────────────────────────────────────────────────
def test_should_use_planning_hits():
    """命中触发词 → 走计划驱动"""
    assert should_use_planning("帮我全面诊断我的持仓")
    assert should_use_planning("持仓诊断一下")
    assert should_use_planning("给我的组合做个全面体检")


def test_should_use_planning_miss():
    """普通问题不触发"""
    assert not should_use_planning("茅台现在多少钱")
    assert not should_use_planning("")


# ── 计划解析容错 ──────────────────────────────────────────────────────────
def test_parse_plan_plain_list():
    """纯 JSON 列表"""
    steps = parse_plan('[{"title":"A","action":"portfolio_risk"}]')
    assert steps and steps[0]["title"] == "A"


def test_parse_plan_dict_with_steps():
    """dict 带 steps 字段"""
    steps = parse_plan('{"steps":[{"title":"B","action":"analyze_stock"}]}')
    assert steps and steps[0]["action"] == "analyze_stock"


def test_parse_plan_json_fence_with_prose():
    """```json 围栏 + 前后解释文字"""
    text = '好的,这是计划:\n```json\n{"steps":[{"title":"C","action":"portfolio_risk"}]}\n```\n请确认'
    steps = parse_plan(text)
    assert steps and steps[0]["title"] == "C"


def test_parse_plan_malformed_returns_none():
    """完全非 JSON → None"""
    assert parse_plan("抱歉我无法生成计划") is None
    assert parse_plan("") is None


def test_normalize_steps_filters_summarize_and_assigns_ids():
    """规范化:过滤 summarize,补 id/status"""
    steps = normalize_steps(
        [
            {"title": "X", "action": "analyze_stock"},
            {"title": "汇总", "action": "summarize"},
            {"title": "Y", "action": "portfolio_risk"},
        ]
    )
    assert [s["id"] for s in steps] == [1, 2]
    assert all(s["status"] == "pending" for s in steps)
    assert all(s["action"] != "summarize" for s in steps)


# ── 编排:正常 / 重规划 / 降级 ────────────────────────────────────────────
def test_run_diagnosis_happy_path():
    """正常:生成计划 → 逐步执行 → 流式汇总,plan 事件推进到 done"""
    plan = '{"steps":[{"title":"组合整体风险","action":"portfolio_risk"}]}'
    ai = FakeAI(multi_queue=[plan, "风险评估结果"])
    stream = FakeStream()

    summary = asyncio.run(run_portfolio_diagnosis(None, stream, ai, _exec_ok))

    assert summary == "诊断完"  # 流式 token 拼接
    plans = stream.plan_events()
    assert plans[0]["status"] == "planning"
    assert plans[-1]["status"] == "done"
    # 最终步骤全部完成
    assert all(s["status"] == "done" for s in plans[-1]["steps"])
    assert stream.tokens() == "诊断完"


def test_run_diagnosis_replan_on_step_failure():
    """步骤失败 → 重规划一次 → 用新计划继续"""
    # 初始计划:analyze_stock(会因 get_technical_analysis 抛错而失败)
    plan = '{"steps":[{"title":"分析茅台","action":"analyze_stock","params":{"symbol":"600519"}}]}'
    replan = '{"steps":[{"title":"改为组合风险","action":"portfolio_risk"}]}'
    ai = FakeAI(multi_queue=[plan, replan, "组合风险结果"])
    stream = FakeStream()

    calls = {"tech": 0}

    def _exec(db, name, args):
        async def _inner():
            if name == "get_technical_analysis":
                calls["tech"] += 1
                raise RuntimeError("数据源超时")
            return {
                "get_portfolio": "持仓:茅台",
                "get_stock_suggestions": "建议",
            }.get(name, "")

        return _inner()

    summary = asyncio.run(run_portfolio_diagnosis(None, stream, ai, _exec))

    assert summary == "诊断完"
    # 触发过重规划(plan+replan+step 共 3 次 chat_multi)
    assert ai.multi_calls == 3
    # 最终计划里出现重规划后的步骤且已完成
    final_steps = stream.plan_events()[-1]["steps"]
    assert any("组合风险" in s["title"] and s["status"] == "done" for s in final_steps)


def test_run_diagnosis_degrades_when_plan_generation_fails():
    """计划生成失败 → 回退默认计划,仍产出汇总"""
    ai = FakeAI(multi_queue=[RuntimeError("LLM 挂了"), "默认风险评估"])
    stream = FakeStream()

    summary = asyncio.run(run_portfolio_diagnosis(None, stream, ai, _exec_ok))

    assert summary == "诊断完"
    plans = stream.plan_events()
    assert plans[-1]["status"] == "done"
    # 默认计划只有组合风险一步
    assert len(plans[-1]["steps"]) == 1


def test_build_default_plan_shape():
    """默认计划为组合风险单步"""
    plan = build_default_plan("持仓文本")
    assert plan[0]["action"] == "portfolio_risk"
