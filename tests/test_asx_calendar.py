from datetime import datetime
from zoneinfo import ZoneInfo

from src.platform.scheduling.trading_calendar import market_status

SYD = ZoneInfo("Australia/Sydney")


def test_asx_normal_tuesday_trading():
    assert market_status("AU", datetime(2026, 10, 13, 11, 0, tzinfo=SYD)) == "trading"


def test_asx_pre_open_not_trading():
    assert market_status("AU", datetime(2026, 10, 13, 9, 0, tzinfo=SYD)) != "trading"


def test_asx_weekend_closed():
    assert market_status("AU", datetime(2026, 10, 17, 11, 0, tzinfo=SYD)) != "trading"


def test_asx_christmas_day_closed():
    assert market_status("AU", datetime(2026, 12, 25, 11, 0, tzinfo=SYD)) != "trading"


def test_asx_boxing_day_observed_closed():
    # 2026-12-26 是周六,补休落在周一 12-28
    assert market_status("AU", datetime(2026, 12, 28, 11, 0, tzinfo=SYD)) != "trading"


def test_asx_christmas_eve_early_close():
    assert market_status("AU", datetime(2026, 12, 24, 13, 0, tzinfo=SYD)) == "trading"
    assert market_status("AU", datetime(2026, 12, 24, 15, 0, tzinfo=SYD)) != "trading"
