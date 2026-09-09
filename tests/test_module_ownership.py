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


def test_reporting_and_administration_modules_own_their_implementations():
    from src.modules.administration import pat, selfcheck, stock_link, update_checker
    from src.modules.reporting import pdf_export

    for module in (pat, selfcheck, stock_link, update_checker):
        assert "/src/modules/administration/" in module.__file__.replace("\\", "/")
    assert pdf_export.__file__.replace("\\", "/").endswith("/src/modules/reporting/pdf_export.py")


def test_market_module_owns_market_domain_implementations():
    from src.modules.market import cn_symbol, data_collector, kline_context, news_ranker

    for module in (cn_symbol, data_collector, kline_context, news_ranker):
        assert "/src/modules/market/" in module.__file__.replace("\\", "/")


def test_assistant_and_automation_own_planning_and_suggestions():
    from src.modules.assistant import chat_planner
    from src.modules.automation import suggestion_pool

    assert chat_planner.__file__.replace("\\", "/").endswith("/src/modules/assistant/chat_planner.py")
    assert suggestion_pool.__file__.replace("\\", "/").endswith("/src/modules/automation/suggestion_pool.py")


def test_research_module_owns_evaluation_and_explanation_services():
    from src.modules.research import analysis_link, context_scheduler, prediction_outcome, signal_explain

    for module in (analysis_link, context_scheduler, prediction_outcome, signal_explain):
        assert "/src/modules/research/" in module.__file__.replace("\\", "/")


def test_portfolio_module_owns_benchmark_and_diagnostics():
    from src.modules.portfolio import portfolio_benchmark, portfolio_diagnostics

    assert portfolio_benchmark.__file__.replace("\\", "/").endswith("/src/modules/portfolio/portfolio_benchmark.py")
    assert portfolio_diagnostics.__file__.replace("\\", "/").endswith("/src/modules/portfolio/portfolio_diagnostics.py")


def test_market_and_strategy_own_alerting_and_event_gating():
    from src.modules.market import price_alert_engine, price_alert_scheduler
    from src.modules.strategy import intraday_event_gate

    assert price_alert_engine.__file__.replace("\\", "/").endswith("/src/modules/market/price_alert_engine.py")
    assert price_alert_scheduler.__file__.replace("\\", "/").endswith("/src/modules/market/price_alert_scheduler.py")
    assert intraday_event_gate.__file__.replace("\\", "/").endswith("/src/modules/strategy/intraday_event_gate.py")


def test_automation_and_platform_own_remaining_core_implementations():
    from src.modules.administration import doctor
    from src.modules.automation import agent_catalog, agent_prediction_evaluation, agent_runs
    from src.platform.notifications import notify_dedupe, notify_policy
    from src.platform.observability import log_context, otel
    from src.platform.persistence import json_safe, json_store
    from src.platform.scheduling import schedule_parser, scheduler_registry, timezone, trading_calendar

    for module in (doctor, agent_catalog, agent_prediction_evaluation, agent_runs):
        assert "/src/modules/" in module.__file__.replace("\\", "/")
    for module in (notify_dedupe, notify_policy, log_context, otel, json_safe, json_store,
                   schedule_parser, scheduler_registry, timezone, trading_calendar):
        assert "/src/platform/" in module.__file__.replace("\\", "/")
