"""加仓快速评估接口:服务端口径算摊薄成本 + AI 给 适合/谨慎/不适合 结论。"""

from __future__ import annotations

import asyncio

import pytest
from pydantic import ValidationError

from src.web.api import insights


class _FakeAIClient:
    def __init__(self, reply: str):
        self._reply = reply

    async def chat(self, system_prompt, user_content, temperature=0.3):
        return self._reply


def _run_eval(monkeypatch, db, req, reply="结论: 适合\n理由:\n- 摊薄明显\n风险: 大盘转弱"):
    monkeypatch.setattr(insights, "_get_ai_client", lambda db, mid=None: _FakeAIClient(reply))

    async def _empty(*a, **k):
        return ""

    monkeypatch.setattr(insights, "_fetch_realtime_context", _empty)
    monkeypatch.setattr(insights, "_fetch_fundamental_context", _empty)
    monkeypatch.setattr(insights, "_fetch_technical_context", _empty)
    monkeypatch.setattr(insights, "_fetch_message_context", _empty)
    return asyncio.run(insights.add_position_eval(req, db))


def test_add_position_eval_computes_diluted_cost(monkeypatch, db):
    """加仓 100@8 到 100@10 的持仓:摊薄后成本 9.0(↓10%),并带 AI 结论。"""
    req = insights.AddPositionEvalRequest(
        symbol="600519", market="CN",
        current_quantity=100, current_cost=10,
        add_quantity=100, add_price=8,
    )
    res = _run_eval(monkeypatch, db, req)
    assert res["new_cost"] == 9.0
    assert round(res["dilute_pct"], 1) == 10.0
    assert res["total_quantity"] == 200
    assert res["action"] == "加仓"
    assert res["verdict"] == "适合"


def test_build_position_when_empty(monkeypatch, db):
    """空仓时为建仓:成本=加仓价,摊薄为 0。"""
    req = insights.AddPositionEvalRequest(
        symbol="600519", market="CN",
        current_quantity=0, current_cost=0,
        add_quantity=100, add_price=8,
    )
    res = _run_eval(monkeypatch, db, req)
    assert res["new_cost"] == 8.0
    assert res["dilute_pct"] == 0
    assert res["action"] == "建仓"


def test_verdict_parse_not_confused_by_substring():
    """'不适合' 含 '适合',解析必须先长后短,不能误判为 '适合'。"""
    assert insights._parse_verdict("结论: 不适合\n理由: ...") == "不适合"
    assert insights._parse_verdict("结论: 谨慎") == "谨慎"
    assert insights._parse_verdict("结论: 适合") == "适合"
    assert insights._parse_verdict("看不出来") == "未知"


def test_zero_add_quantity_rejected_by_schema():
    """加仓股数必须 > 0(schema 层拦截)。"""
    with pytest.raises(ValidationError):
        insights.AddPositionEvalRequest(
            symbol="600519", add_quantity=0, add_price=8,
        )
