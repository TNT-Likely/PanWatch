from marketdata.defaults import InMemoryMetricsSink, StaticConfigProvider
from marketdata.ports import SourceConfig


def test_static_config_returns_sorted_enabled():
    cp = StaticConfigProvider({
        "quote": [
            SourceConfig(vendor="b", priority=2),
            SourceConfig(vendor="a", priority=1),
            SourceConfig(vendor="x", priority=0, enabled=False),
        ]
    })
    got = [s.vendor for s in cp.sources_for("quote", "CN")]
    assert got == ["a", "b"]  # 已按 priority 排序、禁用的被剔除


def test_static_config_unknown_type_empty():
    cp = StaticConfigProvider({})
    assert cp.sources_for("kline", "CN") == []


def test_metrics_snapshot():
    m = InMemoryMetricsSink()
    m.record(vendor="a", datatype="quote", market="CN", ok=True, count=3, latency_ms=100)
    m.record(vendor="a", datatype="quote", market="CN", ok=False, count=0, latency_ms=200, error="boom")
    snap = m.snapshot()["a"]
    assert snap["count"] == 2
    assert snap["success_rate"] == 0.5
    assert snap["last_error"] == "boom"
