#!/usr/bin/env python3
"""Agent 过程评测入口（make eval）。

跑两组用例：
1. structured_output 解析（纯规则，无需模型，永远执行）；
2. chat 工具循环（需要真实模型）——配置**只从环境变量读取**：
   EVAL_AI_BASE_URL / EVAL_AI_API_KEY / EVAL_AI_MODEL
   （不读用户数据库里的 AI 服务配置；未配置则跳过并提示）。

可选 --judge：对 chat 用例的答案追加 LLM-as-judge 语义评分
（需 EVAL_JUDGE_BASE_URL / EVAL_JUDGE_API_KEY / EVAL_JUDGE_MODEL）。

门禁用法：prompts/*.txt 或工具 schema 变更时跑本脚本；
通过率低于阈值（EVAL_PASS_THRESHOLD，默认 0.9）时退出码非 0，阻断提交。

示例：
    make eval                                   # 只跑规则用例（未配模型时）
    EVAL_AI_BASE_URL=... EVAL_AI_API_KEY=... EVAL_AI_MODEL=... make eval
    ... make eval EVAL_ARGS="--judge --only quote-1"
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

# 支持 `python tests/eval/run_eval.py` 直跑
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tests.eval.cases.chat_cases import CHAT_CASES  # noqa: E402
from tests.eval.cases.structured_cases import (  # noqa: E402
    STRUCTURED_CASES,
    check_structured_case,
)
from tests.eval.framework import ChatEvalRunner, evaluate_case  # noqa: E402
from tests.eval.judge import JudgeConfig, LLMJudge  # noqa: E402


def _eval_ai_config() -> tuple[str, str, str] | None:
    """chat 用例的被测模型配置（仅环境变量，缺任一即跳过）。"""
    base_url = os.environ.get("EVAL_AI_BASE_URL", "").strip()
    api_key = os.environ.get("EVAL_AI_API_KEY", "").strip()
    model = os.environ.get("EVAL_AI_MODEL", "").strip()
    if not (base_url and api_key and model):
        return None
    return base_url, api_key, model


def run_structured(only: str | None) -> tuple[int, int]:
    """跑结构化解析用例，返回 (通过数, 总数)。"""
    passed = 0
    cases = [c for c in STRUCTURED_CASES if not only or c.id == only]
    print(f"\n=== structured_output 解析用例（{len(cases)} 条，纯规则）===")
    for case in cases:
        failures = check_structured_case(case)
        if failures:
            print(f"  [FAIL] {case.id}: {'; '.join(failures)}")
        else:
            passed += 1
            print(f"  [PASS] {case.id}")
    return passed, len(cases)


async def run_chat(only: str | None, use_judge: bool) -> tuple[int, int]:
    """跑 chat 工具循环用例，返回 (通过数, 总数)。未配模型时返回 (0, 0)。"""
    cases = [c for c in CHAT_CASES if not only or c.id == only]
    config = _eval_ai_config()
    if config is None:
        print(
            f"\n=== chat 工具循环用例（{len(cases)} 条）：跳过 ===\n"
            "  需要环境变量 EVAL_AI_BASE_URL / EVAL_AI_API_KEY / EVAL_AI_MODEL\n"
            "  （只从环境变量读取，不使用数据库里的 AI 服务配置）"
        )
        return 0, 0

    base_url, api_key, model = config
    from src.core.ai_client import AIClient

    runner = ChatEvalRunner(AIClient(base_url=base_url, api_key=api_key, model=model))

    judge: LLMJudge | None = None
    if use_judge:
        judge_config = JudgeConfig.from_env()
        if judge_config is None:
            print("  [WARN] --judge 需要 EVAL_JUDGE_* 环境变量，本次跳过 judge 评分")
        else:
            judge = LLMJudge(judge_config)

    passed = 0
    print(f"\n=== chat 工具循环用例（{len(cases)} 条，模型: {model}）===")
    for case in cases:
        result = await runner.run_case(case)
        failures = evaluate_case(case, result)
        if failures:
            print(f"  [FAIL] {case.id}: {'; '.join(failures)}")
        else:
            passed += 1
            print(f"  [PASS] {case.id}")
        if judge is not None:
            try:
                score = await judge.judge(
                    case.question, list(case.tool_data.values()), result.answer
                )
                print(
                    f"         judge: 相关性{score.relevance} 有据性{score.groundedness} "
                    f"清晰度{score.clarity} 均分{score.mean:.1f} — {score.comment}"
                )
            except Exception as e:  # noqa: BLE001
                print(f"         judge 评分失败: {e}")
    return passed, len(cases)


def main() -> int:
    parser = argparse.ArgumentParser(description="Agent 过程评测")
    parser.add_argument("--judge", action="store_true", help="对 chat 用例追加 LLM-as-judge 评分")
    parser.add_argument("--only", default="", help="只跑指定 id 的用例")
    args = parser.parse_args()
    only = args.only or None

    s_passed, s_total = run_structured(only)
    c_passed, c_total = asyncio.run(run_chat(only, args.judge))

    total = s_total + c_total
    passed = s_passed + c_passed
    if total == 0:
        print("\n没有匹配的用例")
        return 1

    rate = passed / total
    threshold = float(os.environ.get("EVAL_PASS_THRESHOLD", "0.9"))
    print(f"\n=== 汇总 ===\n  通过 {passed}/{total}（{rate:.0%}），阈值 {threshold:.0%}")
    if rate < threshold:
        print("  ✗ 低于阈值，评测不通过")
        return 1
    print("  ✓ 评测通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
