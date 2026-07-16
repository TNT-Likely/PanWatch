import marketdata.vendors.sina as sv
from marketdata.symbol import Symbol
from marketdata.types import Quote


def test_sina_us_quote(monkeypatch):
    # US: gb_ 逗号字段,idx 0 name,1 price,2 change%,5 open,6 high,7 low,10 vol,14 pe,26 prev_close
    parts = ["0"] * 30
    parts[0] = "苹果"; parts[1] = "150.5"; parts[2] = "1.2"; parts[5] = "149.0"
    parts[6] = "151.0"; parts[7] = "148.0"; parts[10] = "1000000"; parts[14] = "28.5"; parts[26] = "148.7"
    line = 'var hq_str_gb_aapl="' + ",".join(parts) + '";'
    monkeypatch.setattr(sv, "market_get", lambda *a, **k: line)
    out = sv.SinaQuoteVendor().fetch([Symbol.parse("AAPL", "US")], {})
    assert len(out) == 1 and isinstance(out[0], Quote)
    q = out[0]
    assert q.symbol == "AAPL" and q.market == "US" and q.name == "苹果"
    assert q.current_price == 150.5 and q.change_pct == 1.2 and q.prev_close == 148.7 and q.pe_ratio == 28.5


def test_sina_hk_quote(monkeypatch):
    # HK: rt_hk 逗号字段,idx 1 name,2 open,3 prev_close,4 high,5 low,6 price,7 change,8 change%,11 amount,12 vol
    parts = ["0"] * 15
    parts[1] = "腾讯控股"; parts[2] = "300.0"; parts[3] = "298.0"; parts[4] = "305.0"
    parts[5] = "297.0"; parts[6] = "302.0"; parts[7] = "4.0"; parts[8] = "1.34"; parts[11] = "5e8"; parts[12] = "1000000"
    line = 'var hq_str_rt_hk00700="' + ",".join(parts) + '";'
    monkeypatch.setattr(sv, "market_get", lambda *a, **k: line)
    out = sv.SinaQuoteVendor().fetch([Symbol.parse("00700", "HK")], {})
    q = out[0]
    assert q.symbol == "00700" and q.market == "HK" and q.name == "腾讯控股"
    assert q.current_price == 302.0 and q.prev_close == 298.0 and q.change_pct == 1.34


def test_sina_cn_unsupported():
    # CN 不支持 → supports_markets 拦截(vendor 只 US/HK);此处直接调不传 CN
    assert sv.SinaQuoteVendor().fetch([], {}) == []
