"""Actual implementation ownership during the legacy-path migration."""


def test_automation_module_owns_legacy_agent_implementations():
    from src.modules.automation import daily_report, intraday_monitor, premarket_outlook

    assert daily_report.__file__.replace("\\", "/").endswith("/src/modules/automation/daily_report.py")
    assert intraday_monitor.__file__.replace("\\", "/").endswith("/src/modules/automation/intraday_monitor.py")
    assert premarket_outlook.__file__.replace("\\", "/").endswith("/src/modules/automation/premarket_outlook.py")
