"""Agent 过程评测框架：用例结构、运行器与规则断言引擎。

用例 = 固定输入（问题 + mock 工具数据）→ 规则断言：
- 工具选择正确（该调的调了、不该调的没调、闲聊不调）；
- 工具参数正确；
- 动作在白名单内（只允许 CHAT_TOOLS 注册的只读工具）；
- 答案引用了工具结果（有据性：mock 数据里的关键值必须出现在答案中）；
- 工具失败时优雅降级（不编造无据数值）。

规则断言优先；语义维度（相关性/清晰度）由 judge.py 的 LLM-as-judge 补充。
每个线上 bad case 修复后应固化为一条新用例（加进 cases/chat_cases.py）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from src.web.api.chat import CHAT_TOOLS, SYSTEM_PROMPT

# 动作白名单：chat agent 只允许调用这些只读工具
TOOL_WHITELIST = {t["function"]["name"] for t in CHAT_TOOLS}
MAX_TOOL_ROUNDS = 5

# 用例未提供某工具 mock 数据时的默认返回（模拟工具失败）
DEFAULT_TOOL_MISSING = "工具执行出错: eval 用例未提供该工具的 mock 数据"


@dataclass
class ChatEvalCase:
    """一条 chat 工具循环评测用例。"""

    id: str
    question: str
    # 工具名 → mock 返回文本（工具失败场景直接给"工具执行出错: ..."文案）
    tool_data: dict[str, str] = field(default_factory=dict)
    # 必须调用的工具（子集断言，不要求顺序）
    expected_tools: tuple[str, ...] = ()
    # 明确不应调用的工具
    forbidden_tools: tuple[str, ...] = ()
    # 闲聊/概念题：完全不应调用任何工具
    expect_no_tools: bool = False
    # 工具名 → {参数名: 期望值或校验函数}；同名多次调用时任一命中即通过
    param_checks: dict[str, dict] = field(default_factory=dict)
    # 有据性：答案必须包含的关键值（全部命中才通过）
    answer_must_contain: tuple[str, ...] = ()
    # 答案必须包含其中任意一个（如失败场景的"失败/无法/未能"类表述）
    answer_must_contain_any: tuple[str, ...] = ()
    # 答案不得包含（如工具失败时不得出现编造的具体数值）
    answer_must_not_contain: tuple[str, ...] = ()
    notes: str = ""


@dataclass
class ChatEvalResult:
    """一次用例运行的过程记录。"""

    case_id: str
    tool_calls: list[tuple[str, dict]] = field(default_factory=list)
    answer: str = ""
    error: str = ""


class ChatEvalRunner:
    """驱动 chat 工具循环跑一条评测用例（工具执行被 mock 数据替代）。

    ai_client 需实现 `chat_with_tools(messages, tools, temperature) -> message`
    （与 src.core.ai_client.AIClient 一致）：
    - make eval 时注入真实 AIClient（配置从环境变量读取，见 run_eval.py）；
    - 单测里注入脚本化的假客户端，不发任何真实请求。
    """

    def __init__(self, ai_client, temperature: float = 0.0):
        self.ai_client = ai_client
        # 评测用低温，尽量减少非确定性
        self.temperature = temperature

    async def run_case(self, case: ChatEvalCase) -> ChatEvalResult:
        result = ChatEvalResult(case_id=case.id)
        messages: list[dict] = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": case.question},
        ]
        try:
            for _round in range(MAX_TOOL_ROUNDS):
                msg = await self.ai_client.chat_with_tools(
                    messages, tools=CHAT_TOOLS, temperature=self.temperature
                )
                tool_calls = getattr(msg, "tool_calls", None)
                if not tool_calls:
                    result.answer = getattr(msg, "content", "") or ""
                    break

                messages.append({
                    "role": "assistant",
                    "content": getattr(msg, "content", None),
                    "tool_calls": [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.function.name,
                                "arguments": tc.function.arguments,
                            },
                        }
                        for tc in tool_calls
                    ],
                })
                for tc in tool_calls:
                    try:
                        args = json.loads(tc.function.arguments) if tc.function.arguments else {}
                    except json.JSONDecodeError:
                        args = {}
                    result.tool_calls.append((tc.function.name, args))
                    tool_result = case.tool_data.get(tc.function.name, DEFAULT_TOOL_MISSING)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.id,
                        "content": tool_result,
                    })
            else:
                result.error = "超过最大工具轮次仍未给出回答"
        except Exception as e:  # noqa: BLE001 - 评测记录任何运行异常
            result.error = f"运行异常: {e}"
        return result


def _param_match(actual, expected) -> bool:
    """参数断言：expected 可为期望值或校验函数。"""
    if callable(expected):
        try:
            return bool(expected(actual))
        except Exception:
            return False
    return str(actual or "").strip() == str(expected)


def evaluate_case(case: ChatEvalCase, result: ChatEvalResult) -> list[str]:
    """对一次运行做规则断言，返回失败原因列表（空列表即通过）。"""
    failures: list[str] = []
    if result.error:
        failures.append(result.error)

    called = [name for name, _ in result.tool_calls]
    called_set = set(called)

    # 1) 动作白名单：调用了未注册的工具直接失败
    for name in sorted(called_set - TOOL_WHITELIST):
        failures.append(f"调用了白名单外的工具: {name}")

    # 2) 工具选择
    if case.expect_no_tools and called:
        failures.append(f"不该调用工具却调用了: {called}")
    for name in case.expected_tools:
        if name not in called_set:
            failures.append(f"缺少必需的工具调用: {name}")
    for name in case.forbidden_tools:
        if name in called_set:
            failures.append(f"调用了不该调用的工具: {name}")

    # 3) 工具参数
    for tool_name, expects in (case.param_checks or {}).items():
        calls = [args for name, args in result.tool_calls if name == tool_name]
        if not calls:
            continue  # 缺调用已在上面报过
        matched = any(
            all(_param_match(args.get(k), v) for k, v in expects.items())
            for args in calls
        )
        if not matched:
            expect_desc = {k: (v if not callable(v) else "<校验函数>") for k, v in expects.items()}
            failures.append(f"{tool_name} 参数不符合预期 {expect_desc}，实际 {calls}")

    # 4) 有据性 / 内容约束
    answer = result.answer or ""
    for token in case.answer_must_contain:
        if token not in answer:
            failures.append(f"答案缺少工具结果引用: {token!r}")
    if case.answer_must_contain_any and not any(
        token in answer for token in case.answer_must_contain_any
    ):
        failures.append(f"答案未包含任一预期表述: {case.answer_must_contain_any}")
    for token in case.answer_must_not_contain:
        if token in answer:
            failures.append(f"答案包含不应出现的内容: {token!r}")

    return failures
