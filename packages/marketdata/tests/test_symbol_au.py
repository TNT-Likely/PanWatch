"""AU 符号归一化:入口边界统一身份,.AX 恰好追加一次。"""
import pytest

from marketdata.symbol import Market, Symbol


@pytest.mark.parametrize("raw,market", [("VAS.AX", None), ("VAS", "AU"), ("vas.ax", None), ("vas", "au"), ("VAS.AX.AX", None)])
def test_au_inputs_normalize_to_one_identity(raw, market):
    """后缀/显式市场/大小写/双后缀输入都归一到 Symbol(AU, "VAS")。"""
    sym = Symbol.parse(raw, market)
    assert sym == Symbol(Market.AU, "VAS")
    assert sym.to_yfinance() == "VAS.AX"


def test_conflicting_market_and_suffix_rejected():
    """显式市场与 .AX 后缀冲突时在入口拒绝,而非静默改判。"""
    with pytest.raises(ValueError):
        Symbol.parse("VAS.AX", market="US")


def test_bare_tickers_stay_us():
    """裸代码仍按 US 处理(AAPL/CBA),保持既有行为。"""
    assert Symbol.parse("AAPL").market == Market.US
    assert Symbol.parse("CBA").market == Market.US
    assert Symbol.parse("AAPL").to_yfinance() == "AAPL"


def test_cn_hk_unchanged():
    """CN/HK 解析行为不变。"""
    assert Symbol.parse("600519").market == Market.CN
    assert Symbol.parse("00700").to_yfinance() == "0700.HK"
