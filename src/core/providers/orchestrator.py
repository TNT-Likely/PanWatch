"""数据源主备调度器(Orchestrator)。

Quote 类型为例,调用流:
1. 上层 `await orchestrator.fetch(req)` 传入请求
2. Orchestrator 按 `DataSource.priority` 顺序遍历已启用、且 supports req.market 的 provider
3. 依次 await provider.fetch(req);第一个 success 且非空就返回,并写入 TTL 缓存
4. 全部失败:返回 success=False 带最后一次 error

设计要点:
- TTL 缓存 key 由 ProviderRequest.cache_key 生成,跨调用方共享。Quote 默认 5s TTL,
  足够 Dashboard 防抖,不至于让模拟盘判断脱离实时。
- in-memory 健康度指标:每个 provider 维护最近 N 次的 success/latency,UI 可读。
  持久化到 DB(data_source_runs 表)留到后续 phase。
- Provider 实例缓存:同一 provider 重复 fetch 复用实例,避免每次新建。
- 线程安全:metrics 用 lock 保护;TTL 缓存自身已线程安全。
"""

from __future__ import annotations

import asyncio
import collections
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

from src.core.providers.base import Provider, ProviderRequest, ProviderResponse, QuoteProvider
from src.core.providers.cache import TTLCache

logger = logging.getLogger(__name__)

ProviderFactory = Callable[[dict], Provider]


