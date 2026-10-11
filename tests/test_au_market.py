import re

from src.platform.marketdata.models import MARKETS, MarketCode


def test_au_market_registered():
    m = MARKETS[MarketCode.AU]
    assert m.timezone == "Australia/Sydney"
    assert len(m.sessions) == 1
    assert m.sessions[0].start.hour == 10
    assert m.sessions[0].end.hour == 16
    assert re.fullmatch(m.symbol_pattern, "VAS")
    assert re.fullmatch(m.symbol_pattern, "BHP")
    assert not re.fullmatch(m.symbol_pattern, "600519")


def test_au_get_tz():
    from zoneinfo import ZoneInfo
    assert MARKETS[MarketCode.AU].get_tz() == ZoneInfo("Australia/Sydney")
