"""服务启动时的调度任务注册契约。"""

from types import SimpleNamespace
from unittest.mock import Mock


def test_mcp_log_cleanup_is_registered_on_wrapped_apscheduler(monkeypatch):
    import server
    from src.modules.administration.api import mcp

    prune = Mock(name="prune_mcp_logs")
    monkeypatch.setattr(mcp, "prune_mcp_logs", prune)
    scheduler = SimpleNamespace(scheduler=Mock())

    server.register_mcp_log_cleanup(scheduler)

    scheduler.scheduler.add_job.assert_called_once_with(
        prune,
        "cron",
        hour=4,
        minute=0,
        id="mcp_log_retention",
        replace_existing=True,
    )
