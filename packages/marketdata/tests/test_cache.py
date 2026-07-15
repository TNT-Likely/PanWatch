import time

from marketdata.cache import TTLCache


def test_set_get_roundtrip():
    c = TTLCache(default_ttl_sec=10)
    c.set("k", 42)
    assert c.get("k") == 42


def test_expired_key_returns_none():
    c = TTLCache(default_ttl_sec=10)
    c.set("k", 1, ttl_sec=0.01)
    time.sleep(0.02)
    assert c.get("k") is None


def test_zero_ttl_not_cached():
    c = TTLCache()
    c.set("k", 1, ttl_sec=0)
    assert c.get("k") is None


def test_max_size_evicts():
    c = TTLCache(default_ttl_sec=100, max_size=2)
    c.set("a", 1); c.set("b", 2); c.set("c", 3)
    assert len(c) == 2
