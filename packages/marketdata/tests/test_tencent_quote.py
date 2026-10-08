import marketdata.vendors.tencent as tv
from marketdata.symbol import Symbol


def _fake_line() -> str:
    parts = ["0"] * 50
    parts[1] = "贵州茅台"
    parts[2] = "600519"
    parts[3] = "1700.0"      # current
    parts[4] = "1680.0"      # prev_close
    parts[5] = "1685.0"      # open
    parts[6] = "12345"       # volume
    parts[30] = "20260930150000"
    parts[31] = "20.0"       # change_amount
    parts[32] = "1.19"       # change_pct
    parts[33] = "1710.0"     # high
    parts[34] = "1670.0"     # low
    parts[35] = "1700/12345/6789.0"   # price/vol/turnover → turnover=6789.0
    parts[38] = "0.5"        # turnover_rate
    parts[39] = "35.0"       # pe
    parts[44] = "2000000"    # circulating mv
    parts[45] = "2100000"    # total mv
    parts[49] = "1.2"        # volume_ratio
    return 'v_sh600519="' + "~".join(parts) + '";'


def test_tencent_parses_quote(monkeypatch):
    monkeypatch.setattr(tv, "market_get", lambda *a, **k: _fake_line().encode("gbk"))
    v = tv.TencentQuoteVendor()
    out = v.fetch([Symbol.parse("600519")], {})
    assert len(out) == 1
    q = out[0]
    assert q.symbol == "600519" and q.name == "贵州茅台" and q.market == "CN"
    assert q.current_price == 1700.0 and q.change_pct == 1.19
    assert q.turnover == 6789.0 and q.volume_ratio == 1.2 and q.pe_ratio == 35.0
    assert q.quote_date.isoformat() == "2026-09-30"
    assert q.source_timestamp.isoformat() == "2026-09-30T15:00:00+08:00"
    assert q.source == "tencent"


def test_tencent_empty_content_returns_empty(monkeypatch):
    monkeypatch.setattr(tv, "market_get", lambda *a, **k: None)
    assert tv.TencentQuoteVendor().fetch([Symbol.parse("600519")], {}) == []
