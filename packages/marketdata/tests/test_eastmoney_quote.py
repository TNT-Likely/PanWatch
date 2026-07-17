import marketdata.vendors.eastmoney as ev
from marketdata.symbol import Symbol
from marketdata.types import Quote


def _fake_data(code: str = "600519", name: str = "贵州茅台") -> dict:
    """构造真实形态的 push2 stock/get JSON(f59=2 位小数,价格字段为放大 100 倍的整数)。

    current=1700.00 prev_close=1680.00 open=1685.00 high=1710.00 low=1670.00
    change_amount=20.00(=current-prev) change_pct=1.19%(≈20/1680)
    """
    return {
        "f43": 170000,     # 最新价(raw) → /10^2 = 1700.00
        "f44": 171000,     # 最高 → 1710.00
        "f45": 167000,     # 最低 → 1670.00
        "f46": 168500,     # 今开 → 1685.00
        "f47": 12345,      # 成交量(手)
        "f48": 6789000000, # 成交额(元)
        "f50": 120,        # 量比(raw) → /100 = 1.20
        "f55": 999,        # 未在本 vendor 中作为主字段使用(CN 换手率走 f168)
        "f57": code,       # 代码
        "f58": name,       # 名称
        "f59": 2,          # 小数位数
        "f60": 168000,     # 昨收 → 1680.00
        "f116": 2100050000000,  # 总市值(raw 元)→ /1e8 = 21000.5(亿)
        "f117": 2100050000000,  # 流通市值(raw 元)→ /1e8 = 21000.5(亿)
        "f168": 50,        # 换手率(raw) → /100 = 0.50%
        "f169": 2000,      # 涨跌额(raw) → /10^2 = 20.00
        "f170": 119,       # 涨跌幅(raw) → /100 = 1.19%
        "f171": 500,       # 振幅(未映射到 Quote,忽略)
    }


def _payload(code: str = "600519", name: str = "贵州茅台") -> dict:
    return {"data": _fake_data(code, name)}


def test_eastmoney_parses_quote_with_decimal_restore(monkeypatch):
    monkeypatch.setattr(ev, "market_get", lambda *a, **k: _payload())
    v = ev.EastmoneyQuoteVendor()
    out = v.fetch([Symbol.parse("600519", market="CN")], {})
    assert len(out) == 1
    q = out[0]
    assert isinstance(q, Quote)
    assert q.symbol == "600519" and q.name == "贵州茅台" and q.market == "CN"
    assert q.current_price == 1700.0
    assert q.prev_close == 1680.0
    assert q.open_price == 1685.0
    assert q.high_price == 1710.0
    assert q.low_price == 1670.0
    assert q.change_amount == 20.0
    assert q.change_pct == 1.19
    assert q.turnover_rate == 0.5
    assert q.volume_ratio == 1.2
    assert q.volume == 12345.0
    assert q.turnover == 6789000000.0
    assert q.total_market_value == 21000.5
    assert q.circulating_market_value == 21000.5


def test_eastmoney_batch_multiple_symbols_loops_calls(monkeypatch):
    calls: list[dict] = []

    def fake_market_get(url, *, params=None, **kwargs):
        calls.append(params or {})
        secid = (params or {}).get("secid", "")
        if secid.endswith("600519"):
            return _payload("600519", "贵州茅台")
        if secid.endswith("000001"):
            return _payload("000001", "平安银行")
        return None

    monkeypatch.setattr(ev, "market_get", fake_market_get)
    v = ev.EastmoneyQuoteVendor()
    symbols = [Symbol.parse("600519", market="CN"), Symbol.parse("000001", market="CN")]
    out = v.fetch(symbols, {})
    assert len(calls) == 2  # 单只查询,逐只循环
    assert len(out) == 2
    codes = {q.symbol for q in out}
    assert codes == {"600519", "000001"}


def test_eastmoney_empty_response_returns_empty(monkeypatch):
    monkeypatch.setattr(ev, "market_get", lambda *a, **k: None)
    out = ev.EastmoneyQuoteVendor().fetch([Symbol.parse("600519", market="CN")], {})
    assert out == []


def test_eastmoney_no_symbols_returns_empty():
    assert ev.EastmoneyQuoteVendor().fetch([], {}) == []


def test_eastmoney_unsupported_market_skipped(monkeypatch):
    # 本 vendor 只做 CN;HK/US symbol 应被跳过,不发请求
    calls = {"n": 0}

    def fake_market_get(*a, **k):
        calls["n"] += 1
        return _payload()

    monkeypatch.setattr(ev, "market_get", fake_market_get)
    out = ev.EastmoneyQuoteVendor().fetch([Symbol.parse("00700", market="HK")], {})
    assert out == []
    assert calls["n"] == 0


def test_eastmoney_missing_data_key_returns_empty(monkeypatch):
    monkeypatch.setattr(ev, "market_get", lambda *a, **k: {"data": None})
    out = ev.EastmoneyQuoteVendor().fetch([Symbol.parse("600519", market="CN")], {})
    assert out == []
