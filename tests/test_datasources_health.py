import src.web.api.datasources as ds


def test_to_response_includes_health_and_engine_attached(monkeypatch):
    from types import SimpleNamespace

    row = SimpleNamespace(id=1, name="腾讯行情", type="quote", provider="tencent",
                          config={}, enabled=True, priority=1,
                          supports_batch=True, test_symbols=[])
    health_map = {"tencent": {"success_rate": 0.98, "p50_latency_ms": 90,
                              "last_error": "", "count": 30}}
    out = ds._to_response(row, health_map)
    assert out["engine_attached"] is True          # quote 类型已接入
    assert out["health"]["success_rate"] == 0.98

    row2 = SimpleNamespace(id=2, name="东财K线", type="kline", provider="eastmoney",
                           config={}, enabled=True, priority=1,
                           supports_batch=False, test_symbols=[])
    out2 = ds._to_response(row2, health_map)
    assert out2["engine_attached"] is True          # kline 已接入(Phase2 Task2)
    assert out2["health"] is None                    # 无该 provider 指标 → null,不编数据
