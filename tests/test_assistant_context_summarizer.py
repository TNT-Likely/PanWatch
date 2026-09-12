import asyncio

from pan_agent import ContextCompressionMode, ModelMessage


class _FakeClient:
    def __init__(self, result):
        self.result = result
        self.calls = []

    async def chat_multi(self, messages, temperature=0.4):
        self.calls.append((messages, temperature))
        return self.result


def test_failover_context_summarizer_returns_structured_summary():
    from src.modules.assistant.context_summarizer import FailoverContextSummarizer

    client = _FakeClient(
        '{"goal":["分析持仓"],"constraints":["不要修改数据"],'
        '"decisions":["保留现金仓位"],"facts":["现金 50%"],'
        '"current_state":"等待下一步","open_items":["补充风险说明"],'
        '"tool_findings":["持仓已读取"]}'
    )
    summary = asyncio.run(
        FailoverContextSummarizer(client, temperature=0.2).summarize(
            [ModelMessage(role="user", content="分析持仓")],
            mode=ContextCompressionMode.BALANCED,
        )
    )

    assert summary.goal == ["分析持仓"]
    assert client.calls[0][1] == 0.2
    assert "JSON" in client.calls[0][0][0]["content"]


def test_failover_context_summarizer_accepts_fenced_json():
    from src.modules.assistant.context_summarizer import FailoverContextSummarizer

    client = _FakeClient('```json\n{"goal":["目标"]}\n```')
    summary = asyncio.run(
        FailoverContextSummarizer(client).summarize(
            [ModelMessage(role="user", content="目标")],
            mode=ContextCompressionMode.HANDOFF,
        )
    )

    assert summary.goal == ["目标"]
