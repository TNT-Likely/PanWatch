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

XUEQIU_COOKIE_STATUS_LABELS = {
    "not_configured": "未配置",
    "unknown": "待检测",
    "ok": "正常",
    "expired": "已过期",
    "blocked": "WAF 拦截",
    "error": "检测失败",
}

XUEQIU_COOKIE_UPDATE_HINT = (
    "雪球新闻默认通过 Playwright 无头浏览器采集，一般无需配置 Cookie。"
    "若采集失败，请确认已安装 Chromium：playwright install chromium。"
    "可选：粘贴登录 Cookie 以获取需登录态的内容。"
)

XUEQIU_TIMELINE_FETCH_JS = """
async (args) => {
  const params = new URLSearchParams({
    symbol_id: args.symbolId,
    count: String(args.count),
    source: args.source,
    page: '1',
  });
  const resp = await fetch('/statuses/stock_timeline.json?' + params.toString(), {
    credentials: 'include',
  });
  const text = await resp.text();
  if (!text.trim().startsWith('{')) {
    return {
      ok: false,
      status: resp.status,
      waf: text.includes('aliyun_waf') || text.includes('_waf_'),
      preview: text.slice(0, 120),
    };
  }
  const data = JSON.parse(text);
  return { ok: true, status: resp.status, list: data.list || [] };
}
"""

XUEQIU_HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/136.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9",
    "Referer": "https://xueqiu.com/",
    "Origin": "https://xueqiu.com",
    "X-Requested-With": "XMLHttpRequest",
}


def _parse_netscape_cookie_line(line: str) -> tuple[str, str] | None:
    """解析 Netscape cookies.txt 单行，兼容制表符或空格分隔。"""
    if "\t" in line:
        parts = line.split("\t")
        if len(parts) >= 7:
            return parts[5], parts[6]
    match = re.match(
        r"^(\S+)\s+(?:TRUE|FALSE)\s+\S+\s+(?:TRUE|FALSE)\s+\d+\s+(\S+)\s+(.+)$",
        line,
    )
    if match:
        return match.group(2), match.group(3)
    return None


def is_netscape_cookie_format(raw: str) -> bool:
    """判断是否为 Netscape cookies.txt 导出格式。"""
    raw = (raw or "").strip()
    if not raw:
        return False
    if raw.startswith("# Netscape"):
        return True
    for line in raw.splitlines()[:8]:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if _parse_netscape_cookie_line(line) and "xueqiu.com" in line:
            return True
    return False


def normalize_xueqiu_cookies(raw: str) -> str:
    """将 Netscape cookies.txt 或 HTTP Cookie 头格式统一为请求头字符串。"""
    raw = (raw or "").strip()
    if not raw:
        return ""
    if not is_netscape_cookie_format(raw):
        return raw

    pairs: list[str] = []
    seen: set[str] = set()
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parsed = _parse_netscape_cookie_line(line)
        if not parsed:
            continue
        name, value = parsed
        if not name:
            continue
        if name in seen:
            pairs = [pair for pair in pairs if not pair.startswith(f"{name}=")]
            seen.discard(name)
        pairs.append(f"{name}={value}")
        seen.add(name)
    if not pairs:
        return raw
    return "; ".join(pairs)

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


def resolve_xueqiu_cookie_health(config: dict | None) -> dict | None:
    """根据 config 中的 Cookie 与缓存检测结果，生成前端展示用的健康状态。"""
    config = config or {}
    cookies = str(config.get("cookies") or "").strip()
    cached = config.get("cookie_health")
    if isinstance(cached, dict) and cached.get("status"):
        return {
            "status": cached.get("status", "unknown"),
            "label": cached.get("label")
            or XUEQIU_COOKIE_STATUS_LABELS.get(str(cached.get("status")), "待检测"),
            "message": cached.get("message") or "",
            "checked_at": cached.get("checked_at"),
            "sample_count": int(cached.get("sample_count") or 0),
            "update_hint": XUEQIU_COOKIE_UPDATE_HINT,
        }
    return {
        "status": "unknown",
        "label": XUEQIU_COOKIE_STATUS_LABELS["unknown"],
        "message": "默认通过 Playwright 采集，无需 Cookie，请点击「检测连通」或「测试」验证",
        "checked_at": None,
        "sample_count": 0,
        "update_hint": XUEQIU_COOKIE_UPDATE_HINT,
    }


