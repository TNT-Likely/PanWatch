"""市场数据采集器必须绕过环境代理(trust_env=False)。

生产环境为 Telegram/AI 配置了 HTTP_PROXY/HTTPS_PROXY(LAN Clash 代理),
但该代理无法服务行情接口(腾讯 gtimg / 东方财富 push2his),会回 "Server
disconnected without sending a response."。行情采集应直连,不走 env 代理。
"""

from __future__ import annotations

from src.collectors import kline_collector
from src.models.market import MarketCode


def test_kline_collector_market_data_bypasses_env_proxy(monkeypatch):
    """行情采集创建的所有 httpx.Client 必须 trust_env=False(直连、不吃 env 代理)。"""
    captured: list[dict] = []

    class _FakeClient:
        def __init__(self, **kwargs):
            captured.append(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def get(self, *args, **kwargs):
            raise RuntimeError("network disabled in test")

    # 屏蔽真实网络与退避 sleep,保持单测快速、离线
    monkeypatch.setattr(kline_collector.httpx, "Client", _FakeClient)
    monkeypatch.setattr(kline_collector.time, "sleep", lambda *_: None)

    # days>=500 会同时触发 腾讯主路径 + 东方财富长历史回退,覆盖多个 client
    kline_collector.KlineCollector(MarketCode.CN).get_klines("000001", days=600)

    assert captured, "应至少创建一个 httpx.Client"
    for kw in captured:
        assert kw.get("trust_env") is False, f"行情 client 必须 trust_env=False,实际: {kw}"


def test_get_klines_retries_tencent_on_empty(monkeypatch):
    """腾讯首次空响应(突发限流)应退避重试,第二次成功即返回——不因瞬时空回包而失败。"""
    monkeypatch.setattr(kline_collector, "_use_marketdata_kline", lambda: False)
    calls = {"n": 0}
    good = (
        'kline_dayqfq={"code":0,"data":{"sz000001":'
        '{"day":[["2026-06-18","10","11","12","9","1000"]]}}}'
    )

    class _Resp:
        def __init__(self, text):
            self.text = text

    class _FakeClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def get(self, *args, **kwargs):
            calls["n"] += 1
            # 第 1 次回空 body(json.loads 解析为空),第 2 次给正常数据
            return _Resp("kline_dayqfq=" if calls["n"] == 1 else good)

    monkeypatch.setattr(kline_collector.httpx, "Client", _FakeClient)
    monkeypatch.setattr(kline_collector.time, "sleep", lambda *_: None)
    # 隔离:东财兜底置空,只验证腾讯自身重试自愈
    monkeypatch.setattr(kline_collector, "_fetch_eastmoney_klines", lambda *a, **k: [])

    out = kline_collector.KlineCollector(MarketCode.CN).get_klines("000001", days=60)
    assert calls["n"] == 2, f"应重试一次后成功,实际请求 {calls['n']} 次"
    assert len(out) == 1 and out[0].date == "2026-06-18"
