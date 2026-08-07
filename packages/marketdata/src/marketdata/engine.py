"""数据源主备调度器:按 ConfigProvider 的优先级链取数,首个非空即返回。

- 缓存(唯一一层,vendor 内不再各自缓存)。
- 每次取数经 MetricsSink 记录 (vendor, ok, latency, error)。
- 通过 ConfigProvider 拿源、通过注入 vendors 取实例:不依赖 web/DB。
"""

from __future__ import annotations

import logging
import threading
import time

from marketdata.cache import TTLCache
from marketdata.http import record_error
from marketdata.keypool import KeyPool
from marketdata.ports import ConfigProvider, MetricsSink
from marketdata.symbol import Market, Symbol
from marketdata.types import Request, Response
from marketdata.vendors.base import Vendor

logger = logging.getLogger(__name__)


class Engine:
    def __init__(self, *, datatype: str, vendors: dict[str, Vendor],
                 config: ConfigProvider, metrics: MetricsSink,
                 cache: TTLCache, default_ttl: float):
        self.datatype = datatype
        self.vendors = vendors
        self.config = config
        self.metrics = metrics
        self.cache = cache
        self.default_ttl = default_ttl
        # 每个源(vendor)一个 KeyPool 实例, 进程级复用(按 vendor 名索引)
        self._keypools: dict[str, KeyPool] = {}
        self._kp_lock = threading.Lock()

    def _get_keypool(self, vendor: str, key_pool: list[str]) -> KeyPool | None:
        if not key_pool:
            return None
        with self._kp_lock:
            kp = self._keypools.get(vendor)
            if kp is None:
                kp = KeyPool(list(key_pool))
                self._keypools[vendor] = kp
            elif set(kp._state.keys()) != set(key_pool):
                # key 池配置变更: 重建(保留旧用量无意义, 简单重建)
                kp = KeyPool(list(key_pool))
                self._keypools[vendor] = kp
            return kp

    def fetch(self, req: Request, *, cache_ttl_sec: float | None = None, min_count: int = 1) -> Response:
        key = req.cache_key(self.datatype)
        cached = self.cache.get(key)
        if cached is not None:
            return cached

        market = req.market
        syms = [Symbol(Market(market), c) for c in req.symbols]
        sources = sorted(self.config.sources_for(self.datatype, market), key=lambda s: s.priority)

        last_err = ""
        best: Response | None = None
        for src in sources:
            if not src.enabled:
                continue
            vendor = self.vendors.get(src.vendor)
            if vendor is None:
                continue
            if vendor.supports_markets and market not in vendor.supports_markets:
                continue

            kp = self._get_keypool(src.vendor, src.key_pool)
            # 多 key 池: 逐个 key 尝试, 限流自动切下一个
            attempts = (kp.size if kp else 1)
            key_tried = 0
            success = False
            while key_tried < attempts:
                key_tried += 1
                api_key = kp.pick() if kp else None
                # 注入 api_key 到 vendor 调用配置(约定 vendor 从 config["api_key"] 读)
                base_cfg = dict(src.config or {})
                if api_key:
                    base_cfg["api_key"] = api_key
                call_config = {**base_cfg, "days": req.limit, **dict(req.extra)}

                t0 = time.monotonic()
                try:
                    data = vendor.fetch(syms, call_config)
                except Exception as e:
                    latency = int((time.monotonic() - t0) * 1000)
                    err = str(e)
                    # 限流/凭证失效均触发 key 切换: 401/403 凭证错, 429 限流, 以及含 rate/quota 等关键字
                    rl = (
                        "401" in err or "403" in err
                        or any(c in err for c in ("429", "rate", "Rate", "quota", "Quota", "Too Many", "limit exceeded", "apikey", "incorrect", "unauthorized", "Unauthorized"))
                    )
                    if kp and api_key:
                        kp.mark_failure(api_key, rate_limited=rl)
                    self.metrics.record(vendor=src.vendor, datatype=self.datatype, market=market,
                                        ok=False, count=0, latency_ms=latency, error=err)
                    last_err = err
                    logger.warning(f"[marketdata/{self.datatype}] vendor={src.vendor} key={api_key[:8] if api_key else '-'} raised: {e}")
                    record_error(f"{src.vendor}: {type(e).__name__}: {e}")
                    if kp and rl:
                        continue  # 限流: 换下一个 key 重试
                    break  # 其他异常: 跳到下一个源
                else:
                    latency = int((time.monotonic() - t0) * 1000)
                    if kp and api_key:
                        kp.mark_success(api_key)
                    if data:
                        self.metrics.record(vendor=src.vendor, datatype=self.datatype, market=market,
                                            ok=True, count=len(data), latency_ms=latency)
                        resp = Response(ok=True, data=data, vendor=src.vendor, latency_ms=latency)
                        if len(data) >= min_count:
                            ttl = cache_ttl_sec if cache_ttl_sec is not None else self.default_ttl
                            self.cache.set(key, resp, ttl_sec=ttl)
                            return resp
                        if best is None or len(data) > len(best.data):
                            best = resp
                    else:
                        self.metrics.record(vendor=src.vendor, datatype=self.datatype, market=market,
                                            ok=False, count=0, latency_ms=latency, error="empty")
                        last_err = "empty"
                    success = True
                    break  # 该源已成功取数(即使不足 min_count, 也走 best 候选逻辑)
            if success:
                continue

        if best is not None:
            ttl = cache_ttl_sec if cache_ttl_sec is not None else self.default_ttl
            self.cache.set(key, best, ttl_sec=ttl)
            return best
        return Response(ok=False, data=None, error=last_err or "no enabled provider")
