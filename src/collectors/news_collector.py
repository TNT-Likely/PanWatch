"""新闻采集器 - 雪球 + 东方财富"""
import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from functools import lru_cache
import asyncio

import httpx

from src.collectors.market_http import source_suffix
from src.core.cn_symbol import get_cn_prefix

logger = logging.getLogger(__name__)

# 简单内存缓存（5分钟过期）
_news_cache: dict[str, tuple[datetime, list]] = {}
_cache_ttl = timedelta(minutes=5)


def _get_cached(key: str) -> list | None:
    """获取缓存"""
    if key in _news_cache:
        cached_time, data = _news_cache[key]
        if datetime.now() - cached_time < _cache_ttl:
            return data
        del _news_cache[key]
    return None


def _set_cached(key: str, data: list) -> None:
    """设置缓存"""
    _news_cache[key] = (datetime.now(), data)


@dataclass
class NewsItem:
    """新闻数据结构"""
    source: str           # "xueqiu" / "eastmoney_news" / "eastmoney"
    external_id: str      # 来源侧唯一ID
    title: str
    content: str
    publish_time: datetime
    symbols: list[str] = field(default_factory=list)  # 关联股票代码
    importance: int = 0   # 0-3 重要性
    url: str = ""         # 原文链接


class BaseNewsCollector(ABC):
    """新闻采集器抽象基类"""

    source: str = ""

    @abstractmethod
    async def fetch_news(self, symbols: list[str] | None = None, since: datetime | None = None) -> list[NewsItem]:
        """
        获取新闻列表

        Args:
            symbols: 过滤的股票代码列表（可选）
            since: 只获取此时间之后的新闻（可选）

        Returns:
            NewsItem 列表
        """
        ...


class XueqiuNewsCollector(BaseNewsCollector):
    """
    雪球个股新闻采集器

    API: https://xueqiu.com/statuses/stock_timeline.json
    特点: 新闻聚合质量高，包含资讯+公告，需要登录 cookie
    """

    source = "xueqiu"
    API_URL = "https://xueqiu.com/statuses/stock_timeline.json"

    def __init__(self, cookies: str = ""):
        self.cookies = cookies

    def _get_symbol_id(self, symbol: str) -> str:
        """转换为雪球 symbol_id 格式"""
        if len(symbol) == 6 and symbol.isdigit():
            prefix = get_cn_prefix(symbol, upper=True)
            # 雪球 A 股新闻接口仅识别 SH/SZ，BJ 代码保留原值
            if prefix in {"SH", "SZ"}:
                return f"{prefix}{symbol}"
        return symbol

    async def fetch_news(self, symbols: list[str] | None = None, since: datetime | None = None) -> list[NewsItem]:
        """获取雪球个股新闻（并发请求）"""
        if not symbols:
            return []

        import asyncio

        # 只处理 A 股代码
        a_share_symbols = [s for s in symbols if len(s) == 6 and s.isdigit()]
        if not a_share_symbols:
            return []

        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Referer": "https://xueqiu.com/",
            "X-Requested-With": "XMLHttpRequest",
        }
        if self.cookies:
            headers["Cookie"] = self.cookies

        async with httpx.AsyncClient(timeout=8, headers=headers, trust_env=True) as client:  # 走系统代理(env HTTP_PROXY,由 apply_proxy_env 统一设)
            tasks = [self._fetch_for_symbol(client, symbol, since) for symbol in a_share_symbols]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        all_news = []
        for result in results:
            if isinstance(result, list):
                all_news.extend(result)

        logger.debug(f"雪球新闻采集到 {len(all_news)} 条")
        return all_news

    async def _fetch_for_symbol(self, client: httpx.AsyncClient, symbol: str, since: datetime | None) -> list[NewsItem]:
        """获取单只股票的新闻"""
        symbol_id = self._get_symbol_id(symbol)

        params = {
            "symbol_id": symbol_id,
            "count": 15,
            "source": "自选股新闻",
            "page": 1,
        }

        try:
            resp = await client.get(self.API_URL, params=params)
            if resp.status_code == 400:
                # 需要登录，跳过
                return []
            resp.raise_for_status()
            data = resp.json()

            items = data.get("list", [])
            result = []

            for item in items:
                try:
                    news = self._parse_item(item, symbol)
                    if news:
                        if since and news.publish_time < since:
                            continue
                        result.append(news)
                except Exception as e:
                    logger.debug(f"解析雪球新闻失败: {e}")

            return result

        except Exception as e:
            logger.debug(f"雪球新闻采集失败 ({symbol}): {e}")
            return []

    def _parse_item(self, item: dict, symbol: str) -> NewsItem | None:
        """解析单条新闻"""
        external_id = str(item.get("id", ""))
        if not external_id:
            return None

        title = item.get("title", "") or item.get("description", "")[:80]
        if not title:
            return None

        # 清理 HTML
        title = re.sub(r"<[^>]+>", "", title).strip()
        content = item.get("description", "") or ""
        content = re.sub(r"<[^>]+>", "", content).strip()

        # 解析时间（毫秒时间戳）
        created_at = item.get("created_at", 0)
        try:
            publish_time = datetime.fromtimestamp(created_at / 1000)
        except (ValueError, TypeError, OSError):
            publish_time = datetime.now()

        # 重要性判断
        importance = 0
        if any(k in title for k in ["重磅", "突发", "紧急", "重大", "独家"]):
            importance = 2
        elif any(k in title for k in ["快讯", "公告", "研报", "业绩"]):
            importance = 1

        # 原文链接
        url = item.get("target", "") or f"https://xueqiu.com/{item.get('user_id', '')}/{external_id}"

        return NewsItem(
            source=self.source,
            external_id=external_id,
            title=title,
            content=content[:300],
            publish_time=publish_time,
            symbols=[symbol],
            importance=importance,
            url=url,
        )


