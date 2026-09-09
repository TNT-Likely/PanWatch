"""Regression tests for platform facade ownership during the migration."""


def test_legacy_ai_and_sse_paths_reexport_platform_objects():
    from src.core.ai_failover import FailoverAIClient
    from src.core.sse import SSEStream
    from src.platform.ai.failover import FailoverAIClient as PlatformFailoverAIClient
    from src.platform.events.sse import SSEStream as PlatformSSEStream

    assert FailoverAIClient is PlatformFailoverAIClient
    assert SSEStream is PlatformSSEStream


def test_legacy_marketdata_notifications_and_scheduler_paths_reexport_platform_objects():
    from src.core.marketdata_client import get_market_data
    from src.core.notifier import NotifierManager
    from src.core.scheduler import AgentScheduler
    from src.platform.marketdata.client import get_market_data as platform_market_data
    from src.platform.notifications.notifier import NotifierManager as PlatformNotifierManager
    from src.platform.scheduling.scheduler import AgentScheduler as PlatformAgentScheduler

    assert get_market_data is platform_market_data
    assert NotifierManager is PlatformNotifierManager
    assert AgentScheduler is PlatformAgentScheduler
