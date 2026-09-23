"""AkshareMacroVendor + macro Engine/client 测试(全部离线,monkeypatch akshare)。"""

import sys
import types

import pandas as pd
import pytest

from marketdata.cache import TTLCache
from marketdata.defaults import InMemoryMetricsSink, StaticConfigProvider
from marketdata.engine import Engine
from marketdata.ports import SourceConfig
from marketdata.types import MacroIndicator, Request
from marketdata.vendors.macro import AkshareMacroVendor


def _df(**cols) -> pd.DataFrame:
    return pd.DataFrame(cols)


def _ok_module() -> types.ModuleType:
    """按本机 akshare(1.18.x)实测列结构构造的最小桩。"""
    mod = types.ModuleType("akshare")

    mod.macro_china_pmi = lambda: _df(
        **{"月份": ["2026年07月", "2026年08月"],
           "制造业-指数": [49.3, 49.8],
           "制造业-同比增长": [-0.2, -0.1],
           "非制造业-指数": [50.1, 50.3],
           "非制造业-同比增长": [0.1, 0.2]}
    )
    mod.macro_china_cpi = lambda: _df(
        **{"月份": ["2026年08月"], "全国-同比增长": [0.3], "全国-当月": [101.2]}
    )
    mod.macro_china_ppi = lambda: _df(
        **{"月份": ["2026年08月"], "当月": [97.1], "当月同比增长": [-2.9], "累计": [-2.5]}
    )
    mod.macro_china_lpr = lambda: _df(
        **{"TRADE_DATE": ["2026-08-20", "2026-09-21"], "LPR1Y": [3.0, 2.95], "LPR5Y": [3.5, 3.45]}
    )
    mod.macro_china_shrzgm = lambda: _df(
        **{"月份": ["2026年07月", "2026年08月"], "社会融资规模增量": [21000.0, 28300.0]}
    )
    mod.macro_china_money_supply = lambda: _df(
        **{"月份": ["2026年08月"],
           "货币和准货币(M2)-数量(亿元)": [3052100.0],
           "货币和准货币(M2)-同比增长": [8.3]}
    )
    mod.macro_china_rmb = lambda: _df(
        **{"日期": ["2026-09-22", "2026-09-23"], "美元/人民币_中间价": [7.10, 7.09]}
    )
    return mod


def _broken_module(exclude: set[str]) -> types.ModuleType:
    """exclude 内的接口抛错,其余正常。"""
    base = _ok_module()
    mod = types.ModuleType("akshare")
    for name in ("macro_china_pmi", "macro_china_cpi", "macro_china_ppi", "macro_china_lpr",
                 "macro_china_shrzgm", "macro_china_money_supply", "macro_china_rmb"):
        if name in exclude:
            def _boom(*a, _name=name, **k):
                raise RuntimeError(f"{_name} down")
            setattr(mod, name, _boom)
        else:
            setattr(mod, name, getattr(base, name))
    return mod


def _install(monkeypatch, mod):
    monkeypatch.setitem(sys.modules, "akshare", mod)


def _vendor() -> AkshareMacroVendor:
    return AkshareMacroVendor()


# ---------- vendor ----------

def test_macro_full_success(monkeypatch):
    _install(monkeypatch, _ok_module())
    out = _vendor().fetch([], {})
    by = {x.name: x for x in out}
    assert isinstance(out[0], MacroIndicator)
    assert by["制造业PMI"].value == 49.8            # 取末行(最新一期)
    assert by["制造业PMI"].period == "2026年08月"
    assert by["非制造业PMI"].value == 50.3
    assert by["CPI同比"].value == 0.3 and by["CPI同比"].unit == "%"
    assert by["PPI同比"].value == -2.9
    assert by["LPR(1Y)"].value == 2.95 and by["LPR(1Y)"].period == "2026-09-21"
    assert by["LPR(5Y)"].value == 3.45
    assert by["社融增量"].value == 28300.0 and by["社融增量"].unit == "亿元"
    assert by["M2"].value == 3052100.0 and by["M2同比"].value == 8.3
    usd = by["USDCNY中间价"]
    assert usd.value == 7.09 and usd.period == "2026-09-23" and usd.unit == "CNY"
    assert all(x.source for x in out)               # 每条带来源标识
    assert {x.source for x in out} <= set(AkshareMacroVendor._SPECS) | {"macro_china_rmb"}


