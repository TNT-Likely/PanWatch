"""global_markets 三 provider + Engine 主备降级测试(全部离线,monkeypatch 外部请求)。"""

import sys
import types

import pandas as pd
import pytest

import marketdata.vendors.global_markets as gm
import marketdata.vendors.tencent as tv
from marketdata.cache import TTLCache
from marketdata.defaults import InMemoryMetricsSink, StaticConfigProvider
from marketdata.engine import Engine
from marketdata.ports import SourceConfig
from marketdata.types import GlobalIndexQuote, Request


def _fake_tencent_line(var: str, name: str, price: float, prev: float, pct: float) -> str:
    parts = ["0"] * 40
    parts[1] = name
    parts[3] = str(price)   # current
    parts[4] = str(prev)    # prev_close
    parts[32] = str(pct)    # change_pct
    return f'v_{var}="' + "~".join(parts) + '";'


def _fake_wh_line(var: str, name: str, price: float, prev: float, pct: float | None) -> str:
    """按 2026-09-23 实抓 whUSDX 真实行构造的 n=22 外汇布局桩。

    字段位:1=名称 3=最新价 5=时间戳 6=昨收 7=开 8=高 9=低 12=涨跌额 13=涨跌幅%。
    pct=None 时字段留空,模拟涨跌幅缺位(应由 price/close 计算)。
    """
    parts = [""] * 22
    parts[0] = "310"
    parts[1] = name
    parts[2] = "USDX"
    parts[3] = str(price)
    parts[4] = "0"
    parts[5] = "20260923165100"
    parts[6] = str(prev)
    parts[7] = str(prev + 0.01)
    parts[8] = str(price + 0.08)
    parts[9] = str(price - 0.26)
    parts[12] = f"{price - prev:.4f}"
    if pct is not None:
        parts[13] = str(pct)
    parts[21] = "2026-09-23"
    return f'v_{var}="' + "~".join(parts) + '";'


def _patch_tencent(monkeypatch, lines: str | None):
    """monkeypatch 腾讯 HTTP 层(lines=None 模拟接口不可用)。"""
    if lines is None:
        monkeypatch.setattr(tv, "market_get", lambda *a, **k: None)
    else:
        monkeypatch.setattr(tv, "market_get", lambda *a, **k: lines.encode("gbk"))


class _FakeFastInfo:
    def __init__(self, last, prev):
        self._d = {"last_price": last, "previous_close": prev}

    def get(self, k, default=None):
        return self._d.get(k, default)


def _install_yahoo_stub(monkeypatch, prices: dict[str, tuple[float, float]], broken: set[str] = ()):
    mod = types.ModuleType("yfinance")

    class _FakeTicker:
        def __init__(self, symbol):
            if symbol in broken:
                raise RuntimeError("yahoo down")
            self.fast_info = _FakeFastInfo(*prices[symbol])

    mod.Ticker = _FakeTicker
    monkeypatch.setitem(sys.modules, "yfinance", mod)


def _install_akshare_stub(monkeypatch, fn):
    mod = types.ModuleType("akshare")
    mod.index_global_spot_em = fn
    monkeypatch.setitem(sys.modules, "akshare", mod)


def _engine(monkeypatch, *, tencent_lines, yahoo_prices, akshare_fn):
    _patch_tencent(monkeypatch, tencent_lines)
    if yahoo_prices is None:
        monkeypatch.setitem(sys.modules, "yfinance", None)   # 模拟缺库
    else:
        _install_yahoo_stub(monkeypatch, yahoo_prices)
    if akshare_fn is None:
        monkeypatch.setitem(sys.modules, "akshare", None)
    else:
        _install_akshare_stub(monkeypatch, akshare_fn)
    return Engine(
        datatype="global_markets",
        vendors={
            "tencent_global": gm.TencentGlobalVendor(),
            "yahoo_global": gm.YahooGlobalVendor(),
            "akshare_global": gm.AkshareGlobalVendor(),
        },
        config=StaticConfigProvider({
            "global_markets": [
                SourceConfig(vendor="tencent_global", priority=10),
                SourceConfig(vendor="yahoo_global", priority=20),
                SourceConfig(vendor="akshare_global", priority=30),
            ],
        }),
        metrics=InMemoryMetricsSink(),
        cache=TTLCache(5.0), default_ttl=5.0,
    )


# ---------- TencentGlobalVendor ----------

