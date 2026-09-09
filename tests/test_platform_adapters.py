"""Ownership checks for stable platform adapters."""


def test_platform_public_adapters_export_their_implementation_objects():
    from src.platform.ai.ai_failover import FailoverAIClient
    from src.platform.events.sse import SSEStream
    from src.platform.ai.failover import FailoverAIClient as PlatformFailoverAIClient
    from src.platform.events.sse import SSEStream as PlatformSSEStream

    assert FailoverAIClient is PlatformFailoverAIClient
    assert SSEStream is PlatformSSEStream


def test_platform_marketdata_and_notification_adapters_export_their_implementation_objects():
    from src.platform.marketdata.marketdata_client import get_market_data
    from src.platform.notifications.notifier import NotifierManager
    from src.modules.automation.agent_scheduler import AgentScheduler
    from src.platform.marketdata.client import get_market_data as platform_market_data
    from src.platform.notifications.notifier import NotifierManager as PlatformNotifierManager
    from src.modules.automation.agent_scheduler import AgentScheduler as PlatformAgentScheduler

    assert get_market_data is platform_market_data
    assert NotifierManager is PlatformNotifierManager
    assert AgentScheduler is PlatformAgentScheduler
