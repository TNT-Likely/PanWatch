import marketdata.vendors.kline as kv
from marketdata.symbol import Symbol
from marketdata.types import Bar

# 固定时间戳(UTC),对应 2026-07-01 / 07-02 / 07-03,避免依赖当前时间
_TS1 = 1782864000
_TS2 = 1782950400
_TS3 = 1783036800


def _chart_payload(timestamps, opens, highs, lows, closes, volumes, adjcloses=None):
    indicators = {"quote": [{"open": opens, "high": highs, "low": lows,
                             "close": closes, "volume": volumes}]}
    if adjcloses is not None:
        indicators["adjclose"] = [{"adjclose": adjcloses}]
    return {"chart": {"result": [{"timestamp": timestamps, "indicators": indicators}], "error": None}}


def test_yahoo_kline_parses_us(monkeypatch):
    payload = _chart_payload(
        [_TS1, _TS2],
        [180.0, 182.0], [185.0, 186.0], [179.0, 181.0], [184.0, 185.5], [1000000, 1200000],
    )
    monkeypatch.setattr(kv, "market_get", lambda *a, **k: payload)
    out = kv.YahooKlineVendor().fetch([Symbol.parse("AAPL", market="US")], {"days": 120})
    assert len(out) == 2 and isinstance(out[0], Bar)
    assert out[0].date == "2026-07-01"
    assert out[0].open == 180.0 and out[0].high == 185.0 and out[0].low == 179.0 and out[0].close == 184.0
    assert out[1].date == "2026-07-02" and out[1].volume == 1200000.0


def test_yahoo_kline_parses_hk(monkeypatch):
    payload = _chart_payload(
        [_TS1, _TS2, _TS3],
        [300.0, 302.0, 305.0], [310.0, 308.0, 312.0], [298.0, 300.0, 303.0],
        [305.0, 303.0, 310.0], [5000000, 4800000, 5200000],
    )
    captured = {}

    def fake_market_get(url, *, host_key=None, params=None, **k):
        captured["url"] = url
        captured["host_key"] = host_key
        captured["params"] = params
        return payload

    monkeypatch.setattr(kv, "market_get", fake_market_get)
    out = kv.YahooKlineVendor().fetch([Symbol.parse("0700", market="HK")], {"days": 60})
    assert len(out) == 3
    assert out[2].date == "2026-07-03" and out[2].close == 310.0
    # sym 应转换成 yfinance 港股格式,host_key 固定 query2
    assert "0700.HK" in captured["url"]
    assert captured["host_key"] == "query2.finance.yahoo.com"
    assert captured["params"]["range"] == "3mo"


def test_yahoo_kline_prefers_adjclose(monkeypatch):
    payload = _chart_payload(
        [_TS1], [180.0], [185.0], [179.0], [184.0], [1000000],
        adjcloses=[183.2],
    )
    monkeypatch.setattr(kv, "market_get", lambda *a, **k: payload)
    out = kv.YahooKlineVendor().fetch([Symbol.parse("AAPL", market="US")], {"days": 30})
    assert len(out) == 1 and out[0].close == 183.2


def test_yahoo_kline_skips_null_bar(monkeypatch):
    payload = _chart_payload(
        [_TS1, _TS2],
        [180.0, None], [185.0, 186.0], [179.0, 181.0], [184.0, None], [1000000, 1200000],
    )
    monkeypatch.setattr(kv, "market_get", lambda *a, **k: payload)
    out = kv.YahooKlineVendor().fetch([Symbol.parse("AAPL", market="US")], {"days": 30})
    assert len(out) == 1 and out[0].date == "2026-07-01"


def test_yahoo_kline_truncates_to_days(monkeypatch):
    payload = _chart_payload(
        [_TS1, _TS2, _TS3],
        [1.0, 2.0, 3.0], [1.5, 2.5, 3.5], [0.5, 1.5, 2.5], [1.2, 2.2, 3.2],
        [10, 20, 30],
    )
    monkeypatch.setattr(kv, "market_get", lambda *a, **k: payload)
    out = kv.YahooKlineVendor().fetch([Symbol.parse("AAPL", market="US")], {"days": 2})
    assert len(out) == 2
    assert out[0].date == "2026-07-02" and out[1].date == "2026-07-03"


def test_yahoo_kline_empty_result_returns_empty(monkeypatch):
    monkeypatch.setattr(kv, "market_get", lambda *a, **k: {"chart": {"result": [], "error": None}})
    out = kv.YahooKlineVendor().fetch([Symbol.parse("AAPL", market="US")], {"days": 30})
    assert out == []


def test_yahoo_kline_none_response_returns_empty(monkeypatch):
    monkeypatch.setattr(kv, "market_get", lambda *a, **k: None)
    out = kv.YahooKlineVendor().fetch([Symbol.parse("AAPL", market="US")], {"days": 30})
    assert out == []


def test_yahoo_kline_no_symbols_returns_empty():
    assert kv.YahooKlineVendor().fetch([], {"days": 30}) == []


def test_yahoo_kline_rejects_cn_market():
    # 不支持 CN,直接返回空,不发请求
    out = kv.YahooKlineVendor().fetch([Symbol.parse("600519", market="CN")], {"days": 30})
    assert out == []


def test_yahoo_kline_passes_proxy(monkeypatch):
    captured = {}

    def fake_market_get(*a, **k):
        captured["proxy"] = k.get("proxy")
        return _chart_payload([_TS1], [1.0], [1.5], [0.5], [1.2], [10])

    monkeypatch.setattr(kv, "market_get", fake_market_get)
    kv.YahooKlineVendor().fetch([Symbol.parse("AAPL", market="US")], {"days": 30, "proxy": "http://127.0.0.1:7890"})
    assert captured["proxy"] == "http://127.0.0.1:7890"
