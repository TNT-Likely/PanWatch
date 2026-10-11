from marketdata.symbol import Market, Symbol, _detect_market


def test_detect_au_suffix():
    assert _detect_market("VAS.AX") is Market.AU
    assert _detect_market("cba.ax") is Market.AU  # 接受小写,与其它市场一致先 upper


def test_bare_au_like_code_stays_us():
    assert _detect_market("CBA") is Market.US  # 无后缀无法区分,默认 US;显式市场可建 AU 股


def test_existing_detection_unchanged():
    assert _detect_market("600519") is Market.CN
    assert _detect_market("00700") is Market.HK
    assert _detect_market("AAPL") is Market.US


def test_to_yfinance_au():
    s = Symbol(market=Market.AU, code="VAS")
    assert s.to_yfinance() == "VAS.AX"
