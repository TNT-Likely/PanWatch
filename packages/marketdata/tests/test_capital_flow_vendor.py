import marketdata.vendors.capital_flow as cfv
from marketdata.symbol import Symbol
from marketdata.types import CapitalFlow


def test_capital_flow_parses(monkeypatch):
    # 东财 fflow daykline 逗号行(15列,与真实响应同形,对齐原实现字段索引):
    # 0:日期 1:主力净额 2:小单净额 3:中单净额 4:大单净额 5:超大单净额 6:主力占比
    # 7:小单占比 8:中单占比 9:大单占比 10:超大单占比 11:收盘价 12:涨跌幅 13:成交量 14:成交额
    payload = {"data": {"name": "贵州茅台", "klines": [
        "2026-06-30,1000,200,300,400,100,1.5,4,6,8,2,1600.0,1.0,20000,30000000",
        "2026-07-01,5000,1000,1500,2000,500,3.2,10,15,20,5,1700.5,3.2,50000,85000000",
    ]}}
    monkeypatch.setattr(cfv, "market_get", lambda *a, **k: payload)
    out = cfv.EastmoneyCapitalFlowVendor().fetch([Symbol.parse("600519")], {})
    assert len(out) == 1 and isinstance(out[0], CapitalFlow)
    cf = out[0]
    assert cf.symbol == "600519" and cf.name == "贵州茅台"
    # 末行(最新)取值:主力净额=parts[1]、主力占比=parts[6]
    assert cf.main_net_inflow == 5000.0 and cf.main_net_inflow_pct == 3.2
    # 超大/大/中/小单净额分别取 parts[5]/parts[4]/parts[3]/parts[2]
    assert cf.super_net_inflow == 500.0
    assert cf.big_net_inflow == 2000.0
    assert cf.mid_net_inflow == 1500.0
    assert cf.small_net_inflow == 1000.0
    # 5日主力净流入 = 最后5条(此处仅2条)主力净额之和
    assert cf.main_net_5d == 6000.0


def test_capital_flow_empty(monkeypatch):
    monkeypatch.setattr(cfv, "market_get", lambda *a, **k: {"data": {"klines": []}})
    assert cfv.EastmoneyCapitalFlowVendor().fetch([Symbol.parse("600519")], {}) == []
