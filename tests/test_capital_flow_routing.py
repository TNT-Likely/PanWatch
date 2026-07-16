"""资金流取数 flag 门控路由测试"""
import src.collectors.capital_flow_collector as cf
from src.models.market import MarketCode


def test_get_capital_flow_flag_on_uses_marketdata(monkeypatch):
    """flag 开:走 marketdata 包的 capital_flow,转换为 PanWatch CapitalFlow"""
    from marketdata.types import CapitalFlow as MdCF
    monkeypatch.setattr(cf, "_use_marketdata_cf", lambda: True)

    class _MD:
        def capital_flow(self, symbol, *, market="CN"):
            return MdCF(symbol=symbol, name="X", main_net_inflow=5000.0, main_net_inflow_pct=3.2,
                        super_net_inflow=1000.0, big_net_inflow=1500.0, mid_net_inflow=2000.0,
                        small_net_inflow=500.0, main_net_5d=None)

    monkeypatch.setattr(cf, "get_market_data", lambda: _MD())
    out = cf.CapitalFlowCollector(MarketCode.CN).get_capital_flow("600519")
    assert out is not None and isinstance(out, cf.CapitalFlow)
    assert out.main_net_inflow == 5000.0 and out.symbol == "600519"


def test_get_capital_flow_flag_off_unchanged(monkeypatch):
    """flag 关:原 market_get 取数/解析路径保持不变"""
    monkeypatch.setattr(cf, "_use_marketdata_cf", lambda: False)
    monkeypatch.setattr(cf, "market_get", lambda *a, **k: {"data": {"code": "600519", "name": "X", "klines": [
        "2026-07-01,5000,500,2000,1500,1000,3.2,0,0,0,0,0,0,0,0"]}})
    out = cf.CapitalFlowCollector(MarketCode.CN).get_capital_flow("600519")
    assert out is not None and out.symbol == "600519"  # 走原 market_get 解析路径
