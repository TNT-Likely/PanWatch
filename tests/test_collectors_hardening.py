"""采集层批量整治 P1:共享 market_http(节流/重试/来源)+ 各 collector 缓存。

价格提醒所需的量比直接从腾讯报价 parts[49] 取,免再拉 K线;
报价/资金流/异动加 TTL 缓存,避免调度任务每轮重复联网触发限流。
"""

from __future__ import annotations

import logging

from src.collectors import akshare_collector, capital_flow_collector, market_http
from src.models.market import MarketCode


def _make_quote_line(volume_ratio: str = "1.25") -> str:
    """造一条腾讯报价行(≥50 字段),index 49 为量比。"""
    f = ["0"] * 60
    f[1] = "贵州茅台"
    f[2] = "600519"
    f[3] = "1700.0"  # 现价
    f[4] = "1690.0"  # 昨收
    f[5] = "1695.0"  # 开盘
    f[6] = "12345"  # 成交量
    f[31] = "10.0"  # 涨跌额
    f[32] = "0.59"  # 涨跌幅
    f[33] = "1710.0"  # 最高
    f[34] = "1680.0"  # 最低
    f[35] = "1700.0/12345/2000000"  # 价/量/额
    f[38] = "0.5"  # 换手率
    f[39] = "30.0"  # 市盈率
    f[44] = "1.0e12"
    f[45] = "2.0e12"
    f[49] = volume_ratio  # 量比
    return 'v_sh600519="' + "~".join(f) + '";'


def test_tencent_quote_parses_volume_ratio():
    """腾讯报价应解析出量比(parts[49]),供价格提醒直接用、免拉 K线。"""
    parsed = akshare_collector._parse_tencent_line(_make_quote_line("1.25"))
    assert parsed is not None
    assert parsed["volume_ratio"] == 1.25


def test_tencent_quotes_cached(monkeypatch):
    """同一批 symbols 的报价在 TTL 内应命中缓存,不重复联网。"""
    calls = {"n": 0}
    line = _make_quote_line("1.1")

    def fake_market_get(url, **kwargs):
        calls["n"] += 1
        return line.encode("gbk")

    monkeypatch.setattr(akshare_collector, "market_get", fake_market_get)
    akshare_collector._fetch_tencent_quotes(["sh600519"])
    akshare_collector._fetch_tencent_quotes(["sh600519"])
    assert calls["n"] == 1, f"第二次应命中报价缓存,实际联网 {calls['n']} 次"


def test_capital_flow_cached(monkeypatch):
    """资金流为日级数据,同一只在 TTL 内应命中缓存,不重复联网。"""
    monkeypatch.setattr(capital_flow_collector, "_use_marketdata_cf", lambda: False)
    calls = {"n": 0}
    fake = {
        "data": {
            "code": "600519",
            "name": "贵州茅台",
            "klines": ["2026-06-18,100,1,2,3,4,5,6,7,8,9,10,11,12,13,14"],
        }
    }

    def fake_market_get(url, **kwargs):
        calls["n"] += 1
        return fake

    monkeypatch.setattr(capital_flow_collector, "market_get", fake_market_get)
    c = capital_flow_collector.CapitalFlowCollector(MarketCode.CN)
    assert c.get_capital_flow("600519") is not None
    assert c.get_capital_flow("600519") is not None
    assert calls["n"] == 1, f"第二次应命中资金流缓存,实际联网 {calls['n']} 次"


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
