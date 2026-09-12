"""Run a small local context-engineering demo without a model or database."""

from __future__ import annotations

import asyncio

from pan_agent import ContextBudget, ContextEngine, ModelMessage


async def main() -> None:
    messages = [
        ModelMessage(role="system", content="你是一个严谨的研究助手。"),
        ModelMessage(role="user", content="目标是跟踪组合风险，不能修改提醒。" + " 事实" * 120),
        ModelMessage(role="assistant", content="已记录目标，下一步需要补充持仓数据。" + " 结果" * 120),
        ModelMessage(role="user", content="请继续分析当前状态。"),
    ]
    result = await ContextEngine().prepare(
        messages,
        budget=ContextBudget(
            max_tokens=400,
            soft_limit_tokens=128,
            hard_limit_tokens=256,
            keep_recent_messages=1,
        ),
    )
    print("before:", result.usage_before.model_dump(mode="json"))
    print("after:", result.usage_after.model_dump(mode="json"))
    print("compressed:", result.compressed)
    print("summary:", result.summary.model_dump(mode="json") if result.summary else None)
    print("prepared_messages:", len(result.messages))


if __name__ == "__main__":
    asyncio.run(main())
