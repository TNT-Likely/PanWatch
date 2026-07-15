from marketdata.symbol import Market, Symbol


def test_parse_detects_market():
    assert Symbol.parse("600519").market == Market.CN
    assert Symbol.parse("000001").market == Market.CN
    assert Symbol.parse("00700").market == Market.HK
    assert Symbol.parse("AAPL").market == Market.US


def test_parse_respects_explicit_market():
    assert Symbol.parse("00700", "HK").market == Market.HK
    assert Symbol.parse("600519", "CN").code == "600519"


def test_to_tencent():
    assert Symbol.parse("600519").to_tencent() == "sh600519"
    assert Symbol.parse("000001").to_tencent() == "sz000001"
    assert Symbol.parse("920001").to_tencent() == "bj920001"
    assert Symbol.parse("00700", "HK").to_tencent() == "hk00700"
    assert Symbol.parse("AAPL").to_tencent() == "usAAPL"


def test_to_yfinance():
    assert Symbol.parse("00700", "HK").to_yfinance() == "0700.HK"
    assert Symbol.parse("AAPL").to_yfinance() == "AAPL"