def test_tencent_global_parses(monkeypatch):
    _patch_tencent(monkeypatch, ";".join([
        _fake_tencent_line("usDJI", "道琼斯", 44000.5, 44230.0, -0.52),
        _fake_wh_line("whUSDX", "美元指数", 100.2, 100.15, 0.05),   # wh* 用真实 n=22 外汇布局
    ]))
    out = gm.TencentGlobalVendor().fetch([], {})
    assert {q.symbol for q in out} == {"DJI", "USDX"}
    by = {q.symbol: q for q in out}
    assert isinstance(by["DJI"], GlobalIndexQuote)
    assert by["DJI"].name == "道琼斯" and by["DJI"].price == 44000.5
    assert by["DJI"].close == 44230.0 and by["DJI"].change_pct == -0.52
    assert by["DJI"].source_tag == "tencent_global"
    assert by["USDX"].price == 100.2 and by["USDX"].close == 100.15
    assert by["USDX"].change_pct == 0.05


def test_tencent_global_http_down_returns_empty(monkeypatch):
    _patch_tencent(monkeypatch, None)
    assert gm.TencentGlobalVendor().fetch([], {}) == []


def test_tencent_global_wh_forex_layout_parsed():
    """回归:wh* 行是 n=22 外汇布局,过不了 _parse_line 的 n>=35 门槛,
    必须走 _parse_wh_line 专用解析(实抓 2026-09-23 复现:否则美元指数静默缺失)。"""
    import marketdata.vendors.tencent as tv

    line = _fake_wh_line("whUSDX", "美元指数", 100.78, 100.53, 0.25)
    parsed = gm._parse_wh_line(line)
    assert parsed == ("美元指数", 100.78, 100.53, 0.25)
    # 外汇行不满足 n>=35:_parse_line 必须拒绝(证明原路径确实拿不到)
    assert tv._parse_line(line, "") is None


def test_tencent_global_wh_pct_missing_computed_from_close(monkeypatch):
    """涨跌幅字段缺位时由 price/close 计算,不编造、不丢行。"""
    _patch_tencent(monkeypatch, _fake_wh_line("whUSDX", "美元指数", 100.78, 100.53, None))
    out = gm.TencentGlobalVendor().fetch([], {})
    assert [q.symbol for q in out] == ["USDX"]
    q = out[0]
    assert q.price == 100.78 and q.close == 100.53
    assert q.change_pct == pytest.approx((100.78 - 100.53) / 100.53 * 100)
    assert q.source_tag == "tencent_global"


def test_tencent_global_mixed_stock_and_wh_lines(monkeypatch):
    """主源一次请求混合股指行(n=73/78)与外汇行(n=22):两路径都解析,USDX 不再缺。"""
    _patch_tencent(monkeypatch, ";".join([
        _fake_tencent_line("usDJI", "道琼斯", 44000.5, 44230.0, -0.52),
        _fake_wh_line("whUSDX", "美元指数", 100.78, 100.53, 0.25),
    ]))
    out = gm.TencentGlobalVendor().fetch([], {})
    by = {q.symbol: q for q in out}
    assert set(by) == {"DJI", "USDX"}
    assert by["DJI"].price == 44000.5 and by["DJI"].close == 44230.0
    assert by["USDX"].price == 100.78 and by["USDX"].close == 100.53
    assert by["USDX"].change_pct == 0.25 and by["USDX"].name == "美元指数"


def test_tencent_global_symbol_filter(monkeypatch):
    seen = {}

    def fake_get(url, **k):
        seen["codes"] = url.split("q=")[1]
        return ";".join([
            _fake_tencent_line("usDJI", "道琼斯", 44000.5, 44230.0, -0.52),
            _fake_tencent_line("hkHSI", "恒生指数", 25000.0, 24900.0, 0.4),
        ]).encode("gbk")

    monkeypatch.setattr(tv, "market_get", fake_get)
    out = gm.TencentGlobalVendor().fetch([], {"symbols": ["HSI"]})
    assert seen["codes"] == "hkHSI"   # 只请求过滤后的代码
    assert [q.symbol for q in out] == ["HSI"]


# ---------- YahooGlobalVendor ----------

def test_yahoo_global_parses(monkeypatch):
    _install_yahoo_stub(monkeypatch, {"^N225": (38000.0, 37581.0), "^KS11": (2500.0, 2480.0)})
    out = gm.YahooGlobalVendor().fetch([], {})
    by = {q.symbol: q for q in out}
    assert by["N225"].price == 38000.0 and by["N225"].close == 37581.0
    assert by["N225"].source_tag == "yahoo_global"
    assert by["KS11"].price == 2500.0


def test_yahoo_global_per_symbol_failure_soft(monkeypatch):
    prices = {"^N225": (38000.0, 37581.0), "^KS11": (2500.0, 2480.0),
              "^GSPC": (5000.0, 4980.0), "^DJI": (44000.0, 44230.0),
              "^IXIC": (15000.0, 14950.0), "DX-Y.NYB": (100.2, 100.15)}
    _install_yahoo_stub(monkeypatch, prices, broken={"^HSI"})
    out = gm.YahooGlobalVendor().fetch([], {})
    assert {q.symbol for q in out} == {"N225", "KS11", "SPX", "DJI", "IXIC", "USDX"}