class EastMoneyStockNewsCollector(BaseNewsCollector):
    """
    东方财富个股新闻采集器

    API: https://search-api-web.eastmoney.com/search/jsonp (搜索 API)
    特点: 按股票名称搜索相关新闻（用名称搜索效果远好于代码）
    """

    source = "eastmoney_news"
    API_URL = "https://search-api-web.eastmoney.com/search/jsonp"

    def __init__(self, symbol_names: dict[str, str] | None = None):
        """
        初始化采集器

        Args:
            symbol_names: 股票代码到名称的映射，如 {"601127": "赛力斯", "600519": "贵州茅台"}
                          如果不提供，会自动从数据库获取
        """
        self._symbol_names = symbol_names

    def _get_symbol_names(self, symbols: list[str]) -> dict[str, str]:
        """获取股票代码到名称的映射（优先使用预设值，否则从数据库查询）"""
        if self._symbol_names:
            # 过滤出请求的 symbols 对应的名称
            return {sym: self._symbol_names[sym] for sym in symbols if sym in self._symbol_names}

        # 从数据库获取
        try:
            from src.web.database import SessionLocal
            from src.web.models import Stock

            db = SessionLocal()
            try:
                stocks = db.query(Stock).filter(Stock.symbol.in_(symbols)).all()
                return {s.symbol: s.name for s in stocks}
            finally:
                db.close()
        except Exception as e:
            logger.warning(f"获取股票名称失败: {e}")
            return {}

    async def fetch_news(self, symbols: list[str] | None = None, since: datetime | None = None) -> list[NewsItem]:
        """获取个股新闻（并发请求 + 缓存）- 支持 A股/港股/美股"""
        if not symbols:
            return []

        # 获取股票名称映射（支持所有市场，因为我们用名称搜索）
        symbol_names = self._get_symbol_names(symbols)

        # 对于没有名称的股票，使用代码作为 fallback
        for sym in symbols:
            if sym not in symbol_names:
                symbol_names[sym] = sym
                logger.debug(f"[EastMoneyStockNews] {sym} 无名称，使用代码搜索")

        if not symbol_names:
            return []

        # 检查缓存
        cache_key = f"eastmoney_news:{','.join(sorted(symbols))}"
        cached = _get_cached(cache_key)
        if cached is not None:
            logger.debug(f"东财资讯命中缓存")
            if since:
                return [n for n in cached if n.publish_time >= since]
            return cached

        # 限制并发数
        semaphore = asyncio.Semaphore(5)

        async def fetch_with_limit(client, symbol, stock_name):
            async with semaphore:
                # 缓存维度不包含 since，为避免“空结果污染缓存”，这里不做时间过滤
                return await self._fetch_for_symbol(client, symbol, stock_name, None)

        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Referer": "https://so.eastmoney.com/",
            "Accept": "*/*",
        }
        async with httpx.AsyncClient(timeout=8, verify=False, headers=headers, trust_env=True) as client:  # 走系统代理(env HTTP_PROXY,由 apply_proxy_env 统一设)
            tasks = [
                fetch_with_limit(client, symbol, symbol_names.get(symbol, symbol))
                for symbol in symbols
                if symbol in symbol_names  # 只查询有名称的股票
            ]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        all_news = []
        for result in results:
            if isinstance(result, list):
                all_news.extend(result)

        # 去重（相同新闻可能出现在多只股票搜索结果中）
        seen = set()
        unique_news = []
        for news in all_news:
            if news.external_id not in seen:
                seen.add(news.external_id)
                unique_news.append(news)

        # 缓存结果
        _set_cached(cache_key, unique_news)
        logger.debug(f"东方财富个股新闻采集到 {len(unique_news)} 条")
        if since:
            return [n for n in unique_news if n.publish_time >= since]
        return unique_news

    async def fetch_by_keyword(self, keyword: str) -> list[NewsItem]:
        """按任意关键词(行业/主题词,如"汽车行业""新能源汽车")搜中文新闻。

        复用东方财富搜索 API —— keyword 不限股票名,无需 cookie。
        给 TradingAgents 新闻分析师的行业/主题新闻查询用(个股查询走 fetch_news)。
        """
        headers = {
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
            "Referer": "https://so.eastmoney.com/",
            "Accept": "*/*",
        }
        async with httpx.AsyncClient(timeout=8, verify=False, headers=headers, trust_env=True) as client:  # 走系统代理(env HTTP_PROXY,由 apply_proxy_env 统一设)
            return await self._fetch_for_symbol(client, keyword, keyword, None)

    async def _fetch_for_symbol(self, client: httpx.AsyncClient, symbol: str, stock_name: str, since: datetime | None) -> list[NewsItem]:
        """获取单只股票的新闻（使用搜索 API，用股票名称搜索）"""
        import json as json_module

        # 构建搜索参数 - 使用股票名称搜索
        search_param = {
            "uid": "",
            "keyword": stock_name,  # 用股票名称搜索，效果更好
            "type": ["cmsArticleWebOld"],
            "client": "web",
            "clientType": "web",
            "clientVersion": "curr",
            "param": {
                "cmsArticleWebOld": {
                    "searchScope": "default",
                    "sort": "default",
                    "pageIndex": 1,
                    "pageSize": 15,
                    "preTag": "",
                    "postTag": ""
                }
            }
        }

        params = {
            "cb": "jQuery",
            "param": json_module.dumps(search_param, separators=(',', ':'))
        }

        try:
            resp = await client.get(self.API_URL, params=params)
            resp.raise_for_status()
            text = resp.text

            # 解析 JSONP: jQuery({...})
            if text.startswith("jQuery(") and text.endswith(")"):
                json_str = text[7:-1]
                data = json_module.loads(json_str)
            else:
                return []

            if data.get("code") != 0:
                return []

            items = data.get("result", {}).get("cmsArticleWebOld", [])
            result = []

            for item in items:
                try:
                    news = self._parse_item(item, symbol)
                    if news:
                        if since and news.publish_time < since:
                            continue
                        result.append(news)
                except Exception as e:
                    logger.debug(f"解析东方财富个股新闻失败: {e}")

            return result

        except Exception as e:
            logger.debug(f"东方财富个股新闻采集失败 ({stock_name}): {e}")
            return []

    def _parse_item(self, item: dict, symbol: str) -> NewsItem | None:
        """解析单条新闻"""
        external_id = str(item.get("code", ""))
        if not external_id:
            return None

        title = item.get("title", "")
        if not title:
            return None

        content = item.get("content", "") or ""
        url = item.get("url", "")

        # 清理 HTML（搜索结果可能包含 <em> 等高亮标签）
        title = re.sub(r"<[^>]+>", "", title).strip()
        content = re.sub(r"<[^>]+>", "", content).strip()

        # 解析时间: "2026-01-20 17:19:17"
        date_str = item.get("date", "")
        try:
            publish_time = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError):
            try:
                publish_time = datetime.strptime(date_str[:10], "%Y-%m-%d")
            except (ValueError, TypeError):
                publish_time = datetime.now()

        # 重要性判断
        importance = 0
        if any(k in title for k in ["重磅", "突发", "紧急", "重大", "独家"]):
            importance = 2
        elif any(k in title for k in ["快讯", "消息", "公告", "研报"]):
            importance = 1

        # 原文链接 - 直接使用 API 返回的 URL
        if not url:
            url = f"https://finance.eastmoney.com/a/{external_id}.html"

        return NewsItem(
            source=self.source,
            external_id=external_id,
            title=title,
            content=content,
            publish_time=publish_time,
            symbols=[symbol],
            importance=importance,
            url=url,
        )