def build_xueqiu_cookie_health_record(probe: dict) -> dict:
    """将探测结果转为可写入数据源 config 的结构。"""
    status = str(probe.get("status") or "error")
    return {
        "status": status,
        "label": probe.get("label") or XUEQIU_COOKIE_STATUS_LABELS.get(status, "检测失败"),
        "message": probe.get("message") or "",
        "checked_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "sample_count": int(probe.get("sample_count") or 0),
    }


async def probe_xueqiu_cookie(cookies: str, test_symbol: str = "600519") -> dict:
    """轻量探测雪球新闻采集是否可用（Playwright，Cookie 可选）。"""
    raw_cookies = (cookies or "").strip()
    cookies = normalize_xueqiu_cookies(raw_cookies)
    symbol_id = XueqiuNewsCollector._get_symbol_id(test_symbol)

    try:
        client = await _XueqiuBrowserClient.get()
        items, error = await client.fetch_timeline(
            symbol_id,
            count=3,
            cookies=cookies,
        )
    except Exception as e:
        logger.debug(f"雪球采集探测失败: {e}")
        return {
            "status": "error",
            "label": XUEQIU_COOKIE_STATUS_LABELS["error"],
            "message": f"采集探测失败: {e}",
            "sample_count": 0,
        }

    if error:
        status = "blocked" if "WAF" in error else "error"
        if "Playwright" in error or "Chromium" in error:
            status = "error"
        return {
            "status": status,
            "label": XUEQIU_COOKIE_STATUS_LABELS[status],
            "message": error,
            "sample_count": 0,
        }

    count = len(items)
    if count > 0:
        message = f"采集正常，探测到 {count} 条样本新闻"
    else:
        message = "采集通道正常，但当前测试股票暂无新闻"
    return {
        "status": "ok",
        "label": XUEQIU_COOKIE_STATUS_LABELS["ok"],
        "message": message,
        "sample_count": count,
    }


