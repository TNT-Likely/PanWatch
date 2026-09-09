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
