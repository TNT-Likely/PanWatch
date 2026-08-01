"""首页指数 spark(近20日收盘) 注入 + 60s 缓存 + fail-soft 测试"""
import asyncio

import src.web.api.market as mkt


class _K:
    """极简 K 线桩,只需 .close 供 spark 取值。"""

    def __init__(self, close: float):
        self.close = close


def test_spark_injected_for_each_index(monkeypatch):
    """每个指数都应附上 spark(近20日收盘价列表),与 get_index_klines 返回的 close 序列一致。"""
    mkt.clear_indices_cache()

    captured_days: dict[str, int] = {}

    def _fake_quotes(tencent_symbols):
        return [
            {
                "symbol": "000001",
                "name": "上证指数",
                "current_price": 3200.0,
                "change_pct": 0.63,
                "change_amount": 20.0,
                "prev_close": 3180.0,
            },
        ]

    class _MD:
        def index_quotes(self, tencent_symbols):
            return _fake_quotes(tencent_symbols)

    def _fake_get_index_klines(code, market, days=120):
        captured_days[code] = days
        return [_K(100 + i) for i in range(20)]

    monkeypatch.setattr(mkt, "get_market_data", lambda: _MD())
    monkeypatch.setattr(mkt, "get_index_klines", _fake_get_index_klines)

    out = asyncio.run(mkt.get_market_indices())

    assert len(out) == len(mkt.MARKET_INDICES)
    for item in out:
        assert item["spark"] == [100 + i for i in range(20)]
    # 近20日收盘:days=20 原样透传
    assert all(d == 20 for d in captured_days.values())


def test_spark_failsoft_on_error_or_unmapped(monkeypatch):
    """单指数取 spark 异常(如美股指数无 INDEX_SECID 映射)→ spark=[],不影响 quote 主体也不抛异常。"""
    mkt.clear_indices_cache()

    class _MD:
        def index_quotes(self, tencent_symbols):
            return [
                {
                    "symbol": "000001",
                    "name": "上证指数",
                    "current_price": 3200.0,
                    "change_pct": 0.63,
                    "change_amount": 20.0,
                    "prev_close": 3180.0,
                },
            ]

    def _boom(code, market, days=120):
        raise RuntimeError(f"boom for {code}")

    monkeypatch.setattr(mkt, "get_market_data", lambda: _MD())
    monkeypatch.setattr(mkt, "get_index_klines", _boom)

    out = asyncio.run(mkt.get_market_indices())

    # quote 主体不受影响:上证指数仍返回正确行情
    sh = next(i for i in out if i["symbol"] == "000001")
    assert sh["current_price"] == 3200.0
    assert sh["spark"] == []
    # 未映射/取数失败的指数(如美股)同样 spark=[] 且仍在结果里
    assert all(i["spark"] == [] for i in out)
    assert len(out) == len(mkt.MARKET_INDICES)


def test_indices_response_cached_60s(monkeypatch):
    """整个 indices 响应加 60s 进程内缓存:短时间内重复调用不应重复拉取 quote/K线。"""
    mkt.clear_indices_cache()

    call_count = {"quotes": 0, "klines": 0}

    class _MD:
        def index_quotes(self, tencent_symbols):
            call_count["quotes"] += 1
            return [
                {
                    "symbol": "000001",
                    "name": "上证指数",
                    "current_price": 3200.0,
                    "change_pct": 0.63,
                    "change_amount": 20.0,
                    "prev_close": 3180.0,
                },
            ]

    def _fake_get_index_klines(code, market, days=120):
        call_count["klines"] += 1
        return [_K(100 + i) for i in range(20)]

    monkeypatch.setattr(mkt, "get_market_data", lambda: _MD())
    monkeypatch.setattr(mkt, "get_index_klines", _fake_get_index_klines)

    out1 = asyncio.run(mkt.get_market_indices())
    out2 = asyncio.run(mkt.get_market_indices())

    assert out1 == out2
    assert call_count["quotes"] == 1
    assert call_count["klines"] == len(mkt.MARKET_INDICES)  # 只在第一次调用时逐指数拉取一次