class EastMoneyNewsCollector(BaseNewsCollector):
    """
    东方财富公告采集器

    API: https://np-anotice-stock.eastmoney.com/api/security/ann
    特点: 支持批量查询多只股票公告
    """

    source = "eastmoney"
    API_URL = "https://np-anotice-stock.eastmoney.com/api/security/ann"

    async def fetch_news(self, symbols: list[str] | None = None, since: datetime | None = None) -> list[NewsItem]:
        """获取东方财富公告（批量查询，单次请求）"""
        if not symbols:
            logger.debug("东方财富公告需要指定股票代码")
            return []

        # 只处理 A 股代码
        a_share_symbols = [s for s in symbols if len(s) == 6 and s.isdigit()]
        if not a_share_symbols:
            return []

        # 检查缓存
        cache_key = f"eastmoney_ann:{','.join(sorted(a_share_symbols))}"
        cached = _get_cached(cache_key)
        if cached is not None:
            logger.debug(f"东财公告命中缓存")
            if since:
                return [n for n in cached if n.publish_time >= since]
            return cached

        # 批量查询（逗号分隔的股票代码）
        params = {
            "sr": -1,
            "page_size": 50,
            "page_index": 1,
            "ann_type": "A",
            "stock_list": ",".join(a_share_symbols),
            "f_node": 0,
            "s_node": 0,
        }

        try:
            async with httpx.AsyncClient(timeout=5, verify=False, trust_env=True) as client:  # 走系统代理(env HTTP_PROXY,由 apply_proxy_env 统一设)
                resp = await client.get(self.API_URL, params=params)
                resp.raise_for_status()
                data = resp.json()

            if not data.get("success"):
                return []

            items = data.get("data", {}).get("list", [])
            result = []

            for item in items:
                try:
                    # 从公告中提取关联的股票代码
                    codes = item.get("codes", []) or []
                    stock_codes = [c.get("stock_code", "") for c in codes if c.get("stock_code")]
                    if not stock_codes:
                        stock_codes = a_share_symbols[:1]

                    news = self._parse_item(item, stock_codes[0])
                    if news:
                        # 设置所有关联的股票代码
                        news.symbols = stock_codes
                        result.append(news)
                except Exception as e:
                    logger.debug(f"解析东方财富公告失败: {e}")

            # 缓存结果（缓存维度不包含 since，避免“空结果污染缓存”）
            _set_cached(cache_key, result)
            logger.debug(f"东方财富公告采集到 {len(result)} 条")
            if since:
                return [n for n in result if n.publish_time >= since]
            return result

        except Exception as e:
            logger.warning(f"东方财富公告采集失败: {e}{source_suffix()}")
            return []

    def _parse_item(self, item: dict, symbol: str) -> NewsItem | None:
        """解析单条公告"""
        external_id = str(item.get("art_code", ""))
        if not external_id:
            return None

        title = item.get("title", "")
        if not title:
            return None

        # 解析时间
        notice_date = item.get("notice_date", "")
        try:
            publish_time = datetime.strptime(notice_date, "%Y-%m-%d %H:%M:%S")
        except (ValueError, TypeError):
            try:
                publish_time = datetime.strptime(notice_date[:10], "%Y-%m-%d")
            except (ValueError, TypeError):
                publish_time = datetime.now()

        # 重要性判断
        importance = 0
        columns = item.get("columns", []) or []
        column_names = [c.get("column_name", "") for c in columns]
        if any(k in title for k in ["重大", "业绩预告", "业绩快报", "年报", "半年报"]):
            importance = 3
        elif any(k in title for k in ["季报", "分红", "增持", "减持"]):
            importance = 2
        elif "临时" in str(column_names):
            importance = 1

        # 原文链接
        url = f"https://data.eastmoney.com/notices/detail/{symbol}/{external_id}.html"

        return NewsItem(
            source=self.source,
            external_id=external_id,
            title=title,
            content="",  # 公告通常只有标题，内容需另外获取
            publish_time=publish_time,
            symbols=[symbol],
            importance=importance,
            url=url,
        )


