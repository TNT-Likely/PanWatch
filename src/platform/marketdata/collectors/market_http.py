"""行情/数据采集的统一 HTTP 工具。

把散落在各 collector 的样板收敛到一处,避免每个文件各写一套且各有缺漏:
- **走系统代理**:默认 trust_env=True,遵循进程 env 的 HTTP_PROXY/NO_PROXY(由 apply_proxy_env 按 UI 的 http_proxy 设置)。没配代理时即直连。
- **按 host 节流**:同一域名请求最小间隔,平滑顺序/并发突发(第三方批量突发会限流)。
- **退避重试**:空响应/异常退避 + 抖动重试。
- **调用来源标记**:全项目共享一个 contextvar,失败日志带 [src=xxx],定位是哪个任务触发。

来源标记是全局共享的:任何调度入口 `with fetch_source("xxx"):` 包裹后,
该任务内所有 collector(K线/报价/资金流/...)的失败日志都会带上同一来源。
asyncio.to_thread 会传播 contextvars,异步调度里设置也能透到 worker 线程。
"""

from __future__ import annotations

import contextvars
import logging
import random
import threading
import time
from contextlib import contextmanager
from typing import Any

import httpx

logger = logging.getLogger(__name__)


# ── 调用来源标记(全局共享)──────────────────────────────────────────────
_FETCH_SOURCE: contextvars.ContextVar[str] = contextvars.ContextVar(
    "fetch_source", default=""
)


@contextmanager
def fetch_source(name: str):
    """标注当前取数的调用来源,写入失败日志便于定位触发方。"""
    token = _FETCH_SOURCE.set(name or "")
    try:
        yield
    finally:
        _FETCH_SOURCE.reset(token)


def source_suffix() -> str:
    src = _FETCH_SOURCE.get()
    return f" [src={src}]" if src else ""


# ── 按 host 进程级节流 ───────────────────────────────────────────────────
_THROTTLE_LOCK = threading.Lock()
_last_call: dict[str, float] = {}


def throttle(host_key: str, min_interval_s: float) -> None:
    """保证对同一 host 的请求间隔 ≥ min_interval_s,平滑顺序/并发突发。"""
    if min_interval_s <= 0:
        return
    with _THROTTLE_LOCK:
        wait = min_interval_s - (time.time() - _last_call.get(host_key, 0.0))
        if wait > 0:
            time.sleep(wait)
        _last_call[host_key] = time.time()


# ── 统一同步 GET ─────────────────────────────────────────────────────────
def market_get(
    url: str,
    *,
    host_key: str,
    params: dict | None = None,
    headers: dict | None = None,
    min_interval_s: float = 0.0,
    timeout: float = 10.0,
    retries: int = 2,
    backoff: float = 0.4,
    jitter: float = 0.25,
    parse: str = "text",  # "text" | "json" | "content"
    encoding: str | None = None,  # 强制解码(如 "gbk")
    symbol: str = "",
    log_label: str = "",
    raise_for_status: bool = True,
    trust_env: bool = True,  # 遵循进程 env 代理(HTTP_PROXY/NO_PROXY),由 apply_proxy_env 统一设
    follow_redirects: bool = True,
    verify: bool = True,
) -> Any | None:
    """按系统代理(env)+ host 节流 + 退避重试。成功返回解析结果,失败返回 None 并打带来源的日志。"""
    last_err: Any = None
    for attempt in range(max(1, retries + 1)):
        throttle(host_key, min_interval_s)
        try:
            with httpx.Client(
                follow_redirects=follow_redirects,
                timeout=timeout + attempt * 4,
                headers=headers,
                trust_env=trust_env,
                verify=verify,
            ) as client:
                resp = client.get(url, params=params)
                if raise_for_status:
                    resp.raise_for_status()
                if parse == "json":
                    return resp.json()
                if parse == "content":
                    return resp.content
                if encoding:
                    return resp.content.decode(encoding, errors="ignore")
                return resp.text
        except Exception as e:
            last_err = e
        if attempt < retries:
            time.sleep(backoff * (attempt + 1) + random.uniform(0, jitter))

    if last_err is not None:
        label = log_label or host_key
        sym = f" symbol={symbol}" if symbol else ""
        logger.warning(f"{label} 获取失败{sym}: {last_err}{source_suffix()}")
    return None


# ── 轻量 TTL 缓存 ────────────────────────────────────────────────────────
# 与 src/core/providers/cache.py 等价,但定义在采集层最底层模块,供各 collector
# 直接复用——避免 collector 反向 import providers 包触发循环依赖。
class TTLCache:
    """单进程内存 TTL 缓存,线程安全,过期 key 在下次 get 时被动剔除。"""

    def __init__(self, default_ttl_sec: float = 20.0, max_size: int = 2048):
        self._default_ttl = default_ttl_sec
        self._max_size = max_size
        self._lock = threading.Lock()
        self._store: dict[str, tuple[Any, float]] = {}

    def get(self, key: str) -> Any | None:
        now = time.monotonic()
        with self._lock:
            entry = self._store.get(key)
            if not entry:
                return None
            value, expires_at = entry
            if expires_at <= now:
                del self._store[key]
                return None
            return value

    def set(self, key: str, value: Any, ttl_sec: float | None = None) -> None:
        ttl = ttl_sec if ttl_sec is not None else self._default_ttl
        if ttl <= 0:
            return  # 显式不缓存
        expires = time.monotonic() + ttl
        with self._lock:
            if len(self._store) >= self._max_size and key not in self._store:
                oldest = min(self._store.items(), key=lambda kv: kv[1][1])
                del self._store[oldest[0]]
            self._store[key] = (value, expires)

    def clear(self) -> None:
        with self._lock:
            self._store.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._store)
