"""AI 对话操作单元测试。"""

from datetime import datetime, timedelta, timezone

import pytest

from src.core.chat_actions import (
    _format_condition_label,
    _normalize_cn_quantity,
    cancel_pending_action,
    execute_pending_action,
    get_chat_action_permissions,
    propose_create_price_alert,
)
from src.web.models import AppSettings, ChatPendingAction, NotifyChannel, Stock


def test_get_chat_action_permissions_defaults(db):
    """默认应开启建提醒、关闭加减仓。"""
    perms = get_chat_action_permissions(db)
    assert perms["create_price_alert"] is True
    assert perms["add_position"] is False
    assert perms["reduce_position"] is False


def test_normalize_cn_quantity_requires_lot(db):
    """A 股数量须为 100 的整数倍。"""
    qty, err = _normalize_cn_quantity(150, "CN")
    assert qty == 0
    assert "100" in (err or "")


def test_propose_create_price_alert_requires_channel(db, monkeypatch):
    """未配置通知渠道时不应生成待确认操作。"""
    stock = Stock(symbol="600519", name="贵州茅台", market="CN")
    db.add(stock)
    db.commit()

    result, action_id = propose_create_price_alert(
        db,
        conversation_id=1,
        args={
            "symbol": "600519",
            "market": "CN",
            "condition_type": "price",
            "op": "<=",
            "value": 1500,
        },
    )
    assert action_id is None
    assert "通知渠道" in result


def test_propose_create_price_alert_success(db):
    """配置通知渠道后应成功生成待确认提醒。"""
    stock = Stock(symbol="600519", name="贵州茅台", market="CN")
    channel = NotifyChannel(name="测试", type="telegram", config={}, enabled=True)
    db.add(stock)
    db.add(channel)
    db.commit()

    result, action_id = propose_create_price_alert(
        db,
        conversation_id=1,
        args={
            "symbol": "600519",
            "market": "CN",
            "condition_type": "price",
            "op": "<=",
            "value": 1500,
        },
    )
    assert action_id is not None
    assert '"ok": true' in result or '"ok":true' in result.replace(" ", "")

    action = db.query(ChatPendingAction).filter(ChatPendingAction.id == action_id).first()
    assert action is not None
    assert action.action_type == "create_price_alert"
    assert action.status == "pending"


def test_execute_pending_action_creates_rule(db):
    """确认待操作后应写入价格提醒规则。"""
    stock = Stock(symbol="000001", name="平安银行", market="CN")
    channel = NotifyChannel(name="测试", type="telegram", config={}, enabled=True)
    db.add(stock)
    db.add(channel)
    db.flush()

    db.add(
        AppSettings(key="chat_action_create_alert", value="true", description="")
    )
    action = ChatPendingAction(
        id="test-action-1",
        conversation_id=1,
        action_type="create_price_alert",
        payload={
            "stock_id": stock.id,
            "name": "测试提醒",
            "condition_group": {"op": "and", "items": [{"type": "price", "op": "<=", "value": 10}]},
            "notify_channel_ids": [channel.id],
        },
        preview={"title": "测试"},
        status="pending",
        expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(hours=1),
    )
    db.add(action)
    db.commit()

    result = execute_pending_action(db, action)
    db.commit()

    assert result.get("rule_id")
    assert action.status == "confirmed"


def test_cancel_pending_action(db):
    """取消待确认操作应更新状态。"""
    action = ChatPendingAction(
        id="test-action-2",
        conversation_id=1,
        action_type="create_price_alert",
        payload={},
        preview={},
        status="pending",
    )
    db.add(action)
    db.commit()

    cancel_pending_action(db, action)
    assert action.status == "cancelled"


def test_format_condition_label():
    """条件文案格式化。"""
    assert "1500" in _format_condition_label("price", "<=", 1500)
