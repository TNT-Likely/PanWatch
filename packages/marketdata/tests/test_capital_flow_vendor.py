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


def test_sina_capital_flow_parses(monkeypatch):
    # 真实抓包样例(MoneyFlow.ssl_qsfx_zjlrqs,daima=sh600519,num=3),按 opendate 降序:
    # 该端点只提供「主力」netamount/ratioamount + 「超大单」r0_net,无大/中/小单细分。
    payload = (
        '[{"opendate":"2026-07-16","trade":"1258.9000","changeratio":"0.00626669",'
        '"turnover":"37.7872","netamount":"676276059.3300","ratioamount":"0.113842",'
        '"r0_net":"481207077.7000","r0_ratio":"0.08100468","r0x_ratio":"85.5763",'
        '"cnt_r0x_ratio":"2","cate_ra":"0.0633939","cate_na":"1416477159.5500"},'
        '{"opendate":"2026-07-15","trade":"1251.0000","changeratio":"0.0297313",'
        '"turnover":"57.2674","netamount":"1954209782.6700","ratioamount":"0.220106",'
        '"r0_net":"1907783618.5100","r0_ratio":"0.21487734","r0x_ratio":"82.1223",'
        '"cnt_r0x_ratio":"1","cate_ra":"0.163156","cate_na":"4627883391.8000"},'
        '{"opendate":"2026-07-14","trade":"1215.6100","changeratio":"0.00381506",'
        '"turnover":"34.4355","netamount":"-21918498.1300","ratioamount":"-0.00418575",'
        '"r0_net":"-10211324.2100","r0_ratio":"-0.00195004","r0x_ratio":"-27.0736",'
        '"cnt_r0x_ratio":"-1","cate_ra":"0.0115617","cate_na":"176699853.6300"}]'
    )
    monkeypatch.setattr(cfv, "market_get", lambda *a, **k: payload)
    out = cfv.SinaCapitalFlowVendor().fetch([Symbol.parse("600519")], {"days": 3})
    assert len(out) == 1 and isinstance(out[0], CapitalFlow)
    cf = out[0]
    assert cf.symbol == "600519" and cf.name == ""
    # 取最新一条(数组首条,opendate 降序):主力净额/占比 + 超大单净额
    assert cf.main_net_inflow == 676276059.33
    assert cf.main_net_inflow_pct == 0.113842
    assert cf.super_net_inflow == 481207077.70
    # 该端点无大/中/小单细分,与东财同形填 0.0
    assert cf.big_net_inflow == 0.0
    assert cf.mid_net_inflow == 0.0
    assert cf.small_net_inflow == 0.0
    # 5日主力净流入 = 最近5条(此处仅3条)主力净额之和
    assert cf.main_net_5d == 676276059.33 + 1954209782.67 + (-21918498.13)


def test_sina_capital_flow_empty(monkeypatch):
    monkeypatch.setattr(cfv, "market_get", lambda *a, **k: "")
    assert cfv.SinaCapitalFlowVendor().fetch([Symbol.parse("600519")], {}) == []


def test_sina_capital_flow_non_cn_market_returns_empty(monkeypatch):
    monkeypatch.setattr(cfv, "market_get", lambda *a, **k: (_ for _ in ()).throw(
        AssertionError("HK/US 不该发起新浪 CN 资金流请求")))
    assert cfv.SinaCapitalFlowVendor().fetch([Symbol.parse("00700", market="HK")], {}) == []
