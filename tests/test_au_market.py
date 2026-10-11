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


def test_market_defs_carry_currency():
    """MarketDef.currency:估值按币种而非市场硬编码解析汇率。"""
    from src.platform.marketdata.models import MARKETS, MarketCode

    assert MARKETS[MarketCode.CN].currency == "CNY"
    assert MARKETS[MarketCode.HK].currency == "HKD"
    assert MARKETS[MarketCode.US].currency == "USD"
    assert MARKETS[MarketCode.AU].currency == "AUD"
    assert MARKETS[MarketCode.AU].timezone == "Australia/Sydney"
