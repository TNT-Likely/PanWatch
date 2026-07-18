"""统一 HTTP 工具:直连(trust_env=False)+ 按 host 节流 + 退避重试 + 来源标记。

默认 trust_env=False —— 生产 LAN 代理会拦国内行情/数据接口,必须直连。
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

_FETCH_SOURCE: contextvars.ContextVar[str] = contextvars.ContextVar("fetch_source", default="")


@contextmanager
def fetch_source(name: str):
    """标注取数来源,写入失败日志便于定位触发方。"""
    token = _FETCH_SOURCE.set(name or "")
    try:
        yield
    finally:
        _FETCH_SOURCE.reset(token)


def source_suffix() -> str:
    src = _FETCH_SOURCE.get()
    return f" [src={src}]" if src else ""


# 失败原因收集:默认 None = 不收集(生产热路径零开销)。数据源"测试"按钮用 capture_errors()
# 包住取数调用,把 market_get / vendor 的真实失败原因收上来透到 UI,而不是只显示"无数据"。
_ERROR_SINK: contextvars.ContextVar[list | None] = contextvars.ContextVar("md_error_sink", default=None)


@contextmanager
def capture_errors():
    """进入后,market_get / record_error 的失败原因会被收集到 yield 出的 list。"""
    errs: list[str] = []
    token = _ERROR_SINK.set(errs)
    try:
        yield errs
    finally:
        _ERROR_SINK.reset(token)


def record_error(msg: str) -> None:
    """把一条失败原因写入当前 capture_errors 上下文(无上下文则忽略)。
    供 vendor 自己 catch 异常(如 yfinance 走库、不经 market_get)时也能上报真因。"""
    sink = _ERROR_SINK.get()
    if sink is not None and msg:
        sink.append(msg)


_THROTTLE_LOCK = threading.Lock()
_last_call: dict[str, float] = {}

# 全局默认代理:vendor 未显式传 proxy 时的回退。默认 None = 直连(trust_env=False)。
# 宿主可调 set_default_proxy() 让全部行情抓取走某个代理(如本机被企业 MITM 代理拦截、
# 需统一走一个不做 MITM 的 NAS 代理的场景)。空/None 恢复直连(不影响那些"代理会拦国内接口"的部署)。
_DEFAULT_PROXY: str | None = None


def set_default_proxy(proxy: str | None) -> None:
    """设置全局默认代理(vendor 未显式传 proxy 时回退用它);传空/None 恢复直连。"""
    global _DEFAULT_PROXY
    _DEFAULT_PROXY = (proxy or "").strip() or None


def get_default_proxy() -> str | None:
    return _DEFAULT_PROXY


def throttle(host_key: str, min_interval_s: float) -> None:
    """保证对同一 host 的请求间隔 ≥ min_interval_s。"""
    if min_interval_s <= 0:
        return
    with _THROTTLE_LOCK:
        wait = min_interval_s - (time.time() - _last_call.get(host_key, 0.0))
        if wait > 0:
            time.sleep(wait)
        _last_call[host_key] = time.time()


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
    parse: str = "text",   # "text" | "json" | "content"
    encoding: str | None = None,
    symbol: str = "",
    log_label: str = "",
    raise_for_status: bool = True,
    trust_env: bool = False,
    follow_redirects: bool = True,
    verify: bool = True,
    proxy: str | None = None,
) -> Any | None:
    """直连 + 按 host 节流 + 退避重试。成功返回解析结果,失败返回 None 并打带来源日志。

    proxy: 显式代理(如市场扫描场景需要走代理);仅在给了值时传给 httpx.Client,
    避免与 trust_env 冲突。未显式传 proxy 时回退到全局默认代理(set_default_proxy);
    两者都无则直连(与此前行为一致)。
    """
    effective_proxy = proxy if proxy is not None else _DEFAULT_PROXY
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
                **({"proxy": effective_proxy} if effective_proxy else {}),
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
        record_error(f"{label}{sym}: {type(last_err).__name__}: {last_err}")
    return None
