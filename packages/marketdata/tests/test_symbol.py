from marketdata.symbol import Market, Symbol


def test_parse_detects_market():
    assert Symbol.parse("2330").market == Market.TW
    assert Symbol.parse("00878", "TW").market == Market.TW
    assert Symbol.parse("006208", "TW").market == Market.TW
    assert Symbol.parse("AAPL").market == Market.US


def test_parse_respects_explicit_market():
    assert Symbol.parse("00700", "HK").market == Market.HK
    assert Symbol.parse("600519", "CN").code == "600519"


def test_to_tencent():
    assert Symbol.parse("600519", "CN").to_tencent() == "sh600519"
    assert Symbol.parse("000001", "CN").to_tencent() == "sz000001"
    assert Symbol.parse("920001", "CN").to_tencent() == "bj920001"
    assert Symbol.parse("00700", "HK").to_tencent() == "hk00700"
    assert Symbol.parse("AAPL").to_tencent() == "usAAPL"


def test_to_yfinance():
    assert Symbol.parse("2330").to_yfinance() == "2330.TW"
    assert Symbol.parse("TAIEX", "TW").to_yfinance() == "^TWII"
    assert Symbol.parse("00700", "HK").to_yfinance() == "0700.HK"
    assert Symbol.parse("AAPL").to_yfinance() == "AAPL"