class _XueqiuBrowserClient:
    """雪球 Playwright 会话：预热 WAF 后在页面内 fetch timeline API。"""

    _instance: "_XueqiuBrowserClient | None" = None
    _class_lock = asyncio.Lock()

    def __init__(self) -> None:
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._warmed_up = False
        self._cookies = ""
        self._init_lock = asyncio.Lock()

    @classmethod
    async def get(cls) -> "_XueqiuBrowserClient":
        async with cls._class_lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance

    async def fetch_timeline(
        self,
        symbol_id: str,
        count: int = 15,
        cookies: str = "",
    ) -> tuple[list[dict], str | None]:
        """拉取单只股票 timeline，返回 (items, error_message)。"""
        try:
            await self._ensure_ready(cookies)
        except ImportError:
            return [], "未安装 Playwright，请运行: pip install playwright && playwright install chromium"
        except Exception as e:
            logger.debug(f"雪球 Playwright 初始化失败: {e}")
            return [], f"Playwright 启动失败: {e}"

        try:
            result = await self._page.evaluate(
                XUEQIU_TIMELINE_FETCH_JS,
                {
                    "symbolId": symbol_id,
                    "count": count,
                    "source": "自选股新闻",
                },
            )
        except Exception as e:
            logger.debug(f"雪球 timeline fetch 失败 ({symbol_id}): {e}")
            return [], f"雪球新闻请求失败: {e}"

        if not result.get("ok"):
            if result.get("waf"):
                return [], "被雪球 WAF 拦截"
            return [], f"接口返回异常 (HTTP {result.get('status')})"

        return result.get("list") or [], None

    async def _ensure_ready(self, cookies: str = "") -> None:
        cookies = normalize_xueqiu_cookies(cookies or "")
        async with self._init_lock:
            if self._page is not None and self._warmed_up and cookies == self._cookies:
                return
            if self._page is not None and cookies != self._cookies:
                await self._close_unlocked()

            from playwright.async_api import async_playwright

            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                    "--no-sandbox",
                ],
            )
            self._context = await self._browser.new_context(
                locale="zh-CN",
                user_agent=XUEQIU_HTTP_HEADERS["User-Agent"],
            )
            if cookies:
                await self._inject_cookies(cookies)
            self._page = await self._context.new_page()
            await self._page.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', { get: () => undefined });"
            )
            await self._page.goto(
                "https://xueqiu.com/",
                wait_until="domcontentloaded",
                timeout=30000,
            )
            await self._page.wait_for_timeout(1500)
            self._warmed_up = True
            self._cookies = cookies

    async def _inject_cookies(self, cookies: str) -> None:
        cookie_list = []
        for part in cookies.split(";"):
            part = part.strip()
            if "=" not in part:
                continue
            name, value = part.split("=", 1)
            cookie_list.append(
                {"name": name, "domain": ".xueqiu.com", "path": "/", "value": value}
            )
        if cookie_list:
            await self._context.add_cookies(cookie_list)

    async def _close_unlocked(self) -> None:
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()
        self._browser = None
        self._playwright = None
        self._context = None
        self._page = None
        self._warmed_up = False
        self._cookies = ""

    @classmethod
    async def close(cls) -> None:
        async with cls._class_lock:
            if cls._instance is not None:
                await cls._instance._close_unlocked()
                cls._instance = None


class XueqiuNewsCollector(BaseNewsCollector):
    """
    雪球个股新闻采集器

    API: https://xueqiu.com/statuses/stock_timeline.json
    特点: 新闻聚合质量高，包含资讯+公告；通过 Playwright 绕过 WAF，Cookie 可选
    """

    source = "xueqiu"
    API_URL = "https://xueqiu.com/statuses/stock_timeline.json"

    def __init__(self, cookies: str = ""):
        self.cookies = normalize_xueqiu_cookies(cookies)

    @staticmethod
    def _get_symbol_id(symbol: str) -> str:
        """转换为雪球 symbol_id 格式"""
        if len(symbol) == 6 and symbol.isdigit():
            prefix = get_cn_prefix(symbol, upper=True)
            # 雪球 A 股新闻接口仅识别 SH/SZ，BJ 代码保留原值
            if prefix in {"SH", "SZ"}:
                return f"{prefix}{symbol}"
        return symbol

    async def fetch_news(self, symbols: list[str] | None = None, since: datetime | None = None) -> list[NewsItem]:
        """获取雪球个股新闻（Playwright 单会话顺序请求）"""
        if not symbols:
            return []

        a_share_symbols = [s for s in symbols if len(s) == 6 and s.isdigit()]
        if not a_share_symbols:
            return []

        client = await _XueqiuBrowserClient.get()
        all_news: list[NewsItem] = []
        for symbol in a_share_symbols:
            symbol_id = self._get_symbol_id(symbol)
            items, error = await client.fetch_timeline(
                symbol_id,
                count=15,
                cookies=self.cookies,
            )
            if error:
                logger.warning(f"雪球新闻采集失败 ({symbol}): {error}")
                continue
            for item in items:
                try:
                    news = self._parse_item(item, symbol)
                    if news:
                        if since and news.publish_time < since:
                            continue
                        all_news.append(news)
                except Exception as e:
                    logger.debug(f"解析雪球新闻失败: {e}")

        logger.debug(f"雪球新闻采集到 {len(all_news)} 条")
        return all_news

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
