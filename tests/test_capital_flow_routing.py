"""资金流取数路由测试(经 marketdata 包统一接入)"""
import src.collectors.capital_flow_collector as cf
from src.models.market import MarketCode


def test_get_capital_flow_uses_marketdata(monkeypatch):
    """走 marketdata 包的 capital_flow,转换为 PanWatch CapitalFlow"""
    from marketdata.types import CapitalFlow as MdCF

    class _MD:
        def capital_flow(self, symbol, *, market="CN"):
            return MdCF(symbol=symbol, name="X", main_net_inflow=5000.0, main_net_inflow_pct=3.2,
                        super_net_inflow=1000.0, big_net_inflow=1500.0, mid_net_inflow=2000.0,
                        small_net_inflow=500.0, main_net_5d=None)

    monkeypatch.setattr(cf, "get_market_data", lambda: _MD())
    out = cf.CapitalFlowCollector(MarketCode.CN).get_capital_flow("600519")
    assert out is not None and isinstance(out, cf.CapitalFlow)
    assert out.main_net_inflow == 5000.0 and out.symbol == "600519"
