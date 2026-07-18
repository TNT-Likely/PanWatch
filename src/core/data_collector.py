"""统一数据源管理器"""

import asyncio
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

from marketdata import PACKAGE_VENDORS_BY_TYPE, capture_errors

from src.web.database import SessionLocal
from src.web.models import DataSource
from src.models.market import MarketCode

logger = logging.getLogger(__name__)

# 数据源"测试"最多测多少个配置的 test_symbols(上限,防用户贴一大串把源打爆)。
# 取 10 覆盖常见配置(此前 kline/capital_flow 写死 [:3]、quote/events [:5],会把用户
# 配的第 4/6 个悄悄切掉,造成"配了 N 个只返回前几个"的意外)。
_TEST_SYMBOL_LIMIT = 10


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
        from src.collectors.kline_collector import KlineCollector
        from src.collectors.capital_flow_collector import CapitalFlowCollector
        from src.collectors.events_collector import EastMoneyEventsCollector

        self.COLLECTOR_FACTORIES = {
            "kline": {
                "tencent": lambda cfg: ("tencent", KlineCollector),
            },
            "capital_flow": {
                "eastmoney": lambda cfg: CapitalFlowCollector(MarketCode.CN),
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
        from src.core.marketdata_client import md_stock_data

        start_time = datetime.now()
        self._log("实时行情", "quote", "start", f"获取 {len(symbols)} 只股票的行情")

        try:
            stocks = await asyncio.to_thread(md_stock_data, symbols, MarketCode.CN.value)

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
            # 收集 vendor/market_get 的真实失败原因,失败时透到 UI(而不是笼统的"无数据")
            with capture_errors() as errs:
                result = await self._test_source_impl(source, test_symbols)
            if not result.success and errs:
                # 去重保序 + 截断,拼成真因;若原本已有更具体的 error(如"provider 无对应 vendor")保留在前
                seen: dict[str, None] = {}
                for m in errs:
                    seen.setdefault(m, None)
                detail = "; ".join(list(seen)[:8])
                generic = {"", "无数据", "获取行情失败", "获取 K 线数据失败", "获取资金流向失败",
                           "未获取到新闻数据", "未获取到快讯数据", "未获取到基本面数据",
                           "未获取到龙虎榜数据", "未获取到融资融券数据", "未获取到股东数据",
                           "未获取到分红数据", "未获取到北向资金数据"}
                result.error = detail if (result.error or "") in generic else f"{result.error};真因: {detail}"
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
        if source.type == "news":
            return await self._test_news_source(source, test_symbols)

        elif source.type == "kline":
            # 按 provider 路由到对应 Provider,而不是写死走 tencent (KlineCollector)。
            # Tushare/YFinance 的 token 等配置从 source.config 注入。
            return await self._test_kline_source(source, test_symbols)

        elif source.type == "capital_flow":
            from src.collectors.capital_flow_collector import CapitalFlowCollector

            collector = CapitalFlowCollector(MarketCode.CN)
            results = []
            for symbol in test_symbols[:_TEST_SYMBOL_LIMIT]:
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
                    symbols=test_symbols[:_TEST_SYMBOL_LIMIT],
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

        elif source.type == "flash_news":
            return await self._test_flash_news_source(source)

        elif source.type == "fundamentals":
            return await self._test_fundamentals_source(source)

        elif source.type == "dragon_tiger":
            return await self._test_dragon_tiger_source(source)

        elif source.type == "margin":
            return await self._test_margin_source(source)

        elif source.type == "shareholders":
            return await self._test_shareholders_source(source)

        elif source.type == "dividend":
            return await self._test_dividend_source(source)

        elif source.type == "northbound":
            return await self._test_northbound_source(source)

        return CollectorResult(
            success=False, error=f"不支持的数据源类型: {source.type}"
        )

    # 包内 kline/quote/flash_news/fundamentals Engine 各自只注册了这些 vendor(权威来源见 marketdata.PACKAGE_VENDORS_BY_TYPE)。
    # provider 不在这个集合里 = 包内没实现该源,测试应给出明确 error,不能构造 Engine 硬跑。
    _NEWS_PACKAGE_VENDORS = PACKAGE_VENDORS_BY_TYPE["news"]
    _KLINE_PACKAGE_VENDORS = PACKAGE_VENDORS_BY_TYPE["kline"]
    _QUOTE_PACKAGE_VENDORS = PACKAGE_VENDORS_BY_TYPE["quote"]
    _FLASH_NEWS_PACKAGE_VENDORS = PACKAGE_VENDORS_BY_TYPE["flash_news"]
    _FUNDAMENTALS_PACKAGE_VENDORS = PACKAGE_VENDORS_BY_TYPE["fundamentals"]
    _DRAGON_TIGER_PACKAGE_VENDORS = PACKAGE_VENDORS_BY_TYPE["dragon_tiger"]
    _MARGIN_PACKAGE_VENDORS = PACKAGE_VENDORS_BY_TYPE["margin"]
    _SHAREHOLDERS_PACKAGE_VENDORS = PACKAGE_VENDORS_BY_TYPE["shareholders"]
    _DIVIDEND_PACKAGE_VENDORS = PACKAGE_VENDORS_BY_TYPE["dividend"]
    _NORTHBOUND_PACKAGE_VENDORS = PACKAGE_VENDORS_BY_TYPE["northbound"]

    async def _test_kline_source(
        self, source: DataSource, test_symbols: list[str]
    ) -> CollectorResult:
        """按 provider 测试 K 线源:走 marketdata 包的单源 Engine(仅该 vendor,不串备份链)。

        测试需要的是"这个 provider 自己工作正常",不是"整条主备链有 fallback 能跑通",
        所以用只含这一个 vendor 的 StaticConfigProvider 隔离测试指定源。
        """
        from marketdata import MarketData, SourceConfig, StaticConfigProvider, Symbol

        if source.provider not in self._KLINE_PACKAGE_VENDORS:
            return CollectorResult(
                success=False,
                error=f"provider {source.provider} 无对应 vendor，包内未实现该 K 线源",
            )

        cfg = source.config or {}
        md = MarketData(
            config=StaticConfigProvider(
                {"kline": [SourceConfig(vendor=source.provider, config=cfg, enabled=True)]}
            )
        )

        results = []
        first_error = ""
        for symbol in test_symbols[:_TEST_SYMBOL_LIMIT]:
            market = Symbol.parse(symbol).market.value
            try:
                bars = md.klines(symbol, market=market, days=30)
                if bars:
                    last = bars[-1]
                    results.append(
                        {
                            "symbol": symbol,
                            "last_close": last.close,
                            "last_date": last.date,
                            "count": len(bars),
                        }
                    )
                elif not first_error:
                    first_error = "无数据"
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
        """按 provider 测试行情源:走 marketdata 包的单源 Engine(仅该 vendor,不串备份链)。"""
        from marketdata import MarketData, SourceConfig, StaticConfigProvider

        from src.core.marketdata_client import _quote_to_row

        if source.provider not in self._QUOTE_PACKAGE_VENDORS:
            return CollectorResult(
                success=False,
                error=f"provider {source.provider} 无对应 vendor，包内未实现该行情源",
            )

        cfg = source.config or {}
        md = MarketData(
            config=StaticConfigProvider(
                {"quote": [SourceConfig(vendor=source.provider, config=cfg, enabled=True)]}
            )
        )

        try:
            quotes = md.quotes(list(test_symbols[:_TEST_SYMBOL_LIMIT]))
        except Exception as e:
            return CollectorResult(success=False, error=str(e))

        rows = [_quote_to_row(q) for q in quotes]
        return CollectorResult(
            success=len(rows) > 0,
            data=[
                {
                    "symbol": row["symbol"],
                    "name": row["name"],
                    "price": row["current_price"],
                    "change_pct": row["change_pct"],
                }
                for row in rows
            ],
            count=len(rows),
            error="" if rows else "获取行情失败",
        )

    async def _test_news_source(
        self, source: DataSource, test_symbols: list[str]
    ) -> CollectorResult:
        """按 provider 测试新闻源:走 marketdata 包的单源 Engine(仅该 vendor,不聚合其它源)。

        新闻是按 symbol 的数据;eastmoney_news 用股票名称搜索(效果远好于代码搜索),
        所以这里取测试股票的名称映射一并传入。capture_errors 已在 test_source 外层
        包着,失败时会自动透真因（含雪球 WAF 拦截）。
        """
        from marketdata import MarketData, SourceConfig, StaticConfigProvider

        if source.provider not in self._NEWS_PACKAGE_VENDORS:
            return CollectorResult(
                success=False,
                error=f"provider {source.provider} 无对应 vendor，包内未实现该新闻源",
            )

        cfg = source.config or {}
        md = MarketData(
            config=StaticConfigProvider(
                {"news": [SourceConfig(vendor=source.provider, config=cfg, enabled=True)]}
            )
        )

        names = self._get_stock_names(test_symbols)

        try:
            # 包内 news publish_time 是 aware(UTC),now 也须 aware,否则 since 过滤崩
            from datetime import timezone
            news = md.news(test_symbols, names=names, now=datetime.now(timezone.utc))
        except Exception as e:
            return CollectorResult(success=False, error=str(e))

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
            error="" if news else "未获取到新闻数据",
        )

    async def _test_flash_news_source(self, source: DataSource) -> CollectorResult:
        """按 provider 测试快讯源:走 marketdata 包的单源 Engine(仅该 vendor,不串备份链)。

        快讯是市场级数据(7×24 电报),不按 symbols 过滤,所以不传 test_symbols。
        """
        from marketdata import MarketData, SourceConfig, StaticConfigProvider

        if source.provider not in self._FLASH_NEWS_PACKAGE_VENDORS:
            return CollectorResult(
                success=False,
                error=f"provider {source.provider} 无对应 vendor，包内未实现该快讯源",
            )

        cfg = source.config or {}
        md = MarketData(
            config=StaticConfigProvider(
                {"flash_news": [SourceConfig(vendor=source.provider, config=cfg, enabled=True)]}
            )
        )

        try:
            items = md.flash_news(limit=20)
        except Exception as e:
            return CollectorResult(success=False, error=str(e))

        return CollectorResult(
            success=len(items) > 0,
            data=[
                {
                    "title": i.title[:80],
                    "time": i.publish_time.strftime("%m-%d %H:%M"),
                    "symbols": i.symbols,
                }
                for i in items[:10]
            ],
            count=len(items),
            error="" if items else "未获取到快讯数据",
        )

    async def _test_fundamentals_source(self, source: DataSource) -> CollectorResult:
        """按 provider 测试基本面源:走 marketdata 包的单源 Engine(仅该 vendor,不串备份链)。

        基本面是按 symbol 的数据(与市场级 flash_news 不同),测试必须显式配置
        test_symbols,不套用全局默认股票,配置缺失时直接给出明确 error。
        """
        from marketdata import MarketData, SourceConfig, StaticConfigProvider

        if source.provider not in self._FUNDAMENTALS_PACKAGE_VENDORS:
            return CollectorResult(
                success=False,
                error=f"provider {source.provider} 无对应 vendor，包内未实现该基本面源",
            )

        syms = list(source.test_symbols or [])[:5]
        if not syms:
            return CollectorResult(success=False, error="请配置测试股票代码")

        cfg = source.config or {}
        md = MarketData(
            config=StaticConfigProvider(
                {
                    "fundamentals": [
                        SourceConfig(vendor=source.provider, config=cfg, enabled=True)
                    ]
                }
            )
        )

        try:
            items = md.fundamentals(syms)
        except Exception as e:
            return CollectorResult(success=False, error=str(e))

        return CollectorResult(
            success=len(items) > 0,
            data=[
                {
                    "symbol": i.symbol,
                    "name": i.name,
                    "pe_ttm": i.pe_ttm,
                    "pb": i.pb,
                    "roe": i.roe,
                }
                for i in items[:10]
            ],
            count=len(items),
            error="" if items else "未获取到基本面数据",
        )

    async def _test_dragon_tiger_source(self, source: DataSource) -> CollectorResult:
        """测试龙虎榜源:走 marketdata 包的单源 Engine(仅该 vendor,不串备份链)。

        龙虎榜是市场级数据(不按 symbols 过滤),但需要指定交易日。测试时优先取
        source.config.test_date,未配置则用当前日期占位(仅用于验证连通性，
        实抓以真实交易日为准）。
        """
        from marketdata import MarketData, SourceConfig, StaticConfigProvider

        if source.provider not in self._DRAGON_TIGER_PACKAGE_VENDORS:
            return CollectorResult(
                success=False,
                error=f"provider {source.provider} 无对应 vendor，包内未实现该龙虎榜源",
            )

        cfg = source.config or {}
        md = MarketData(
            config=StaticConfigProvider(
                {
                    "dragon_tiger": [
                        SourceConfig(vendor=source.provider, config=cfg, enabled=True)
                    ]
                }
            )
        )

        test_date = cfg.get("test_date") or datetime.now().strftime("%Y-%m-%d")

        try:
            items = md.dragon_tiger(date=test_date)
        except Exception as e:
            return CollectorResult(success=False, error=str(e))

        return CollectorResult(
            success=len(items) > 0,
            data=[
                {
                    "symbol": i.symbol,
                    "name": i.name,
                    "net_buy": i.net_buy,
                }
                for i in items[:10]
            ],
            count=len(items),
            error=""
            if items
            else "未获取到龙虎榜数据（需配置 test_date 或当日有榜）",
        )

    async def _test_margin_source(self, source: DataSource) -> CollectorResult:
        """测试融资融券源:走 marketdata 包的单源 Engine(仅该 vendor,不串备份链)。

        融资融券是按 symbol 的数据,测试必须显式配置 test_symbols。
        """
        from marketdata import MarketData, SourceConfig, StaticConfigProvider

        if source.provider not in self._MARGIN_PACKAGE_VENDORS:
            return CollectorResult(
                success=False,
                error=f"provider {source.provider} 无对应 vendor，包内未实现该融资融券源",
            )

        syms = list(source.test_symbols or [])[:5]
        if not syms:
            return CollectorResult(success=False, error="请配置测试股票代码")

        cfg = source.config or {}
        md = MarketData(
            config=StaticConfigProvider(
                {"margin": [SourceConfig(vendor=source.provider, config=cfg, enabled=True)]}
            )
        )

        try:
            items = md.margin(syms)
        except Exception as e:
            return CollectorResult(success=False, error=str(e))

        return CollectorResult(
            success=len(items) > 0,
            data=[
                {
                    "symbol": i.symbol,
                    "date": i.date,
                    "total_balance": i.total_balance,
                }
                for i in items[:10]
            ],
            count=len(items),
            error="" if items else "未获取到融资融券数据",
        )

    async def _test_shareholders_source(self, source: DataSource) -> CollectorResult:
        """测试股东户数源:走 marketdata 包的单源 Engine(仅该 vendor,不串备份链)。

        股东户数是按 symbol 的数据,测试必须显式配置 test_symbols。
        """
        from marketdata import MarketData, SourceConfig, StaticConfigProvider

        if source.provider not in self._SHAREHOLDERS_PACKAGE_VENDORS:
            return CollectorResult(
                success=False,
                error=f"provider {source.provider} 无对应 vendor，包内未实现该股东户数源",
            )

        syms = list(source.test_symbols or [])[:5]
        if not syms:
            return CollectorResult(success=False, error="请配置测试股票代码")

        cfg = source.config or {}
        md = MarketData(
            config=StaticConfigProvider(
                {
                    "shareholders": [
                        SourceConfig(vendor=source.provider, config=cfg, enabled=True)
                    ]
                }
            )
        )

        try:
            items = md.shareholders(syms)
        except Exception as e:
            return CollectorResult(success=False, error=str(e))

        return CollectorResult(
            success=len(items) > 0,
            data=[
                {
                    "symbol": i.symbol,
                    "report_date": i.report_date,
                    "holder_num": i.holder_num,
                }
                for i in items[:10]
            ],
            count=len(items),
            error="" if items else "未获取到股东户数数据",
        )

    async def _test_dividend_source(self, source: DataSource) -> CollectorResult:
        """测试分红源:走 marketdata 包的单源 Engine(仅该 vendor,不串备份链)。

        分红是按 symbol 的数据,测试必须显式配置 test_symbols。
        """
        from marketdata import MarketData, SourceConfig, StaticConfigProvider

        if source.provider not in self._DIVIDEND_PACKAGE_VENDORS:
            return CollectorResult(
                success=False,
                error=f"provider {source.provider} 无对应 vendor，包内未实现该分红源",
            )

        syms = list(source.test_symbols or [])[:5]
        if not syms:
            return CollectorResult(success=False, error="请配置测试股票代码")

        cfg = source.config or {}
        md = MarketData(
            config=StaticConfigProvider(
                {"dividend": [SourceConfig(vendor=source.provider, config=cfg, enabled=True)]}
            )
        )

        try:
            items = md.dividend(syms)
        except Exception as e:
            return CollectorResult(success=False, error=str(e))

        return CollectorResult(
            success=len(items) > 0,
            data=[
                {
                    "symbol": i.symbol,
                    "ex_date": i.ex_date,
                    "dividend_per_share": i.dividend_per_share,
                }
                for i in items[:10]
            ],
            count=len(items),
            error="" if items else "未获取到分红数据",
        )

    async def _test_northbound_source(self, source: DataSource) -> CollectorResult:
        """测试北向资金源:走 marketdata 包的单源 Engine(仅该 vendor,不串备份链)。

        北向资金是市场级数据(7×24 资金流),不按 symbols 过滤,所以不传 test_symbols。
        """
        from marketdata import MarketData, SourceConfig, StaticConfigProvider

        if source.provider not in self._NORTHBOUND_PACKAGE_VENDORS:
            return CollectorResult(
                success=False,
                error=f"provider {source.provider} 无对应 vendor，包内未实现该北向资金源",
            )

        cfg = source.config or {}
        md = MarketData(
            config=StaticConfigProvider(
                {
                    "northbound": [
                        SourceConfig(vendor=source.provider, config=cfg, enabled=True)
                    ]
                }
            )
        )

        try:
            items = md.northbound()
        except Exception as e:
            return CollectorResult(success=False, error=str(e))

        return CollectorResult(
            success=len(items) > 0,
            data=[
                {
                    "date": i.date,
                    "hgt_net": i.hgt_net,
                    "total_net": i.total_net,
                }
                for i in items[:10]
            ],
            count=len(items),
            error="" if items else "未获取到北向资金数据",
        )


# 全局单例
_manager: DataCollectorManager | None = None


def get_collector_manager() -> DataCollectorManager:
    """获取全局数据源管理器"""
    global _manager
    if _manager is None:
        _manager = DataCollectorManager()
    return _manager
