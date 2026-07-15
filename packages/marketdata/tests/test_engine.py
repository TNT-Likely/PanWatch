from marketdata.cache import TTLCache
from marketdata.defaults import InMemoryMetricsSink
from marketdata.engine import Engine
from marketdata.ports import SourceConfig
from marketdata.types import Request


class FakeVendor:
    def __init__(self, name, behavior):
        self.name, self.behavior, self.supports_markets = name, behavior, set()

    def fetch(self, symbols, config):
        if self.behavior == "raise":
            raise RuntimeError("boom")
        if self.behavior == "empty":
            return []
        return [{"symbol": symbols[0].code, "v": self.name}]


class FakeConfig:
    def __init__(self, srcs):
        self._srcs = srcs

    def sources_for(self, datatype, market):
        return list(self._srcs)


def _engine(vendors, srcs, metrics=None):
    return Engine(
        datatype="quote", vendors=vendors, config=FakeConfig(srcs),
        metrics=metrics or InMemoryMetricsSink(), cache=TTLCache(5.0), default_ttl=5.0,
    )


def _req():
    return Request(symbols=("600519",), market="CN")


def test_first_success_wins():
    e = _engine({"a": FakeVendor("a", "ok"), "b": FakeVendor("b", "ok")},
                [SourceConfig(vendor="a", priority=1), SourceConfig(vendor="b", priority=2)])
    r = e.fetch(_req())
    assert r.ok and r.vendor == "a" and r.data[0]["v"] == "a"


def test_failover_empty_then_raise_then_ok():
    e = _engine({"a": FakeVendor("a", "empty"), "b": FakeVendor("b", "raise"), "c": FakeVendor("c", "ok")},
                [SourceConfig(vendor="a", priority=1), SourceConfig(vendor="b", priority=2), SourceConfig(vendor="c", priority=3)])
    r = e.fetch(_req())
    assert r.ok and r.vendor == "c"


def test_all_fail_returns_not_ok():
    e = _engine({"a": FakeVendor("a", "empty")}, [SourceConfig(vendor="a", priority=1)])
    assert e.fetch(_req()).ok is False


def test_market_filter_skips_unsupported():
    v = FakeVendor("a", "ok")
    v.supports_markets = {"US"}   # 不支持 CN
    assert _engine({"a": v}, [SourceConfig(vendor="a", priority=1)]).fetch(_req()).ok is False


def test_cache_hit_skips_second_call():
    v = FakeVendor("a", "ok")
    calls = {"n": 0}
    inner = v.fetch
    v.fetch = lambda s, c: (calls.__setitem__("n", calls["n"] + 1), inner(s, c))[1]
    e = _engine({"a": v}, [SourceConfig(vendor="a", priority=1)])
    e.fetch(_req()); e.fetch(_req())
    assert calls["n"] == 1


def test_metrics_recorded():
    m = InMemoryMetricsSink()
    _engine({"a": FakeVendor("a", "ok")}, [SourceConfig(vendor="a", priority=1)], metrics=m).fetch(_req())
    assert m.snapshot()["a"]["success_rate"] == 1.0


def test_priority_resort_when_config_unsorted():
    e = _engine({"a": FakeVendor("a", "ok"), "b": FakeVendor("b", "ok")},
                [SourceConfig(vendor="b", priority=2), SourceConfig(vendor="a", priority=1)])
    r = e.fetch(_req())
    assert r.ok and r.vendor == "a"