def test_macro_partial_failure_fail_soft(monkeypatch):
    # LPR 抛错 + CPI 缺列:只影响自身,其余指标照常产出
    mod = _broken_module({"macro_china_lpr"})
    def cpi_missing_col():
        return _df(**{"月份": ["2026年08月"], "全国-当月": [101.2]})
    mod.macro_china_cpi = cpi_missing_col
    _install(monkeypatch, mod)

    out = _vendor().fetch([], {})
    names = {x.name for x in out}
    assert "LPR(1Y)" not in names and "LPR(5Y)" not in names
    assert "CPI同比" not in names
    assert {"制造业PMI", "PPI同比", "社融增量", "M2", "USDCNY中间价"} <= names


def test_macro_usdcny_zero_is_invalid(monkeypatch):
    mod = _ok_module()
    mod.macro_china_rmb = lambda: _df(**{"日期": ["2026-09-23"], "美元/人民币_中间价": [0.0]})
    _install(monkeypatch, mod)
    out = _vendor().fetch([], {})
    assert "USDCNY中间价" not in {x.name for x in out}
    assert len(out) > 0                              # 其余不受影响


def test_macro_all_fail_returns_empty_no_raise(monkeypatch):
    mod = _broken_module({
        "macro_china_pmi", "macro_china_cpi", "macro_china_ppi", "macro_china_lpr",
        "macro_china_shrzgm", "macro_china_money_supply", "macro_china_rmb",
    })
    _install(monkeypatch, mod)
    assert _vendor().fetch([], {}) == []


def test_macro_missing_lib_raises_vendor_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "akshare", None)
    with pytest.raises(Exception):
        _vendor().fetch([], [])


def test_macro_fn_absent_in_other_akshare_versions(monkeypatch):
    mod = types.ModuleType("akshare")   # 桩里没有任何宏观接口(模拟旧版本)
    _install(monkeypatch, mod)
    assert _vendor().fetch([], {}) == []


# ---------- 行序归一回归(实抓 2026-09-23:东财月度表降序,lpr/shrzgm 升序) ----------

def test_macro_descending_tables_take_latest_not_last_row(monkeypatch):
    """回归:cpi/pmi/ppi/money_supply 实抓为降序(head=最新,tail=2006-2008 年旧数据),
    盲取 iloc[-1] 会把 18-20 年前旧值当'最新值'写入 86400s 缓存 —— 必须按 period
    归一键排序取最新行。"""
    mod = types.ModuleType("akshare")
    # 降序:cpi('2026年08月份' 在 head,2006 年在 tail)
    mod.macro_china_cpi = lambda: _df(
        **{"月份": ["2026年08月份", "2026年07月份", "2006年01月份"],
           "全国-同比增长": [0.3, 0.4, 1.9]}
    )
    # 降序:pmi
    mod.macro_china_pmi = lambda: _df(
        **{"月份": ["2026年08月份", "2026年07月份", "2008年01月份"],
           "制造业-指数": [49.8, 49.3, 41.2]}
    )
    # 降序:ppi
    mod.macro_china_ppi = lambda: _df(
        **{"月份": ["2026年08月份", "2006年01月份"],
           "当月同比增长": [-2.9, 3.0]}
    )
    # 降序:money_supply
    mod.macro_china_money_supply = lambda: _df(
        **{"月份": ["2026年08月份", "2008年01月份"],
           "货币和准货币(M2)-数量(亿元)": [3052100.0, 417810.0],
           "货币和准货币(M2)-同比增长": [8.3, 18.9]}
    )
    # 升序:lpr(日期字符串)/shrzgm('202604' 数字期)——排序对升序表不回退
    mod.macro_china_lpr = lambda: _df(
        **{"TRADE_DATE": ["2026-08-20", "2026-09-21"], "LPR1Y": [3.0, 2.95], "LPR5Y": [3.5, 3.45]}
    )
    mod.macro_china_shrzgm = lambda: _df(
        **{"月份": ["202512", "202604"], "社会融资规模增量": [21000.0, 28300.0]}
    )
    mod.macro_china_rmb = lambda: _df(
        **{"日期": ["2026-09-01", "2026-09-23"], "美元/人民币_中间价": [7.10, 7.09]}
    )
    _install(monkeypatch, mod)

    out = _vendor().fetch([], {})
    by = {x.name: x for x in out}
    assert by["CPI同比"].value == 0.3 and by["CPI同比"].period == "2026年08月份"
    assert by["制造业PMI"].value == 49.8 and by["制造业PMI"].period == "2026年08月份"
    assert by["PPI同比"].value == -2.9 and by["PPI同比"].period == "2026年08月份"
    assert by["M2"].value == 3052100.0 and by["M2同比"].value == 8.3
    assert by["M2"].period == "2026年08月份"
    assert by["LPR(1Y)"].value == 2.95 and by["LPR(1Y)"].period == "2026-09-21"
    assert by["社融增量"].value == 28300.0 and by["社融增量"].period == "202604"
    assert by["USDCNY中间价"].value == 7.09 and by["USDCNY中间价"].period == "2026-09-23"


