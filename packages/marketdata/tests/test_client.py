from marketdata.client import MarketData
from marketdata.ports import SourceConfig
from marketdata.types import Quote


class _OneVendor:
    name = "fake"
    supports_markets: set = set()

    def fetch(self, symbols, config):
        return [Quote(symbol=s.code, market=s.market.value, current_price=1.0) for s in symbols]


class _Cfg:
    def sources_for(self, datatype, market):
        return [SourceConfig(vendor="fake", priority=1)]


def _md():
    md = MarketData(config=_Cfg())
    md._quote_engine.vendors = {"fake": _OneVendor()}   # 注入假 vendor(测试缝)
    return md


def test_quotes_returns_typed():
    out = _md().quotes(["600519"], market="CN")
    assert len(out) == 1 and isinstance(out[0], Quote) and out[0].symbol == "600519"


def test_quotes_groups_mixed_markets():
    out = _md().quotes(["600519", "00700", "AAPL"])   # 自动识别 → 3 个市场
    assert {(q.symbol, q.market) for q in out} == {("600519", "CN"), ("00700", "HK"), ("AAPL", "US")}


def test_health_after_fetch():
    md = _md()
    md.quotes(["600519"], market="CN")
    assert md.health()["fake"]["success_rate"] == 1.0
