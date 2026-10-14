from unittest.mock import Mock

import pytest

from marketdata import MarketData
from marketdata.defaults import StaticConfigProvider
from marketdata.ports import SourceConfig
from marketdata import research


def test_reuses_enabled_priority_and_never_uses_disabled_vendor(monkeypatch):
    md = MarketData(StaticConfigProvider({"kline": [SourceConfig(vendor="eastmoney", priority=2), SourceConfig(vendor="tencent", priority=1, enabled=False)]}))
    assert md.research_sources() == ["eastmoney"]
    fetch = Mock()
    monkeypatch.setattr(research, "fetch_history", fetch)
    with pytest.raises(ValueError, match="source_disabled"):
        md.research_history("SH600000", vendor="tencent")
    fetch.assert_not_called()


def test_raw_hfq_pair_lots_conversion_and_observed_calendar(monkeypatch):
    calls = []
    def get(url, **kwargs):
        params = kwargs["params"]; calls.append(params)
        sym, _, _, _, _, adj = params["param"].split(",")
        values = ["2026-10-13", "10", "11", "12", "9", "200"] if not adj else ["2026-10-13", "20", "22", "24", "18", "200"]
        return {"data": {sym: {"hfqday" if adj else "day": [values]}}}
    monkeypatch.setattr(research, "market_get", get)
    history = research.fetch_history("tencent", "SH600000")
    assert history.bars[0]["close"] == 11
    assert history.bars[0]["factor"] == 2
    assert history.bars[0]["volume"] == 20000
    assert research.fetch_calendar("tencent") == ["2026-10-13"]
    assert calls[0]["param"].endswith(",") and calls[1]["param"].endswith(",hfq")
    assert "SH000001".lower() in calls[2]["param"]


@pytest.mark.parametrize("bad", ["duplicate", "dates", "volume", "price", "qfq", "ratio"])
def test_invalid_pairs_fail_closed(monkeypatch, bad):
    def get(url, **kwargs):
        sym = "sh600000"
        adjusted = kwargs["params"]["param"].endswith(",hfq")
        row = ["2026-10-13", "10", "10", "11", "9", "200"]
        key = "hfqday" if adjusted else "day"
        if adjusted:
            if bad == "dates": row[0] = "2026-10-12"
            if bad == "volume": row[5] = "300"
            if bad == "price": row[2] = "nan"
            if bad == "qfq": key = "qfqday"
            if bad == "ratio": row[3] = "30"
        return {"data": {sym: {key: [row, row] if bad == "duplicate" else [row]}}}
    monkeypatch.setattr(research, "market_get", get)
    with pytest.raises(ValueError):
        research.fetch_history("tencent", "SH600000")


def test_eastmoney_raw_and_hfq_share_conversion(monkeypatch):
    calls = []
    def get(url, **kwargs):
        calls.append(kwargs["params"])
        return {"data": {"klines": ["2026-10-13,10,10,11,9,200"]}}
    monkeypatch.setattr(research, "market_get", get)
    history = research.fetch_history("eastmoney", "SZ000001")
    assert [p["fqt"] for p in calls] == ["0", "2"]
    assert all(p["secid"] == "0.000001" for p in calls)
    assert history.bars[0]["volume"] == 20000


def test_incomplete_session_is_excluded_before_pair_validation(monkeypatch):
    def get(url, **kwargs):
        adjusted = kwargs["params"]["param"].endswith(",hfq")
        return {"data": {"sh600000": {"hfqday" if adjusted else "day": [
            ["2026-10-13", "10", "10", "11", "9", "200"],
            ["2026-10-14", "10", "10", "11", "9", "500" if adjusted else "400"]]}}}
    monkeypatch.setattr(research, "market_get", get)
    assert len(research.fetch_history("tencent", "SH600000", completed_through="2026-10-13").bars) == 1
    with pytest.raises(ValueError, match="prices_invalid"):
        research.fetch_history("tencent", "SH600000")


def test_missing_supplier_response_is_not_an_adjustment_mismatch(monkeypatch):
    monkeypatch.setattr(research, "market_get", lambda *a, **kw: None)
    with pytest.raises(ValueError, match="source_history_unavailable"):
        research.fetch_history("tencent", "SH600000")
