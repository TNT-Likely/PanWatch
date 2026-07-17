"""指数 quote/kline:显式符号/secid 专用路径(不经 Symbol.parse,避免 000001 股/指歧义)。"""

import marketdata.vendors.kline as kv
import marketdata.vendors.tencent as tv
from marketdata import MarketData, StaticConfigProvider


def _md() -> MarketData:
    return MarketData(config=StaticConfigProvider({}))


def _fake_index_line() -> str:
    parts = ["0"] * 50
    parts[1] = "上证指数"
    parts[2] = "000001"
    parts[3] = "3200.0"  # current
    parts[4] = "3180.0"  # prev_close
    parts[31] = "20.0"  # change_amount
    parts[32] = "0.63"  # change_pct
    parts[35] = "3200.0/1000/500000.0"  # price/vol/turnover -> turnover=500000.0
    return 'v_sh000001="' + "~".join(parts) + '";'


def test_index_quotes(monkeypatch):
    """index_quotes 复用腾讯行情解析,按原始符号(sh000001)返回 name/current_price/change_pct/turnover。"""
    monkeypatch.setattr(tv, "market_get", lambda *a, **k: _fake_index_line().encode("gbk"))
    out = _md().index_quotes(["sh000001"])
    assert out and out[0]["name"] == "上证指数"
    assert out[0]["current_price"] == 3200.0
    assert out[0]["change_pct"] == 0.63
    assert out[0]["turnover"] == 500000.0


def test_index_klines(monkeypatch):
    """index_klines 按 INDEX_SECID 显式映射走东财,复用东财K线解析。"""
    payload = {"data": {"klines": ["2026-07-01,3180,3200,3210,3170,1e8"]}}
    monkeypatch.setattr(kv, "market_get", lambda *a, **k: payload)
    out = _md().index_klines("000001", market="CN", days=120)
    assert out and out[0].close == 3200.0 and out[0].high == 3210.0


def test_index_klines_unmapped_returns_empty():
    """美股指数(如 IXIC)未在 INDEX_SECID 映射中 → 空列表,fail-soft。"""
    assert _md().index_klines("IXIC", market="US", days=120) == []
