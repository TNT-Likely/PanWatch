"""新闻采集器 - 雪球 + 东方财富"""
import logging
import re
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from functools import lru_cache
import asyncio

import json

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


# --- 雪球 Playwright 浏览器管理器（简化版：无 TTL / 健康检查 / 后台刷新） ---
_xueqiu_pw = None
_xueqiu_browser = None
_xueqiu_page = None
_xueqiu_page_lock = asyncio.Lock()  # 防止并发创建导致浏览器实例泄露


async def _xueqiu_reset_page():
    """关闭当前页面并清空引用，下次请求自动重建。"""
    global _xueqiu_page, _xueqiu_browser, _xueqiu_pw
    for obj in ["_xueqiu_page", "_xueqiu_browser", "_xueqiu_pw"]:
        try:
            ref = globals().get(obj)
            if ref:
                close_method = getattr(ref, "close", None) or getattr(ref, "stop", None)
                if close_method:
                    await close_method()
        except Exception:
            pass
    _xueqiu_page = None
    _xueqiu_browser = None
    _xueqiu_pw = None


async def _get_xueqiu_page(manual_cookies: str = "") -> "XueqiuPageResult":
    """获取一个已解析 WAF 的 Playwright 页面。

    页面创建后缓存复用；若 fetch 失败由调用方触发 _xueqiu_reset_page。
    使用锁保护并发创建，避免多实例泄露。
    """
    global _xueqiu_pw, _xueqiu_browser, _xueqiu_page

    if _xueqiu_page is not None:
        return _xueqiu_page

    async with _xueqiu_page_lock:
        # 双重检查：持有锁后再次确认页面试图尚未被其他协程创建
        if _xueqiu_page is not None:
            return _xueqiu_page

        from playwright.async_api import async_playwright

        logger.info("Playwright 访问 xueqiu.com 解析 WAF...")
        t0 = datetime.now()

        try:
            _xueqiu_pw = await async_playwright().start()
            _xueqiu_browser = await _xueqiu_pw.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                    "--no-sandbox",
                ]
            )

            context = await _xueqiu_browser.new_context(
                viewport={"width": 1280, "height": 800},
                locale="zh-CN",
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                java_script_enabled=True,
            )
    
            if manual_cookies:
                for part in manual_cookies.split(";"):
                    part = part.strip()
                    if "=" in part:
                        k, v = part.split("=", 1)
                        try:
                            await context.add_cookies([{
                                "name": k.strip(), "value": v.strip(),
                                "domain": ".xueqiu.com", "path": "/"
                            }])
                        except Exception:
                            pass
    
            page = await context.new_page()
            await page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            """)
    
            await page.goto("https://xueqiu.com/", wait_until="domcontentloaded", timeout=20000)
            try:
                await page.wait_for_load_state("networkidle", timeout=5000)
            except Exception:
                pass
            await page.wait_for_timeout(1500)
    
            _xueqiu_page = page
            logger.info("Playwright 雪球 WAF 已解析，页面就绪 (耗时 %.1fs)", (datetime.now() - t0).total_seconds())
            return page

        except ImportError:
            logger.error("Playwright 未安装，无法访问雪球 API")
            await _xueqiu_reset_page()
            return None
        except Exception as e:
            logger.warning(f"Playwright 初始化雪球页面失败: {e}")
            await _xueqiu_reset_page()
            return None


async def _xueqiu_fetch_via_playwright(
    symbol_ids: list[str],
    count: int = 15,
    manual_cookies: str = "",
) -> list[dict]:
    """通过 Playwright 浏览器内 fetch 调用雪球 API

    Args:
        symbol_ids: 雪球格式的股票 ID 列表 (如 ["SH600519", "SZ000001"])
        count: 每只股票获取的新闻条数
        manual_cookies: 用户配置的 Cookie

    Returns:
        原始 JSON 数据列表
    """
    page = await _get_xueqiu_page(manual_cookies)
    if not page:
        return []

    all_items = []
    # Phase 4: 并发控制，Semaphore(2) 限制并发数
    sem = asyncio.Semaphore(2)
    total = len(symbol_ids)
    now = datetime.now()

    async def _fetch_one(sid: str, index: int) -> list[dict]:
        """对单只股票执行 fetch，带重试逻辑。"""
        async with sem:
            for attempt in range(2):  # Phase 4: 重试一次
                try:
                    result = await asyncio.wait_for(
                        page.evaluate(f"""async () => {{
                        try {{
                            const r = await fetch(
                                "https://xueqiu.com/statuses/stock_timeline.json?" +
                                new URLSearchParams({{
                                    symbol_id: "{sid}",
                                    count: "{count}",
                                    source: "\u81ea\u9009\u80a1\u65b0\u95fb",
                                    page: "1",
                                }}),
                                {{
                                    headers: {{
                                        "X-Requested-With": "XMLHttpRequest",
                                        "Accept": "application/json, text/plain, */*",
                                        "Referer": "https://xueqiu.com/",
                                    }}
                                }}
                            );
                            const text = await r.text();
                            const data = JSON.parse(text);
                            return JSON.stringify(data.list || []);
                        }} catch(e) {{
                            return JSON.stringify({{error: e.message}});
                        }}
                    }}"""),
                        timeout=15,  # Phase 4: 12s->15s
                    )

                    data = json.loads(result)
                    if isinstance(data, list):
                        return data
                    elif isinstance(data, dict) and "error" in data:
                        err_msg = data["error"]
                        if any(kw in err_msg.lower() for kw in ("401", "403", "unauthorized", "forbidden")):
                            logger.warning(
                                "雪球 Playwright fetch Cookie 可能过期 (%s): %s (attempt %d/2)",
                                sid, err_msg, attempt + 1,
                            )
                            if attempt == 1:
                                return []
                            await asyncio.sleep(1)
                            continue
                        logger.warning(
                            "雪球 Playwright fetch 失败 (%s): %s (attempt %d/2)",
                            sid, err_msg, attempt + 1,
                        )

                except asyncio.TimeoutError:
                    logger.warning(
                        "雪球 Playwright fetch 超时 (%s) (attempt %d/2, 进度 %d/%d)",
                        sid, attempt + 1, index + 1, total,
                    )
                    if attempt == 0:
                        await asyncio.sleep(1)
                        continue
                except Exception as e:
                    logger.warning(
                        "雪球 Playwright fetch 异常 (%s): %s (attempt %d/2)", sid, e, attempt + 1,
                    )
                    if attempt == 0:
                        await asyncio.sleep(1)
                        continue
            return []

    # Phase 4: 并发 fetch
    tasks = [_fetch_one(sid, i) for i, sid in enumerate(symbol_ids)]
    results = await asyncio.gather(*tasks)

    for items in results:
        all_items.extend(items)

    return all_items


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

    def __init__(self, cookies: str = "", fallback_to_eastmoney: bool = True, auto_refresh_waf: bool = True, symbol_names: dict[str, str] | None = None):
        self.cookies = cookies.strip()
        self.last_error: str = ""  # 最近一次错误详情
        self._fallback_to_eastmoney = fallback_to_eastmoney  # Phase 3: 降级开关
        self._auto_refresh_waf = auto_refresh_waf  # 超时时自动重新过 WAF
        self._symbol_names = symbol_names  # 股票名称映射，供降级使用

    def _get_symbol_id(self, symbol: str) -> str:
        """转换为雪球 symbol_id 格式"""
        if len(symbol) == 6 and symbol.isdigit():
            prefix = get_cn_prefix(symbol, upper=True)
            if prefix in {"SH", "SZ"}:
                return f"{prefix}{symbol}"
        return symbol

    async def fetch_news(self, symbols: list[str] | None = None, since: datetime | None = None) -> list[NewsItem]:
        """获取雪球个股新闻（通过 Playwright 浏览器内 fetch 绕过 WAF）"""
        if not symbols:
            return []

        a_share_symbols = [s for s in symbols if len(s) == 6 and s.isdigit()]
        if not a_share_symbols:
            return []

        if not self.cookies:
            self.last_error = "雪球 Cookie 为空，请在数据源配置中填写有效的雪球登录 Cookie"
            logger.warning("雪球新闻采集: Cookie 为空")
            if self._fallback_to_eastmoney:
                logger.info("雪球 Cookie 为空，自动降级到东方财富采集")
                return await self._fallback_fetch(a_share_symbols, since)
            return []

        symbol_ids = [self._get_symbol_id(s) for s in a_share_symbols]

        raw_items = await _xueqiu_fetch_via_playwright(
            symbol_ids=symbol_ids,
            count=15,
            manual_cookies=self.cookies,
        )

        if not raw_items and self.cookies and self._auto_refresh_waf:
            # 尝试重新过 WAF 后重试一次
            logger.info("雪球 fetch 返回空数据，尝试重新过 WAF 后重试")
            await _xueqiu_reset_page()
            raw_items = await _xueqiu_fetch_via_playwright(
                symbol_ids=symbol_ids,
                count=15,
                manual_cookies=self.cookies,
            )
            if raw_items:
                logger.info(f"雪球 WAF 刷新后重试成功")
                self.last_error = ""
            else:
                logger.warning("雪球 WAF 刷新后重试仍为空数据")

        if not raw_items:
            if self.cookies:
                self.last_error = (
                    "雪球 Cookie 可能已过期，请重新登录雪球网页后复制最新 Cookie。"
                    "如果确认 Cookie 有效，可能是雪球 WAF 升级或网络问题。"
                )
            else:
                self.last_error = "雪球 API 未返回数据（Playwright 可能尚未就绪或被 WAF 拦截）"
            logger.warning(
                "雪球新闻采集: Playwright 返回空数据 (Cookie=%s...)",
                self.cookies[:20] if self.cookies else "空",
            )
            if self._fallback_to_eastmoney:
                logger.info("雪球返回空数据，自动降级到东方财富采集")
                return await self._fallback_fetch(a_share_symbols, since)
            return []

        all_news = []
        for item in raw_items:
            try:
                symbol_id = item.get("symbol_id", "")
                code = symbol_id[2:] if len(symbol_id) > 2 else ""
                news = self._parse_item(item, code)
                if news:
                    if since and news.publish_time < since:
                        continue
                    all_news.append(news)
            except Exception as e:
                logger.debug(f"解析雪球新闻失败: {e}")

        logger.debug(f"雪球新闻采集到 {len(all_news)} 条")
        return all_news

    async def _fallback_fetch(self, symbols: list[str], since: datetime | None) -> list[NewsItem]:
        """Phase 3: 降级到东方财富个股新闻采集器。"""
        try:
            fallback = EastMoneyStockNewsCollector(symbol_names=self._symbol_names)
            news = await fallback.fetch_news(symbols, since)
            if news:
                logger.info(f"雪球降级到东方财富成功: 获取到 {len(news)} 条新闻")
                self.last_error = "雪球不可用，已自动降级到东方财富（降级数据）"
            return news
        except Exception as e:
            logger.warning(f"雪球降级到东方财富也失败: {e}")
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
        if url.startswith("/"):
            url = f"https://xueqiu.com{url}"

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
        async with httpx.AsyncClient(timeout=8, verify=False, headers=headers, trust_env=False) as client:  # CN 源直连,绕过 env 代理
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
        async with httpx.AsyncClient(timeout=8, verify=False, headers=headers, trust_env=False) as client:  # CN 源直连,绕过 env 代理
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
            async with httpx.AsyncClient(timeout=5, verify=False, trust_env=False) as client:  # CN 源直连,绕过 env 代理
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


# 来源优先级（数字越大越优先展示）
SOURCE_PRIORITY = {
    "eastmoney_news": 3,  # 东方财富资讯（降级/兜底来源）
    "eastmoney": 2,        # 东方财富公告
    "xueqiu": 1,           # 雪球（主来源）
}


class NewsCollector:
    """聚合新闻采集器"""

    # 数据源 provider 到采集器的映射
    COLLECTOR_MAP = {
        "xueqiu": lambda config: XueqiuNewsCollector(
            cookies=config.get("cookies", ""),
            fallback_to_eastmoney=config.get("fallback_to_eastmoney", True),
            auto_refresh_waf=config.get("auto_refresh_waf", True),
        ),
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
                elif isinstance(collector, XueqiuNewsCollector):
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

        # 按来源优先级 + 重要性 + 时间倒序排列（优先展示东方财富降级数据）
        all_news.sort(key=lambda x: (SOURCE_PRIORITY.get(x.source, 0), x.importance, x.publish_time), reverse=True)

        # 去重（按 source + external_id）
        seen = set()
        unique_news = []
        for news in all_news:
            key = (news.source, news.external_id)
            if key not in seen:
                seen.add(key)
                unique_news.append(news)

        return unique_news