class NewsCollector:
    """聚合新闻采集器"""

    # 数据源 provider 到采集器的映射
    COLLECTOR_MAP = {
        "xueqiu": lambda config: XueqiuNewsCollector(cookies=config.get("cookies", "")),
        "eastmoney_news": lambda config: EastMoneyStockNewsCollector(
            symbol_names=config.get("symbol_names")  # 可选，不传则自动从数据库获取
        ),
        "eastmoney": lambda config: EastMoneyNewsCollector(),
    }

    def __init__(self, collectors: list[BaseNewsCollector] | None = None):
        self.collectors = collectors or [
            EastMoneyStockNewsCollector(),  # 个股新闻
            EastMoneyNewsCollector(),        # 个股公告
        ]

    @classmethod
    def from_database(cls) -> "NewsCollector":
        """从数据库配置构建新闻采集器"""
        from src.web.database import SessionLocal
        from src.web.models import DataSource

        collectors = []
        db = SessionLocal()
        try:
            data_sources = (
                db.query(DataSource)
                .filter(DataSource.type == "news", DataSource.enabled == True)
                .order_by(DataSource.priority)
                .all()
            )

            for ds in data_sources:
                factory = cls.COLLECTOR_MAP.get(ds.provider)
                if factory:
                    try:
                        collector = factory(ds.config or {})
                        collectors.append(collector)
                    except Exception:
                        pass
        finally:
            db.close()

        # 如果没有配置数据源，使用默认
        if not collectors:
            collectors = [EastMoneyStockNewsCollector(), EastMoneyNewsCollector()]

        return cls(collectors=collectors)

    async def fetch_all(
        self,
        symbols: list[str] | None = None,
        since_hours: int = 2,
        symbol_names: dict[str, str] | None = None,
    ) -> list[NewsItem]:
        """
        聚合所有数据源的新闻（并发采集）

        Args:
            symbols: 股票代码列表
            since_hours: 获取最近 N 小时的新闻（快讯类）
            symbol_names: 股票代码到名称的映射（可选，如果不传则由采集器自行获取）

        Returns:
            按时间倒序排列的新闻列表
        """
        import asyncio

        # 如果传入了 symbol_names，更新各采集器的配置
        if symbol_names:
            for collector in self.collectors:
                if isinstance(collector, EastMoneyStockNewsCollector):
                    collector._symbol_names = symbol_names

        # 公告使用更长的时间窗口（因为公告发布较少）
        news_since = datetime.now() - timedelta(hours=since_hours)
        announcement_since = datetime.now() - timedelta(hours=max(since_hours, 72))

        async def fetch_from_collector(collector: BaseNewsCollector) -> list[NewsItem]:
            try:
                since = announcement_since if collector.source == "eastmoney" else news_since
                return await collector.fetch_news(symbols, since)
            except Exception as e:
                logger.error(f"采集器 {collector.source} 失败: {e}{source_suffix()}")
                return []

        # 并发采集所有数据源
        results = await asyncio.gather(*[fetch_from_collector(c) for c in self.collectors])

        all_news: list[NewsItem] = []
        for news_list in results:
            all_news.extend(news_list)

        # 按时间倒序 + 重要性倒序排列
        all_news.sort(key=lambda x: (x.publish_time, x.importance), reverse=True)

        # 去重（按 source + external_id）
        seen = set()
        unique_news = []
        for news in all_news:
            key = (news.source, news.external_id)
            if key not in seen:
                seen.add(key)
                unique_news.append(news)

        return unique_news