def test_yahoo_global_missing_lib_raises_vendor_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "yfinance", None)
    with pytest.raises(Exception):
        gm.YahooGlobalVendor().fetch([], {})


# ---------- AkshareGlobalVendor ----------

def _em_df():
    return pd.DataFrame({
        "代码": ["DJIA", "N225", "UDI", "MXX", "BADROW"],
        "名称": ["道琼斯", "日经225", "美元指数", "墨西哥BOLSA", "坏行"],
        "最新价": [44000.5, 38000.0, 100.2, 90000.0, "-"],
        "涨跌幅": [-0.52, 1.1, 0.05, 0.3, 1.0],
        "昨收价": [44230.0, 37581.0, 100.15, 90000.0, 1.0],
    })


def test_akshare_global_parses_and_normalizes_keys(monkeypatch):
    _install_akshare_stub(monkeypatch, lambda: _em_df())
    out = gm.AkshareGlobalVendor().fetch([], {})
    by = {q.symbol: q for q in out}
    assert set(by) == {"DJI", "N225", "USDX", "MXX"}   # 键归一 + 未映射透传;BADROW(无法解析价)剔除
    assert by["DJI"].name == "道琼斯" and by["DJI"].price == 44000.5
    assert by["USDX"].source_tag == "akshare_global"
    assert by["N225"].close == 37581.0


def test_akshare_global_symbol_filter(monkeypatch):
    _install_akshare_stub(monkeypatch, lambda: _em_df())
    out = gm.AkshareGlobalVendor().fetch([], {"symbols": ["N225"]})
    assert [q.symbol for q in out] == ["N225"]


def test_akshare_global_missing_lib_raises_vendor_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "akshare", None)
    with pytest.raises(Exception):
        gm.AkshareGlobalVendor().fetch([], {})


def test_akshare_global_missing_columns_raises(monkeypatch):
    _install_akshare_stub(monkeypatch, lambda: pd.DataFrame({"代码": ["DJIA"], "名称": ["道琼斯"]}))
    with pytest.raises(Exception):
        gm.AkshareGlobalVendor().fetch([], {})


def test_akshare_global_empty_df_returns_empty(monkeypatch):
    _install_akshare_stub(monkeypatch, lambda: pd.DataFrame())
    assert gm.AkshareGlobalVendor().fetch([], {}) == []


# ---------- Engine 主备/降级 ----------

def _req():
    return Request(symbols=(), market="CN")


def test_engine_primary_tencent_success(monkeypatch):
    e = _engine(
        monkeypatch,
        tencent_lines=_fake_tencent_line("usDJI", "道琼斯", 44000.5, 44230.0, -0.52),
        yahoo_prices=None, akshare_fn=None,
    )
    r = e.fetch(_req())
    assert r.ok and r.vendor == "tencent_global"
    assert isinstance(r.data[0], GlobalIndexQuote) and r.data[0].symbol == "DJI"


def test_engine_primary_down_yahoo_takes_over(monkeypatch):
    e = _engine(
        monkeypatch,
        tencent_lines=None,
        yahoo_prices={"^N225": (38000.0, 37581.0)},
        akshare_fn=None,
    )
    r = e.fetch(_req())
    assert r.ok and r.vendor == "yahoo_global"
    assert r.data[0].symbol == "N225"


def test_engine_second_down_akshare_takes_over(monkeypatch):
    e = _engine(
        monkeypatch,
        tencent_lines=None,
        yahoo_prices=None,                     # yfinance 缺库 → VendorError
        akshare_fn=lambda: _em_df(),
    )
    r = e.fetch(_req())
    assert r.ok and r.vendor == "akshare_global"


def test_engine_all_fail_ok_false_no_raise(monkeypatch):
    def boom():
        raise RuntimeError("em down")

    e = _engine(monkeypatch, tencent_lines=None, yahoo_prices=None, akshare_fn=boom)
    r = e.fetch(_req())
    assert r.ok is False and r.data is None


def test_client_global_markets_method(monkeypatch):
    from marketdata.client import MarketData

    _patch_tencent(monkeypatch, _fake_tencent_line("usDJI", "道琼斯", 44000.5, 44230.0, -0.52))
    monkeypatch.setitem(sys.modules, "yfinance", None)
    monkeypatch.setitem(sys.modules, "akshare", None)
    md = MarketData(config=StaticConfigProvider({
        "global_markets": [SourceConfig(vendor="tencent_global", priority=10)],
    }))
    resp = md.global_markets()
    assert resp.ok and isinstance(resp.data[0], GlobalIndexQuote)
    assert md._global_markets_engine.default_ttl == 300.0   # 规格 TTL