def test_macro_period_and_value_aligned_in_same_row(monkeypatch):
    """回归(错期归因闭合):降序表中,末行(旧期)某列值更大 —— 旧实现 period 取
    head 最新期、value 取 tail 旧行,期与值错配;新实现两者必须同最新行。"""
    mod = types.ModuleType("akshare")
    mod.macro_china_pmi = lambda: _df(
        **{"月份": ["2026年08月份", "2008年01月份"],
           "制造业-指数": [49.8, 41.2]}   # 旧行值更"大",诱惑盲取末行
    )
    mod.macro_china_cpi = lambda: _df(**{"月份": ["2026年08月份"], "全国-同比增长": [0.3]})
    mod.macro_china_ppi = lambda: _df(**{"月份": ["2026年08月份"], "当月同比增长": [-2.9]})
    mod.macro_china_lpr = lambda: _df(
        **{"TRADE_DATE": ["2026-09-21"], "LPR1Y": [2.95], "LPR5Y": [3.45]}
    )
    mod.macro_china_shrzgm = lambda: _df(**{"月份": ["202604"], "社会融资规模增量": [28300.0]})
    mod.macro_china_money_supply = lambda: _df(
        **{"月份": ["2026年08月份"],
           "货币和准货币(M2)-数量(亿元)": [3052100.0],
           "货币和准货币(M2)-同比增长": [8.3]}
    )
    mod.macro_china_rmb = lambda: _df(**{"日期": ["2026-09-23"], "美元/人民币_中间价": [7.09]})
    _install(monkeypatch, mod)

    out = _vendor().fetch([], {})
    by = {x.name: x for x in out}
    assert by["制造业PMI"].value == 49.8
    assert by["制造业PMI"].period == "2026年08月份"   # 期与值同源于最新行
    assert all(x.period != "2008年01月份" for x in out)


def test_macro_unparseable_period_fails_soft(monkeypatch):
    """period 全不可解析:不猜序、不编造,该表记失败,其余表照常。"""
    mod = types.ModuleType("akshare")
    mod.macro_china_pmi = lambda: _df(
        **{"月份": ["未知期A", "未知期B"], "制造业-指数": [49.8, 49.3]}
    )
    mod.macro_china_cpi = lambda: _df(**{"月份": ["2026年08月份"], "全国-同比增长": [0.3]})
    mod.macro_china_ppi = lambda: _df(**{"月份": ["2026年08月份"], "当月同比增长": [-2.9]})
    mod.macro_china_lpr = lambda: _df(
        **{"TRADE_DATE": ["2026-09-21"], "LPR1Y": [2.95], "LPR5Y": [3.45]}
    )
    mod.macro_china_shrzgm = lambda: _df(**{"月份": ["202604"], "社会融资规模增量": [28300.0]})
    mod.macro_china_money_supply = lambda: _df(
        **{"月份": ["2026年08月份"],
           "货币和准货币(M2)-数量(亿元)": [3052100.0],
           "货币和准货币(M2)-同比增长": [8.3]}
    )
    mod.macro_china_rmb = lambda: _df(**{"日期": ["2026-09-23"], "美元/人民币_中间价": [7.09]})
    _install(monkeypatch, mod)

    out = _vendor().fetch([], {})
    names = {x.name for x in out}
    assert "制造业PMI" not in names and "非制造业PMI" not in names
    assert {"CPI同比", "PPI同比", "LPR(1Y)", "社融增量", "M2", "USDCNY中间价"} <= names


