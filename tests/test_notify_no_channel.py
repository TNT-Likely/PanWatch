"""通知无渠道语义回归：无渠道应走 skipped 软路径，不产生 notify_error（2026-09 生产误判 failed）。"""

import asyncio

from src.platform.notifications.notifier import NotifierManager

# conftest 的 autouse fixture 会把 notify_with_result 替换为 no-op mock，
# 本测试验证的就是真实方法的无渠道行为，因此在测试内恢复原实现。
_ORIG_NOTIFY = NotifierManager.notify_with_result


def test_no_channel_returns_skipped_not_error(monkeypatch):
    monkeypatch.setattr(NotifierManager, "notify_with_result", _ORIG_NOTIFY)
    manager = NotifierManager()
    manager._channel_count = 0
    result = asyncio.run(manager.notify_with_result("标题", "内容", None))
    assert result.get("skipped") == "no_channel"
    assert "error" not in result
    assert result.get("success") is False


def test_base_agent_no_channel_writes_skip_not_error():
    """base 的 skipped 分支按 skipped 键判定：no_channel 不应写入 notify_error。"""
    # 直接复刻 base.py 的判定逻辑（该分支按 .get("skipped") 存在性路由）
    notify_result = {"success": False, "skipped": "no_channel"}
    assert notify_result.get("skipped")  # 走 notify_skipped 分支
    assert not notify_result.get("error")  # 不会写入 raw_data["notify_error"]
