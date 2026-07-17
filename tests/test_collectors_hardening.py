"""采集层批量整治 P1:共享 market_http(节流/重试/来源)+ 各 collector 缓存。

价格提醒所需的量比直接从腾讯报价 parts[49] 取,免再拉 K线;
报价/资金流/异动加 TTL 缓存,避免调度任务每轮重复联网触发限流。
"""

from __future__ import annotations

import logging

from src.collectors import capital_flow_collector, market_http
from src.models.market import MarketCode


def test_capital_flow_cached(monkeypatch):
    """资金流为日级数据,同一只在 TTL 内应命中缓存,不重复调用 marketdata 包。"""
    from marketdata.types import CapitalFlow as MdCF

    calls = {"n": 0}

    class _MD:
        def capital_flow(self, symbol, *, market="CN"):
            calls["n"] += 1
            return MdCF(
                symbol=symbol, name="贵州茅台",
                main_net_inflow=100.0, main_net_inflow_pct=1.0,
                super_net_inflow=4.0, big_net_inflow=3.0,
                mid_net_inflow=2.0, small_net_inflow=1.0,
                main_net_5d=None,
            )

    monkeypatch.setattr(capital_flow_collector, "get_market_data", lambda: _MD())
    c = capital_flow_collector.CapitalFlowCollector(MarketCode.CN)
    assert c.get_capital_flow("600519") is not None
    assert c.get_capital_flow("600519") is not None
    assert calls["n"] == 1, f"第二次应命中资金流缓存,实际调用 {calls['n']} 次"


def test_market_get_retries_and_logs_source(monkeypatch, caplog):
    """market_get 失败应退避重试,并在日志带上 [src=...] 调用来源。"""
    calls = {"n": 0}

    class _FakeClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def get(self, *args, **kwargs):
            calls["n"] += 1
            raise RuntimeError("boom")

    monkeypatch.setattr(market_http.httpx, "Client", _FakeClient)
    monkeypatch.setattr(market_http.time, "sleep", lambda *_: None)

    with caplog.at_level(logging.WARNING):
        with market_http.fetch_source("unit_src"):
            out = market_http.market_get(
                "http://x", host_key="x", retries=2, log_label="测试"
            )

    assert out is None
    assert calls["n"] == 3, f"应 1 次 + 重试 2 次 = 3 次,实际 {calls['n']}"
    assert any(
        "[src=unit_src]" in r.getMessage() for r in caplog.records
    ), caplog.text