@dataclass
class _Metrics:
    """单 provider 的滚动健康度统计(最近 100 次)。"""

    window: collections.deque = field(default_factory=lambda: collections.deque(maxlen=100))
    last_error: str = ""
    last_success_at: float = 0.0

    def record(self, success: bool, latency_ms: int, error: str = "") -> None:
        self.window.append((success, latency_ms))
        if success:
            self.last_success_at = time.time()
        elif error:
            self.last_error = error

    def snapshot(self) -> dict:
        total = len(self.window)
        if total == 0:
            return {"count": 0, "success_rate": None, "p50_latency_ms": None}
        success = sum(1 for s, _ in self.window if s)
        latencies = sorted(lat for _, lat in self.window)
        p50 = latencies[len(latencies) // 2]
        return {
            "count": total,
            "success_rate": round(success / total, 3),
            "p50_latency_ms": p50,
            "last_error": self.last_error,
            "last_success_at": self.last_success_at,
        }


class Orchestrator:
    """通用主备调度器。子类化为各 type 的 Orchestrator(目前只暴露 QuoteOrchestrator)。"""

    #: 该 Orchestrator 服务的 DataSource.type
    source_type: str = ""

    #: 默认 TTL,可被 fetch() cache_ttl_sec 覆盖
    default_ttl_sec: float = 5.0

    def __init__(self):
        self._factories: dict[str, ProviderFactory] = {}
        self._instances: dict[str, Provider] = {}  # provider_name -> instance
        self._metrics: dict[str, _Metrics] = {}
        self._metrics_lock = threading.Lock()
        self._cache = TTLCache(default_ttl_sec=self.default_ttl_sec)

    def register(self, name: str, factory: ProviderFactory) -> None:
        """注册 provider 工厂。name 必须与 DataSource.provider 字段一致。"""
        self._factories[name] = factory

    def registered_providers(self) -> list[str]:
        return list(self._factories.keys())

    def _get_or_create_instance(self, name: str, config: dict) -> Provider | None:
        """惰性实例化 provider。如果 config 变更则重建。"""
        # 简化:不区分 config 变更,DataSource 配置变更时调用 clear_instances()
        if name in self._instances:
            return self._instances[name]
        factory = self._factories.get(name)
        if not factory:
            return None
        try:
            instance = factory(config)
        except Exception as e:
            logger.warning(f"[orchestrator] 创建 provider 失败 {name}: {e}")
            return None
        self._instances[name] = instance
        return instance

    def clear_instances(self) -> None:
        """DataSource 配置变更时调用,强制下次重建 provider 实例。"""
        self._instances.clear()

    def _load_enabled_sources(self, market: str) -> list[tuple[str, dict]]:
        """从 DataSource 表读取已启用 provider 列表,按 priority 排序。

        返回 [(provider_name, config_dict)],仅含支持该 market 的 provider。
        """
        # 延迟 import 避免模块循环
        from src.web.database import SessionLocal
        from src.web.models import DataSource

        db = SessionLocal()
        try:
            rows = (
                db.query(DataSource)
                .filter(DataSource.type == self.source_type, DataSource.enabled == True)
                .order_by(DataSource.priority)
                .all()
            )
            out: list[tuple[str, dict]] = []
            for row in rows:
                # provider 必须已注册;未注册的(比如 yfinance 还没接入)跳过
                if row.provider not in self._factories:
                    continue
                instance = self._get_or_create_instance(row.provider, row.config or {})
                if instance is None:
                    continue
                if instance.supports_markets and market not in instance.supports_markets:
                    continue
                out.append((row.provider, row.config or {}))
            return out
        finally:
            db.close()

    def _record(self, provider_name: str, success: bool, latency_ms: int, error: str = "") -> None:
        with self._metrics_lock:
            m = self._metrics.setdefault(provider_name, _Metrics())
            m.record(success, latency_ms, error)

    def health(self) -> dict[str, dict]:
        """返回所有已注册 provider 的健康度快照。"""
        with self._metrics_lock:
            return {name: m.snapshot() for name, m in self._metrics.items()}

    def fetch_sync(
        self,
        req: ProviderRequest,
        *,
        cache_ttl_sec: float | None = None,
    ) -> ProviderResponse:
        """Sync 包装,供同步代码路径调用。

        注意:**只能在没有运行中事件循环的线程使用**。例如 paper_trading_engine
        的 `_scan_sync` 通过 `asyncio.to_thread` 在 worker 线程跑,该线程没有 loop,
        这里 `asyncio.run` 才安全。在 async 函数体内请直接 `await fetch(...)`。
        """
        return asyncio.run(self.fetch(req, cache_ttl_sec=cache_ttl_sec))

    async def fetch(
        self,
        req: ProviderRequest,
        *,
        cache_ttl_sec: float | None = None,
    ) -> ProviderResponse:
        """主备链查询。

        - cache_ttl_sec=None 用 Orchestrator 默认 TTL
        - cache_ttl_sec=0 跳过缓存写入(读取仍尝试,避免重复落库)
        """
        cache_key = req.cache_key(self.source_type)
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached  # 缓存命中,直接返回

        sources = self._load_enabled_sources(req.market)
        if not sources:
            logger.warning(
                f"[orchestrator/{self.source_type}] 没有可用的 provider (market={req.market})"
            )
            return ProviderResponse(
                success=False, error=f"no enabled provider for market={req.market}"
            )

        last_err = ""
        for name, config in sources:
            instance = self._instances.get(name)
            if instance is None:
                continue
            t0 = time.monotonic()
            try:
                resp = await instance.fetch(req)
            except Exception as e:
                latency_ms = int((time.monotonic() - t0) * 1000)
                self._record(name, success=False, latency_ms=latency_ms, error=str(e))
                last_err = str(e)
                logger.warning(
                    f"[orchestrator/{self.source_type}] provider={name} raised: {e}"
                )
                continue

            latency_ms = int((time.monotonic() - t0) * 1000)
            resp.provider = name
            resp.latency_ms = latency_ms

            if resp.success and not resp.is_empty:
                self._record(name, success=True, latency_ms=latency_ms)
                if cache_ttl_sec != 0:
                    self._cache.set(cache_key, resp, ttl_sec=cache_ttl_sec)
                return resp

            err = resp.error or ("empty data" if resp.is_empty else "unknown")
            self._record(name, success=False, latency_ms=latency_ms, error=err)
            last_err = err

        return ProviderResponse(
            success=False, error=last_err or "all providers failed"
        )


class QuoteOrchestrator(Orchestrator):
    source_type = "quote"
    default_ttl_sec = 5.0  # 行情数据 5s 防抖,够 60s 调度器叠加


class KlineOrchestrator(Orchestrator):
    source_type = "kline"
    default_ttl_sec = 60.0  # K 线变更慢,1 分钟缓存


class NewsOrchestrator(Orchestrator):
    source_type = "news"
    default_ttl_sec = 300.0


class CapitalFlowOrchestrator(Orchestrator):
    source_type = "capital_flow"
    default_ttl_sec = 60.0


class EventsOrchestrator(Orchestrator):
    source_type = "events"
    default_ttl_sec = 1800.0  # 公告/事件 30 分钟缓存


class DiscoveryOrchestrator(Orchestrator):
    source_type = "discovery"
    default_ttl_sec = 60.0


class ChartOrchestrator(Orchestrator):
    source_type = "chart"
    default_ttl_sec = 0.0  # 截图按需,不缓存(浏览器渲染本身比较慢但场景一次性)


_quote_orchestrator: QuoteOrchestrator | None = None
_kline_orchestrator: KlineOrchestrator | None = None
_capital_flow_orchestrator: CapitalFlowOrchestrator | None = None
_events_orchestrator: EventsOrchestrator | None = None
_discovery_orchestrator: DiscoveryOrchestrator | None = None
_chart_orchestrator: ChartOrchestrator | None = None
_news_orchestrator: NewsOrchestrator | None = None
_singleton_lock = threading.Lock()


def get_quote_orchestrator() -> QuoteOrchestrator:
    """全局单例。首次调用时注册所有内置 quote provider。"""
    global _quote_orchestrator
    if _quote_orchestrator is not None:
        return _quote_orchestrator
    with _singleton_lock:
        if _quote_orchestrator is not None:
            return _quote_orchestrator
        orch = QuoteOrchestrator()
        # 在这里注册所有内置 Quote provider。新增 provider 时在此追加一行。
        from src.core.providers.quote.tencent import TencentQuoteProvider
        from src.core.providers.quote.yfinance import YFinanceQuoteProvider

        orch.register("tencent", lambda cfg: TencentQuoteProvider(config=cfg))
        orch.register("yfinance", lambda cfg: YFinanceQuoteProvider(config=cfg))
        _quote_orchestrator = orch
        return orch


def get_kline_orchestrator() -> KlineOrchestrator:
    """全局单例。首次调用时注册所有内置 kline provider。"""
    global _kline_orchestrator
    if _kline_orchestrator is not None:
        return _kline_orchestrator
    with _singleton_lock:
        if _kline_orchestrator is not None:
            return _kline_orchestrator
        orch = KlineOrchestrator()
        from src.core.providers.kline.tencent import TencentKlineProvider
        from src.core.providers.kline.tushare import TushareKlineProvider
        from src.core.providers.kline.tdx import TdxKlineProvider
        from src.core.providers.kline.yfinance import YFinanceKlineProvider

        orch.register("tencent", lambda cfg: TencentKlineProvider(config=cfg))
        orch.register("tdx", lambda cfg: TdxKlineProvider(config=cfg))
        orch.register("tushare", lambda cfg: TushareKlineProvider(config=cfg))
        orch.register("yfinance", lambda cfg: YFinanceKlineProvider(config=cfg))
        _kline_orchestrator = orch
        return orch


def get_capital_flow_orchestrator() -> CapitalFlowOrchestrator:
    global _capital_flow_orchestrator
    if _capital_flow_orchestrator is not None:
        return _capital_flow_orchestrator
    with _singleton_lock:
        if _capital_flow_orchestrator is not None:
            return _capital_flow_orchestrator
        orch = CapitalFlowOrchestrator()
        from src.core.providers.capital_flow.eastmoney import EastmoneyCapitalFlowProvider
        orch.register("eastmoney", lambda cfg: EastmoneyCapitalFlowProvider(config=cfg))
        _capital_flow_orchestrator = orch
        return orch


def get_events_orchestrator() -> EventsOrchestrator:
    global _events_orchestrator
    if _events_orchestrator is not None:
        return _events_orchestrator
    with _singleton_lock:
        if _events_orchestrator is not None:
            return _events_orchestrator
        orch = EventsOrchestrator()
        from src.core.providers.events.eastmoney import EastmoneyEventsProvider
        orch.register("eastmoney", lambda cfg: EastmoneyEventsProvider(config=cfg))
        _events_orchestrator = orch
        return orch


def get_discovery_orchestrator() -> DiscoveryOrchestrator:
    global _discovery_orchestrator
    if _discovery_orchestrator is not None:
        return _discovery_orchestrator
    with _singleton_lock:
        if _discovery_orchestrator is not None:
            return _discovery_orchestrator
        orch = DiscoveryOrchestrator()
        from src.core.providers.discovery.eastmoney import EastmoneyDiscoveryProvider
        orch.register("eastmoney", lambda cfg: EastmoneyDiscoveryProvider(config=cfg))
        _discovery_orchestrator = orch
        return orch


def get_chart_orchestrator() -> ChartOrchestrator:
    """Chart orchestrator 暂为空实现 — 截图链路保留 ScreenshotCollector 直接使用,
    后续如有多 provider 需求再注册具体 ChartProvider 子类。"""
    global _chart_orchestrator
    if _chart_orchestrator is not None:
        return _chart_orchestrator
    with _singleton_lock:
        if _chart_orchestrator is not None:
            return _chart_orchestrator
        _chart_orchestrator = ChartOrchestrator()
        return _chart_orchestrator


def get_news_orchestrator() -> NewsOrchestrator:
    """News orchestrator 暂为空实现 — 现有 NewsCollector.from_database() 已经做了
    DB 驱动的多源聚合,不直接走单源 fallback 链。后续如要把 NewsCollector 拆成多
    个独立 Provider 在此注册。"""
    global _news_orchestrator
    if _news_orchestrator is not None:
        return _news_orchestrator
    with _singleton_lock:
        if _news_orchestrator is not None:
            return _news_orchestrator
        _news_orchestrator = NewsOrchestrator()
        return _news_orchestrator
