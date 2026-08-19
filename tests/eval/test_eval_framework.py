"""评测框架自测（全 mock，不发真实请求，随 make test 常跑）。

验证：运行器能正确驱动工具循环并记录过程；断言引擎能抓住
工具选错/白名单外/参数错/无据回答/闲聊误调工具等每一类失败。
"""

import asyncio
import json
from types import SimpleNamespace

import pytest

from tests.eval.cases.chat_cases import CHAT_CASES
from tests.eval.cases.structured_cases import STRUCTURED_CASES
from tests.eval.framework import (
    ChatEvalCase,
    ChatEvalRunner,
    TOOL_WHITELIST,
    evaluate_case,
)
from tests.eval.judge import JudgeConfig, JudgeScore, LLMJudge


def _msg(content=None, tool_calls=None):
    """构造 chat_with_tools 返回的 message 替身。"""
    return SimpleNamespace(content=content, tool_calls=tool_calls)


def _tc(id, name, arguments):
    fn = SimpleNamespace(name=name, arguments=arguments)
    return SimpleNamespace(id=id, function=fn)


class ScriptedAIClient:
    """按脚本逐轮返回 message 的假模型。"""

    def __init__(self, rounds):
        self._rounds = list(rounds)
        self.seen_messages: list[list[dict]] = []

    async def chat_with_tools(self, messages, tools, temperature=0.0):
        self.seen_messages.append([dict(m) for m in messages])
        assert self._rounds, "脚本轮次已用尽"
        return self._rounds.pop(0)


def _run(runner, case):
    return asyncio.run(runner.run_case(case))


def test_runner_records_tool_loop():
    """运行器：完整走一轮工具循环，记录调用与最终回答，断言全过"""
    case = next(c for c in CHAT_CASES if c.id == "quote-1")
    client = ScriptedAIClient([
        _msg(tool_calls=[_tc("c1", "get_stock_quote", '{"symbol": "600519", "market": "CN"}')]),
        _msg(content="贵州茅台现价 1712.5 元，涨 1.35%。"),
    ])
    result = _run(ChatEvalRunner(client), case)

    assert result.tool_calls == [("get_stock_quote", {"symbol": "600519", "market": "CN"})]
    assert "1712.5" in result.answer
    assert evaluate_case(case, result) == []
    # mock 工具数据确实注入了第二轮上下文
    tool_msgs = [m for m in client.seen_messages[-1] if m.get("role") == "tool"]
    assert len(tool_msgs) == 1
    assert "1712.5" in tool_msgs[0]["content"]


def test_assert_catches_wrong_tool():
    """断言引擎：该查行情却查了自选股 → 报缺少必需工具"""
    case = next(c for c in CHAT_CASES if c.id == "quote-1")
    client = ScriptedAIClient([
        _msg(tool_calls=[_tc("c1", "get_watchlist", "{}")]),
        _msg(content="您的自选股如下…"),
    ])
    result = _run(ChatEvalRunner(client), case)
    failures = evaluate_case(case, result)
    assert any("缺少必需的工具调用: get_stock_quote" in f for f in failures)


def test_assert_catches_whitelist_violation():
    """断言引擎：调用白名单外的工具（如写操作）→ 直接失败"""
    case = ChatEvalCase(id="x", question="帮我下单买入")
    client = ScriptedAIClient([
        _msg(tool_calls=[_tc("c1", "place_order", '{"symbol": "600519"}')]),
        _msg(content="已下单"),
    ])
    result = _run(ChatEvalRunner(client), case)
    failures = evaluate_case(case, result)
    assert any("白名单外的工具: place_order" in f for f in failures)


def test_assert_catches_wrong_params():
    """断言引擎：symbol 参数传错 → 报参数不符"""
    case = next(c for c in CHAT_CASES if c.id == "quote-1")
    client = ScriptedAIClient([
        _msg(tool_calls=[_tc("c1", "get_stock_quote", '{"symbol": "000001"}')]),
        _msg(content="价格 1712.5"),
    ])
    result = _run(ChatEvalRunner(client), case)
    failures = evaluate_case(case, result)
    assert any("参数不符合预期" in f for f in failures)


def test_assert_catches_ungrounded_answer():
    """断言引擎：答案没有引用工具返回的关键值 → 报无据"""
    case = next(c for c in CHAT_CASES if c.id == "quote-1")
    client = ScriptedAIClient([
        _msg(tool_calls=[_tc("c1", "get_stock_quote", '{"symbol": "600519"}')]),
        _msg(content="茅台是好公司，建议长期持有。"),  # 没引用价格
    ])
    result = _run(ChatEvalRunner(client), case)
    failures = evaluate_case(case, result)
    assert any("答案缺少工具结果引用" in f for f in failures)


