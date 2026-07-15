from marketdata.types import Request, Quote, Response


def test_request_cache_key_stable():
    r = Request(symbols=("600519", "000001"), market="CN")
    assert r.cache_key("quote") == "quote|CN|day|120|12|600519,000001|"


def test_response_is_empty():
    assert Response(ok=True, data=[]).is_empty is True
    assert Response(ok=True, data=None).is_empty is True
    q = Quote(symbol="600519", market="CN", current_price=1.0)
    assert Response(ok=True, data=[q]).is_empty is False


def test_quote_defaults_optional_fields_none():
    q = Quote(symbol="600519", market="CN", current_price=1700.0)
    assert q.name == "" and q.pe_ratio is None and q.volume_ratio is None