def test_macro_lpr_date_objects_supported(monkeypatch):
    """lpr 实抓 TRADE_DATE 是 date 对象(非字符串):排序键须兼容 date/datetime。"""
    from datetime import date as _date

    mod = types.ModuleType("akshare")
    mod.macro_china_lpr = lambda: _df(
        **{"TRADE_DATE": [_date(2026, 8, 20), _date(2026, 9, 21)],
           "LPR1Y": [3.0, 2.95], "LPR5Y": [3.5, 3.45]}
    )
    mod.macro_china_cpi = lambda: _df(**{"月份": ["2026年08月份"], "全国-同比增长": [0.3]})
    mod.macro_china_ppi = lambda: _df(**{"月份": ["2026年08月份"], "当月同比增长": [-2.9]})
    mod.macro_china_pmi = lambda: _df(
        **{"月份": ["2026年08月份"], "制造业-指数": [49.8]}
    )
    mod.macro_china_shrzgm = lambda: _df(**{"月份": ["202604"], "社会融资规模增量": [28300.0]})
    mod.macro_china_money_supply = lambda: _df(
        **{"月份": ["2026年08月份"],
           "货币和准货币(M2)-数量(亿元)": [3052100.0],
           "货币和准货币(M2)-同比增长": [8.3]}
    )
    mod.macro_china_rmb = lambda: _df(**{"日期": ["2026-09-23"], "美元/人民币_中间价": [7.09]})
    _install(monkeypatch, mod)

    out = _vendor().fetch([], {})
    by = {x.name: x for x in out}
    assert by["LPR(1Y)"].value == 2.95 and by["LPR(1Y)"].period == "2026-09-21"


# ---------- Engine / client ----------

def _engine(monkeypatch, mod):
    _install(monkeypatch, mod)
    return Engine(
        datatype="macro",
        vendors={"akshare": AkshareMacroVendor()},
        config=StaticConfigProvider({"macro": [SourceConfig(vendor="akshare", priority=10)]}),
        metrics=InMemoryMetricsSink(),
        cache=TTLCache(5.0), default_ttl=5.0,
    )


def _req():
    return Request(symbols=(), market="CN")


def test_engine_macro_success(monkeypatch):
    r = _engine(monkeypatch, _ok_module()).fetch(_req())
    assert r.ok and r.vendor == "akshare" and len(r.data) >= 8


def test_engine_macro_all_fail_ok_false(monkeypatch):
    mod = _broken_module({
        "macro_china_pmi", "macro_china_cpi", "macro_china_ppi", "macro_china_lpr",
        "macro_china_shrzgm", "macro_china_money_supply", "macro_china_rmb",
    })
    r = _engine(monkeypatch, mod).fetch(_req())
    assert r.ok is False and r.data is None


def test_client_macro_returns_response(monkeypatch):
    from marketdata.client import MarketData

    _install(monkeypatch, _ok_module())
    md = MarketData(config=StaticConfigProvider({
        "macro": [SourceConfig(vendor="akshare", priority=10)],
    }))
    resp = md.macro()
    assert resp.ok and all(isinstance(x, MacroIndicator) for x in resp.data)
    assert md._macro_engine.default_ttl == 86400.0   # 规格 TTL


def test_client_macro_all_fail_no_raise(monkeypatch):
    from marketdata.client import MarketData

    _install(monkeypatch, _broken_module({
        "macro_china_pmi", "macro_china_cpi", "macro_china_ppi", "macro_china_lpr",
        "macro_china_shrzgm", "macro_china_money_supply", "macro_china_rmb",
    }))
    md = MarketData(config=StaticConfigProvider({
        "macro": [SourceConfig(vendor="akshare", priority=10)],
    }))
    resp = md.macro()   # 不抛异常
    assert resp.ok is False and resp.data is None