def test_assert_catches_chitchat_tool_call():
    """断言引擎：闲聊时误调工具 → 报不该调用"""
    case = next(c for c in CHAT_CASES if c.id == "chitchat-1")
    client = ScriptedAIClient([
        _msg(tool_calls=[_tc("c1", "get_portfolio", "{}")]),
        _msg(content="你好！你的持仓是…"),
    ])
    result = _run(ChatEvalRunner(client), case)
    failures = evaluate_case(case, result)
    assert any("不该调用工具却调用了" in f for f in failures)


def test_assert_tool_failure_case():
    """断言引擎：工具失败场景——如实说明通过，编造价格失败"""
    case = next(c for c in CHAT_CASES if c.id == "fail-1")

    honest = ScriptedAIClient([
        _msg(tool_calls=[_tc("c1", "get_stock_quote", '{"symbol": "600519"}')]),
        _msg(content="抱歉，行情数据获取失败，请稍后再试。"),
    ])
    assert evaluate_case(case, _run(ChatEvalRunner(honest), case)) == []

    fabricating = ScriptedAIClient([
        _msg(tool_calls=[_tc("c1", "get_stock_quote", '{"symbol": "600519"}')]),
        _msg(content="600519 现价 1712.5 元。"),  # 工具失败还报价 = 编造
    ])
    failures = evaluate_case(case, _run(ChatEvalRunner(fabricating), case))
    assert any("不应出现的内容" in f for f in failures)


def test_golden_set_size_and_whitelist():
    """golden set：规模 ≥ 30 条，且所有 expected_tools 都在白名单内"""
    assert len(CHAT_CASES) + len(STRUCTURED_CASES) >= 30
    ids = [c.id for c in CHAT_CASES] + [c.id for c in STRUCTURED_CASES]
    assert len(ids) == len(set(ids)), "用例 id 不得重复"
    for case in CHAT_CASES:
        for name in case.expected_tools:
            assert name in TOOL_WHITELIST, f"{case.id} 期望了白名单外的工具 {name}"


def test_judge_parse_and_mock_call():
    """judge：mock 客户端端到端评分，容忍代码围栏输出"""

    class FakeJudgeClient:
        async def chat(self, system_prompt, user_content, temperature=0.0):
            assert "评审员" in system_prompt
            assert "600519" in user_content
            return '```json\n{"relevance": 5, "groundedness": 4, "clarity": 5, "comment": "有据且清晰"}\n```'

    config = JudgeConfig(base_url="http://mock", api_key="mock", model="mock-judge")
    judge = LLMJudge(config, client=FakeJudgeClient())
    score = asyncio.run(judge.judge("600519 多少钱", ["实时行情：价格 1712.5"], "现价 1712.5"))
    assert isinstance(score, JudgeScore)
    assert (score.relevance, score.groundedness, score.clarity) == (5, 4, 5)
    assert abs(score.mean - 14 / 3) < 1e-9


def test_judge_parse_rejects_bad_output():
    """judge：非法输出（非 JSON / 缺维度 / 越界分值）解析行为正确"""
    with pytest.raises(ValueError):
        LLMJudge.parse_score("我觉得挺好的")
    with pytest.raises(ValueError):
        LLMJudge.parse_score('{"relevance": 5}')
    # 越界分值被夹到 1-5
    score = LLMJudge.parse_score(json.dumps({"relevance": 9, "groundedness": 0, "clarity": 3}))
    assert (score.relevance, score.groundedness, score.clarity) == (5, 1, 3)


def test_judge_config_from_env(monkeypatch):
    """judge：配置只从环境变量读取，缺任一项即返回 None（不读数据库）"""
    for key in ("EVAL_JUDGE_BASE_URL", "EVAL_JUDGE_API_KEY", "EVAL_JUDGE_MODEL"):
        monkeypatch.delenv(key, raising=False)
    assert JudgeConfig.from_env() is None

    monkeypatch.setenv("EVAL_JUDGE_BASE_URL", "http://judge")
    monkeypatch.setenv("EVAL_JUDGE_API_KEY", "k")
    assert JudgeConfig.from_env() is None  # 还缺 model
    monkeypatch.setenv("EVAL_JUDGE_MODEL", "judge-model")
    config = JudgeConfig.from_env()
    assert config is not None
    assert config.temperature == 0.0
