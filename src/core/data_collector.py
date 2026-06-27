"""统一数据源管理器"""

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from src.web.database import SessionLocal
from src.web.models import DataSource
from src.models.market import MarketCode

logger = logging.getLogger(__name__)


@dataclass
class CollectorResult:
    """采集结果"""

    success: bool
    data: Any = None
    count: int = 0
    duration_ms: int = 0
    error: str = ""
    source_name: str = ""
    source_provider: str = ""


@dataclass
class CollectorLog:
    """采集日志"""

    timestamp: datetime
    source_name: str
    source_type: str
    action: str  # "start" / "success" / "error"
    message: str
    duration_ms: int = 0
    count: int = 0


class DataCollectorManager:
    """
    统一数据源管理器

    提供统一的数据采集接口，支持：
    - 从数据库配置加载数据源
    - 记录采集日志
    - 批量/单个采集
    """

    # 数据源类型 -> (provider -> 采集器工厂)
    COLLECTOR_FACTORIES: dict[str, dict[str, Callable]] = {}

    def __init__(self):
        self.logs: list[CollectorLog] = []
        self._register_collectors()

    def _register_collectors(self):
        """注册所有采集器"""
        from src.collectors.news_collector import (
            XueqiuNewsCollector,
            EastMoneyStockNewsCollector,
            EastMoneyNewsCollector,
        )
        from src.collectors.kline_collector import KlineCollector
        from src.collectors.capital_flow_collector import CapitalFlowCollector
        from src.collectors.akshare_collector import AkshareCollector
        from src.collectors.events_collector import EastMoneyEventsCollector

        self.COLLECTOR_FACTORIES = {
            "news": {
                "xueqiu": lambda cfg: XueqiuNewsCollector(
                    cookies=cfg.get("cookies", "")
                ),
                "eastmoney_news": lambda cfg: EastMoneyStockNewsCollector(),
                "eastmoney": lambda cfg: EastMoneyNewsCollector(),
            },
            "kline": {
                "tencent": lambda cfg: ("tencent", KlineCollector),
            },
            "capital_flow": {
                "eastmoney": lambda cfg: CapitalFlowCollector(MarketCode.CN),
            },
            "quote": {
                "tencent": lambda cfg: AkshareCollector(MarketCode.CN),
            },
            "chart": {
                "xueqiu": lambda cfg: ("xueqiu", cfg),
                "eastmoney": lambda cfg: ("eastmoney", cfg),
            },
            "events": {
                "eastmoney": lambda cfg: EastMoneyEventsCollector(),
            },
        }

    def _log(
        self,
        source_name: str,
        source_type: str,
        action: str,
        message: str,
        duration_ms: int = 0,
        count: int = 0,
    ):
        """记录日志"""
        log = CollectorLog(
            timestamp=datetime.now(),
            source_name=source_name,
            source_type=source_type,
            action=action,
            message=message,
            duration_ms=duration_ms,
            count=count,
        )
        self.logs.append(log)

        # 同时输出到 logger:error 走 WARNING；start/success 是底层心跳,降到 DEBUG。
        # UI 日志板始终从 self.logs 读完整记录,不受这里影响。
        if action == "error":
            logger.warning(f"[{source_name}] {message}")
        else:
            logger.debug(f"[{source_name}] {message}")

    def get_logs(self) -> list[dict]:
        """获取日志（用于 UI 展示）"""
        return [
            {
                "timestamp": log.timestamp.strftime("%H:%M:%S"),
                "source_name": log.source_name,
                "source_type": log.source_type,
                "action": log.action,
                "message": log.message,
                "duration_ms": log.duration_ms,
                "count": log.count,
            }
            for log in self.logs
        ]

    def clear_logs(self):
        """清空日志"""
        self.logs = []

    def get_enabled_sources(self, source_type: str) -> list[DataSource]:
        """获取指定类型的已启用数据源"""
        db = SessionLocal()
        try:
            return (
                db.query(DataSource)
                .filter(DataSource.type == source_type, DataSource.enabled == True)
                .order_by(DataSource.priority)
                .all()
            )
        finally:
            db.close()

    def get_source_by_id(self, source_id: int) -> DataSource | None:
        """根据 ID 获取数据源"""
        db = SessionLocal()
        try:
            return db.query(DataSource).filter(DataSource.id == source_id).first()
        finally:
            db.close()

    def _get_stock_names(self, symbols: list[str]) -> dict[str, str]:
        """获取股票代码到名称的映射"""
        from src.web.models import Stock

        # 默认测试股票名称映射
        default_names = {
            "601127": "赛力斯",
            "600519": "贵州茅台",
            "000001": "平安银行",
            "000858": "五粮液",
            "300750": "宁德时代",
        }

        db = SessionLocal()
        try:
            stocks = db.query(Stock).filter(Stock.symbol.in_(symbols)).all()
            result = {s.symbol: s.name for s in stocks}

            # 对于数据库中没有的股票，使用默认名称
            for symbol in symbols:
                if symbol not in result and symbol in default_names:
                    result[symbol] = default_names[symbol]

            return result
        except Exception as e:
            logger.warning(f"获取股票名称失败: {e}")
            # 返回默认名称
            return {s: default_names.get(s, s) for s in symbols if s in default_names}
        finally:
            db.close()

    async def collect_news(
        self, symbols: list[str], hours: int = 12
    ) -> CollectorResult:
        """采集新闻（使用所有已启用的新闻数据源）"""
        from src.collectors.news_collector import NewsCollector

        start_time = datetime.now()
        self._log("新闻采集", "news", "start", f"开始采集 {len(symbols)} 只股票的新闻")

        try:
            collector = NewsCollector.from_database()
            news_list = await collector.fetch_all(symbols=symbols, since_hours=hours)

            duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)
            self._log(
                "新闻采集",
                "news",
                "success",
                f"采集完成，共 {len(news_list)} 条",
                duration_ms=duration_ms,
                count=len(news_list),
            )

            return CollectorResult(
                success=True,
                data=news_list,
                count=len(news_list),
                duration_ms=duration_ms,
            )
        except Exception as e:
            duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)
            self._log("新闻采集", "news", "error", str(e), duration_ms=duration_ms)
            return CollectorResult(success=False, error=str(e), duration_ms=duration_ms)

    async def collect_kline(
        self, symbol: str, market: str = "CN", days: int = 60
    ) -> CollectorResult:
        """采集 K 线数据"""
        from src.collectors.kline_collector import KlineCollector
        from src.models.market import MarketCode

        start_time = datetime.now()
        self._log("K线数据", "kline", "start", f"获取 {symbol} 的 K 线数据")

        try:
            market_code = MarketCode(market)
            collector = KlineCollector(market_code)
            summary = collector.get_kline_summary(symbol)

            duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)

            if summary.get("error"):
                self._log(
                    "K线数据",
                    "kline",
                    "error",
                    summary["error"],
                    duration_ms=duration_ms,
                )
                return CollectorResult(
                    success=False, error=summary["error"], duration_ms=duration_ms
                )

            self._log(
                "K线数据",
                "kline",
                "success",
                f"获取成功，最新收盘价 {summary.get('last_close', 'N/A')}",
                duration_ms=duration_ms,
            )

            return CollectorResult(
                success=True,
                data=summary,
                count=1,
                duration_ms=duration_ms,
            )
        except Exception as e:
            duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)
            self._log("K线数据", "kline", "error", str(e), duration_ms=duration_ms)
            return CollectorResult(success=False, error=str(e), duration_ms=duration_ms)

    async def collect_capital_flow(self, symbol: str) -> CollectorResult:
        """采集资金流向"""
        from src.collectors.capital_flow_collector import CapitalFlowCollector

        start_time = datetime.now()
        self._log("资金流向", "capital_flow", "start", f"获取 {symbol} 的资金流向")

        try:
            collector = CapitalFlowCollector(MarketCode.CN)
            data = collector.get_capital_flow(symbol)

            duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)

            if not data:
                self._log(
                    "资金流向",
                    "capital_flow",
                    "error",
                    "无数据",
                    duration_ms=duration_ms,
                )
                return CollectorResult(
                    success=False, error="无数据", duration_ms=duration_ms
                )

            self._log(
                "资金流向",
                "capital_flow",
                "success",
                f"获取成功，主力净流入 {data.main_net_inflow / 10000:.2f}万",
                duration_ms=duration_ms,
            )

            return CollectorResult(
                success=True,
                data=data,
                count=1,
                duration_ms=duration_ms,
            )
        except Exception as e:
            duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)
            self._log(
                "资金流向", "capital_flow", "error", str(e), duration_ms=duration_ms
            )
            return CollectorResult(success=False, error=str(e), duration_ms=duration_ms)

    async def collect_quote(self, symbols: list[str]) -> CollectorResult:
        """采集实时行情"""
        from src.collectors.akshare_collector import AkshareCollector

        start_time = datetime.now()
        self._log("实时行情", "quote", "start", f"获取 {len(symbols)} 只股票的行情")

        try:
            collector = AkshareCollector(MarketCode.CN)
            stocks = await collector.get_stock_data(symbols)

            duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)
            self._log(
                "实时行情",
                "quote",
                "success",
                f"获取成功，共 {len(stocks)} 只",
                duration_ms=duration_ms,
                count=len(stocks),
            )

            return CollectorResult(
                success=True,
                data=stocks,
                count=len(stocks),
                duration_ms=duration_ms,
            )
        except Exception as e:
            duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)
            self._log("实时行情", "quote", "error", str(e), duration_ms=duration_ms)
            return CollectorResult(success=False, error=str(e), duration_ms=duration_ms)

    async def test_source(self, source: DataSource) -> CollectorResult:
        """测试单个数据源"""
        test_symbols = source.test_symbols or [
            "601127",
            "600519",
        ]  # 默认测试赛力斯和茅台

        start_time = datetime.now()
        self._log(
            source.name,
            source.type,
            "start",
            f"开始测试，测试股票: {','.join(test_symbols)}",
        )

        try:
            result = await self._test_source_impl(source, test_symbols)
            duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)

            if result.success:
                self._log(
                    source.name,
                    source.type,
                    "success",
                    f"测试成功，获取到 {result.count} 条数据",
                    duration_ms=duration_ms,
                    count=result.count,
                )
            else:
                self._log(
                    source.name,
                    source.type,
                    "error",
                    result.error,
                    duration_ms=duration_ms,
                )

            result.duration_ms = duration_ms
            result.source_name = source.name
            result.source_provider = source.provider
            return result

        except Exception as e:
            duration_ms = int((datetime.now() - start_time).total_seconds() * 1000)
            self._log(
                source.name, source.type, "error", str(e), duration_ms=duration_ms
            )
            return CollectorResult(
                success=False,
                error=str(e),
                duration_ms=duration_ms,
                source_name=source.name,
                source_provider=source.provider,
            )

    async def _test_source_impl(
        self, source: DataSource, test_symbols: list[str]
    ) -> CollectorResult:
        """测试数据源的具体实现"""
        from datetime import timedelta

        if source.type == "news":
            from src.collectors.news_collector import (
                XueqiuNewsCollector,
                EastMoneyStockNewsCollector,
                EastMoneyNewsCollector,
            )

            since = datetime.now() - timedelta(hours=24)
            collector = None

            # 获取测试股票的名称映射（用于搜索 API）
            symbol_names = self._get_stock_names(test_symbols)

            if source.provider == "xueqiu":
                cookies = (source.config or {}).get("cookies", "")
                collector = XueqiuNewsCollector(cookies=cookies)
            elif source.provider == "eastmoney_news":
                collector = EastMoneyStockNewsCollector(symbol_names=symbol_names)
            elif source.provider == "eastmoney":
                collector = EastMoneyNewsCollector()

            if collector:
                news = await collector.fetch_news(symbols=test_symbols, since=since)
                error_msg = ""
                if len(news) == 0:
                    if source.provider == "xueqiu":
                        error_msg = (
                            "无数据：Playwright 采集未返回新闻。"
                            "请确认已安装 Chromium（playwright install chromium），"
                            "或在数据源页点击「检测连通」查看详情"
                        )
                    elif source.provider == "eastmoney_news" and not symbol_names:
                        error_msg = "未找到测试股票的名称，请先添加自选股"
                    else:
                        error_msg = "未获取到新闻数据"
                return CollectorResult(
                    success=len(news) > 0,
                    data=[
                        {
                            "title": n.title[:60],
                            "time": n.publish_time.strftime("%m-%d %H:%M"),
                        }
                        for n in news[:10]
                    ],
                    count=len(news),
                    error=error_msg,
                )

        elif source.type == "kline":
            # 按 provider 路由到对应 Provider,而不是写死走 tencent (KlineCollector)。
            # Tushare/YFinance 的 token 等配置从 source.config 注入。
            return await self._test_kline_source(source, test_symbols)

        elif source.type == "capital_flow":
            from src.collectors.capital_flow_collector import CapitalFlowCollector

            collector = CapitalFlowCollector(MarketCode.CN)
            results = []
            for symbol in test_symbols[:3]:
                data = collector.get_capital_flow(symbol)
                if data:
                    results.append(
                        {
                            "symbol": symbol,
                            "name": data.name,
                            "main_net": data.main_net_inflow,
                            "main_pct": data.main_net_inflow_pct,
                        }
                    )

            return CollectorResult(
                success=len(results) > 0,
                data=results,
                count=len(results),
                error="" if results else "获取资金流向失败",
            )

        elif source.type == "quote":
            # 按 provider 路由到对应 Provider,Tushare(暂无 quote)/YFinance 可正确测到。
            return await self._test_quote_source(source, test_symbols)

        elif source.type == "chart":
            from src.collectors.screenshot_collector import ScreenshotCollector
            import base64

            collector = ScreenshotCollector(config={"extra_wait_ms": 3000})
            try:
                symbol = test_symbols[0] if test_symbols else "601127"
                screenshot = await collector.capture(
                    symbol=symbol,
                    name="测试",
                    market="CN",
                    provider=source.provider,
                )
                if screenshot and screenshot.exists:
                    with open(screenshot.filepath, "rb") as f:
                        img_base64 = base64.b64encode(f.read()).decode("utf-8")
                    return CollectorResult(
                        success=True,
                        data={"image": f"data:image/png;base64,{img_base64}"},
                        count=1,
                    )
                return CollectorResult(success=False, error="截图失败")
            finally:
                await collector.close()

        elif source.type == "events":
            from src.collectors.events_collector import EastMoneyEventsCollector

            from datetime import timedelta

            # Use a longer window for tests to avoid "recently empty" false negatives.
            # This is only for connectivity/format validation, not for production logic.
            lookback_days = 365
            since = datetime.now() - timedelta(days=lookback_days)
            if source.provider == "eastmoney":
                cfg = source.config or {}
                collector = EastMoneyEventsCollector(
                    timeout_s=cfg.get("timeout_s", 10.0),
                    connect_timeout_s=cfg.get("connect_timeout_s"),
                    verify_ssl=cfg.get("verify_ssl", False),
                    proxy=cfg.get("proxy"),
                    retries=cfg.get("retries", 1),
                    backoff_s=cfg.get("backoff_s", 0.6),
                )
                items = await collector.fetch_events(
                    symbols=test_symbols[:5],
                    since=since,
                    page_size=100,
                )
                if not items and getattr(collector, "last_error", None):
                    return CollectorResult(
                        success=False,
                        data=[],
                        count=0,
                        error=str(collector.last_error),
                    )
                return CollectorResult(
                    success=len(items) > 0,
                    data=[
                        {
                            "title": i.title[:80],
                            "time": i.publish_time.strftime("%m-%d %H:%M"),
                            "event_type": i.event_type,
                        }
                        for i in items[:10]
                    ],
                    count=len(items),
                    error=""
                    if items
                    else f"未获取到事件数据（lookback={lookback_days}d）",
                )

        return CollectorResult(
            success=False, error=f"不支持的数据源类型: {source.type}"
        )

    async def _test_kline_source(
        self, source: DataSource, test_symbols: list[str]
    ) -> CollectorResult:
        """按 provider 测试 K 线源:tencent/tushare/yfinance 各走自己实现,不串备份链。

        测试需要的是"这个 provider 自己工作正常",不是"整条主备链有 fallback 能跑通",
        所以不走 Orchestrator(它会自动切到下一条)。
        """
        from src.core.providers.base import ProviderRequest
        from src.core.providers.kline.tencent import TencentKlineProvider
        from src.core.providers.kline.tushare import TushareKlineProvider
        from src.core.providers.kline.yfinance import YFinanceKlineProvider

        cfg = source.config or {}
        if source.provider == "tencent":
            provider = TencentKlineProvider(config=cfg)
            market = "CN"
        elif source.provider == "tushare":
            provider = TushareKlineProvider(config=cfg)
            market = "CN"
            if provider._init_error:
                return CollectorResult(
                    success=False, error=provider._init_error
                )
        elif source.provider == "yfinance":
            provider = YFinanceKlineProvider(config=cfg)
            market = "US"  # yfinance 默认用 US 测,A 股不支持
            if provider._init_error:
                return CollectorResult(
                    success=False, error=provider._init_error
                )
        else:
            return CollectorResult(
                success=False, error=f"未知的 kline provider: {source.provider}"
            )

        results = []
        first_error = ""
        for symbol in test_symbols[:3]:
            try:
                resp = await provider.fetch(
                    ProviderRequest(
                        symbols=(symbol,), market=market, extra=(("days", 30),)
                    )
                )
                if resp.success and resp.data:
                    last = resp.data[-1]
                    last_close = getattr(last, "close", None) or (
                        last.get("close") if isinstance(last, dict) else None
                    )
                    last_date = getattr(last, "date", None) or (
                        last.get("date") if isinstance(last, dict) else None
                    )
                    results.append(
                        {
                            "symbol": symbol,
                            "last_close": last_close,
                            "last_date": last_date,
                            "count": len(resp.data),
                        }
                    )
                elif not first_error:
                    first_error = resp.error or "无数据"
            except Exception as e:
                if not first_error:
                    first_error = str(e)

        return CollectorResult(
            success=len(results) > 0,
            data=results,
            count=len(results),
            error="" if results else (first_error or "获取 K 线数据失败"),
        )

    async def _test_quote_source(
        self, source: DataSource, test_symbols: list[str]
    ) -> CollectorResult:
        """按 provider 测试行情源。"""
        from src.core.providers.base import ProviderRequest
        from src.core.providers.quote.tencent import TencentQuoteProvider
        from src.core.providers.quote.yfinance import YFinanceQuoteProvider

        cfg = source.config or {}
        if source.provider == "tencent":
            provider = TencentQuoteProvider(config=cfg)
            market = "CN"
        elif source.provider == "yfinance":
            provider = YFinanceQuoteProvider(config=cfg)
            market = "US"
            if provider._init_error:
                return CollectorResult(
                    success=False, error=provider._init_error
                )
        else:
            return CollectorResult(
                success=False, error=f"未知的 quote provider: {source.provider}"
            )

        try:
            resp = await provider.fetch(
                ProviderRequest(symbols=tuple(test_symbols[:5]), market=market)
            )
        except Exception as e:
            return CollectorResult(success=False, error=str(e))

        if not resp.success:
            return CollectorResult(success=False, error=resp.error or "获取行情失败")

        return CollectorResult(
            success=len(resp.data) > 0,
            data=[
                {
                    "symbol": item.get("symbol"),
                    "name": item.get("name"),
                    "price": item.get("current_price"),
                    "change_pct": item.get("change_pct"),
                }
                for item in (resp.data or [])
            ],
            count=len(resp.data or []),
            error="" if resp.data else "获取行情失败",
        )


# 全局单例
_manager: DataCollectorManager | None = None


def get_collector_manager() -> DataCollectorManager:
    """获取全局数据源管理器"""
    global _manager
    if _manager is None:
        _manager = DataCollectorManager()
    return _manager
