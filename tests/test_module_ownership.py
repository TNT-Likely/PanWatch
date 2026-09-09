"""Actual implementation ownership during the legacy-path migration."""


def test_automation_module_owns_legacy_agent_implementations():
    from src.modules.automation import daily_report, intraday_monitor, premarket_outlook

    assert daily_report.__file__.replace("\\", "/").endswith("/src/modules/automation/daily_report.py")
    assert intraday_monitor.__file__.replace("\\", "/").endswith("/src/modules/automation/intraday_monitor.py")
    assert premarket_outlook.__file__.replace("\\", "/").endswith("/src/modules/automation/premarket_outlook.py")


def test_strategy_module_owns_strategy_engine_and_candidates():
    from src.modules.strategy import entry_candidates, strategy_engine

    assert entry_candidates.__file__.replace("\\", "/").endswith("/src/modules/strategy/entry_candidates.py")
    assert strategy_engine.__file__.replace("\\", "/").endswith("/src/modules/strategy/strategy_engine.py")


def test_paper_trading_module_owns_execution_engine():
    from src.modules.paper_trading import paper_trading_engine

    assert paper_trading_engine.__file__.replace("\\", "/").endswith("/src/modules/paper_trading/paper_trading_engine.py")


def test_research_module_owns_analysis_history_and_context():
    from src.modules.research import analysis_history, context_builder, context_store

    assert analysis_history.__file__.replace("\\", "/").endswith("/src/modules/research/analysis_history.py")
    assert context_builder.__file__.replace("\\", "/").endswith("/src/modules/research/context_builder.py")
    assert context_store.__file__.replace("\\", "/").endswith("/src/modules/research/context_store.py")


def test_platform_owns_ai_sse_and_marketdata_implementations():
    from src.platform.ai import ai_client, ai_failover
    from src.platform.events import sse
    from src.platform.marketdata import marketdata_client

    assert ai_client.__file__.replace("\\", "/").endswith("/src/platform/ai/ai_client.py")
    assert ai_failover.__file__.replace("\\", "/").endswith("/src/platform/ai/ai_failover.py")
    assert sse.__file__.replace("\\", "/").endswith("/src/platform/events/sse.py")
    assert marketdata_client.__file__.replace("\\", "/").endswith("/src/platform/marketdata/marketdata_client.py")


def test_platform_owns_notification_and_scheduling_implementations():
    from src.platform.notifications import notifier
    from src.platform.scheduling import scheduler

    assert notifier.__file__.replace("\\", "/").endswith("/src/platform/notifications/notifier.py")
    assert scheduler.__file__.replace("\\", "/").endswith("/src/platform/scheduling/scheduler.py")
    assert notifier.NotifierManager.__module__ == "src.platform.notifications.notifier"
    assert scheduler.AgentScheduler.__module__ == "src.platform.scheduling.scheduler"
