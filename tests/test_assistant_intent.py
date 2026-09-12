"""Tests for the host-owned assistant action intent gate."""

import pytest

from src.modules.assistant.intent import is_write_intent


@pytest.mark.parametrize(
    "content",
    [
        "请把药明生物的提醒目标价改成44元",
        "帮我创建一个跌破40的价格提醒",
        "删除提醒 #4",
        "把这条提醒重命名为低位观察",
    ],
)
def test_explicit_price_alert_actions_are_write_intents(content):
    assert is_write_intent(content) is True


@pytest.mark.parametrize(
    "content",
    [
        "怎么修改价格提醒？",
        "分析一下提醒设置",
        "提醒为什么没有触发？",
        "查询我的价格提醒",
    ],
)
def test_questions_and_reads_are_not_write_intents(content):
    assert is_write_intent(content) is False
