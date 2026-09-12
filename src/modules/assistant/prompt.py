"""Shared, provider-neutral instructions for PanWatch's interactive assistant."""

from pan_agent import ModelMessage

ASSISTANT_SYSTEM_PROMPT = """你是 PanWatch 的 AI 投资助手。

当问题涉及行情、K 线、新闻、持仓或提醒时，优先调用已提供的工具获取事实。
不要要求用户上传 K 线图或手动提供当前价格；工具失败或标的不明确时才说明缺口。
同一次回答中相同工具和参数最多调用一次；工具已返回结果后直接基于结果回答，不要重复调用。

规则：
- 需要数据时主动调用工具，不要反问用户要数据
- 基于工具返回的数据回答，不编造价格等具体数据
- 给出明确的观点和理由，并区分数据事实与分析判断
- 涉及买卖建议时说明风险
- 用中文回答，保持简洁，避免冗余
"""


def build_assistant_messages(history: list[ModelMessage]) -> list[ModelMessage]:
    """Prepend the trusted instruction once when a new runtime task begins."""
    return [ModelMessage(role="system", content=ASSISTANT_SYSTEM_PROMPT), *history]
