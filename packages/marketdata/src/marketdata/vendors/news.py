"""新闻资讯 vendor:xueqiu(雪球个股新闻)/ eastmoney_news(东财个股新闻搜索)/
eastmoney(东财公告),均 markets={"CN"}。

移植自 PanWatch src/collectors/news_collector.py 的 XueqiuNewsCollector /
EastMoneyStockNewsCollector / EastMoneyNewsCollector 抓取核(端点/params/headers/
_parse_item 字段全部照搬)。原实现是 async(httpx.AsyncClient),此处改为同步 market_get。

三者均**不做 since 时间过滤**——过滤统一放 client.news() 里做(需要一个"当下"锚点,
包内不允许偷偷调无参 datetime.now()/time.time());vendor 只管抓 + 解析,失败/空一律
返回 [],不 raise(market_get 失败已自动 record_error)。

雪球端点已知被阿里云 WAF 拦截(返回 HTML 挑战页而非 JSON),与是否带 cookie 无关——
检测到 HTML/非预期结构时直接 record_error 并返回 [],不强行解析。

**待实抓校准**(沙箱代理拦截真实端点,无法验证响应结构/WAF 特征字符串是否与实际一致)。
"""
from __future__ import annotations

import json as json_module
import re
from datetime import datetime, timezone

from marketdata.http import market_get, record_error
from marketdata.symbol import Symbol
from marketdata.types import NewsArticle
from marketdata.vendors.base import NewsVendor as _NewsVendorBase

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _strip_html(text: str | None) -> str:
    return _HTML_TAG_RE.sub("", text or "").strip()


def _parse_epoch_millis(ms) -> datetime:
    """毫秒时间戳(雪球 created_at)→ UTC datetime;解析失败回退 EPOCH。"""
    try:
        return datetime.fromtimestamp(int(ms) / 1000, tz=timezone.utc)
    except (TypeError, ValueError, OSError):
        return _EPOCH


def _parse_datetime_str(s, fmt: str = "%Y-%m-%d %H:%M:%S") -> datetime:
    """"%Y-%m-%d %H:%M:%S" 格式时间字符串 → UTC datetime;解析失败依次回退到纯日期、
    再回退 EPOCH(照搬 news_collector.py 两级 try/except 的容错顺序)。"""
    text = str(s or "").strip()
    if not text:
        return _EPOCH
    try:
        return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        pass
    try:
        return datetime.strptime(text[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return _EPOCH


# ---------------------------------------------------------------------------
# xueqiu(雪球个股新闻)
# ---------------------------------------------------------------------------

_XUEQIU_URL = "https://xueqiu.com/statuses/stock_timeline.json"
_XUEQIU_HOST = "xueqiu.com"
_XUEQIU_WAF_MARKERS = ("<textarea", "_waf_", "aliyun_waf", "<html")
_XUEQIU_WAF_MSG = "雪球被阿里云 WAF 拦截,纯 HTTP 无法通过,与 cookie 无关"


def _xueqiu_symbol_id(code: str) -> str:
    """A股 6 位代码 → 雪球 symbol_id(SH/SZ + code);雪球接口不识别 BJ,原值透传
    (照搬 XueqiuNewsCollector._get_symbol_id 的 SH/SZ/BJ 判断规则)。"""
    if len(code) == 6 and code.isdigit():
        if code.startswith("920") or code.startswith(("83", "87", "88")):
            return code  # BJ:雪球不识别,保留原值
        prefix = "SH" if code.startswith(("5", "6")) or code.startswith("900") else "SZ"
        return f"{prefix}{code}"
    return code


def _xueqiu_importance(title: str) -> int:
    if any(k in title for k in ("重磅", "突发", "紧急", "重大", "独家")):
        return 2
    if any(k in title for k in ("快讯", "公告", "研报", "业绩")):
        return 1
    return 0


def _looks_like_waf(text: str) -> bool:
    lowered = (text or "").lower()
    return any(marker.lower() in lowered for marker in _XUEQIU_WAF_MARKERS)


class XueqiuNewsVendor(_NewsVendorBase):
    name = "xueqiu"
    supports_markets = {"CN"}

    def fetch(self, symbols: list[Symbol], config: dict) -> list[NewsArticle]:
        a_share = [s.code for s in symbols if len(s.code) == 6 and s.code.isdigit()]
        if not a_share:
            return []

        cookies = (config or {}).get("cookies") or ""
        headers = {
            "User-Agent": _UA,
            "Referer": "https://xueqiu.com/",
            "X-Requested-With": "XMLHttpRequest",
        }
        if cookies:
            headers["Cookie"] = cookies

        result: list[NewsArticle] = []
        for code in a_share:
            params = {
                "symbol_id": _xueqiu_symbol_id(code),
                "count": 15,
                "source": "自选股新闻",
                "page": 1,
            }
            text = market_get(
                _XUEQIU_URL,
                host_key=_XUEQIU_HOST,
                params=params,
                headers=headers,
                min_interval_s=0.2,
                timeout=8,
                retries=1,
                parse="text",
                symbol=code,
                log_label="雪球个股新闻",
            )
            if text is None:
                continue  # market_get 失败已 record_error

            if _looks_like_waf(text):
                record_error(_XUEQIU_WAF_MSG)
                continue

            try:
                data = json_module.loads(text)
            except (ValueError, TypeError):
                data = None
            if not isinstance(data, dict):
                record_error(_XUEQIU_WAF_MSG)
                continue

            items = data.get("list", []) or []
            for item in items:
                try:
                    article = _parse_xueqiu_item(item, code)
                    if article:
                        result.append(article)
                except Exception:
                    continue

        return result


def _parse_xueqiu_item(item: dict, code: str) -> NewsArticle | None:
    external_id = str(item.get("id", ""))
    if not external_id:
        return None

    title = item.get("title", "") or (item.get("description", "") or "")[:80]
    if not title:
        return None
    title = _strip_html(title)
    content = _strip_html(item.get("description", ""))

    publish_time = _parse_epoch_millis(item.get("created_at", 0))
    url = item.get("target", "") or f"https://xueqiu.com/{item.get('user_id', '')}/{external_id}"

    return NewsArticle(
        source="xueqiu",
        external_id=external_id,
        title=title,
        content=content[:300],
        publish_time=publish_time,
        symbols=[code],
        importance=_xueqiu_importance(title),
        url=url,
    )


# ---------------------------------------------------------------------------
# eastmoney_news(东财个股新闻搜索,search-api-web JSONP)
# ---------------------------------------------------------------------------

_EM_NEWS_URL = "https://search-api-web.eastmoney.com/search/jsonp"
_EM_NEWS_HOST = "search-api-web.eastmoney.com"
_EM_NEWS_HEADERS = {
    "User-Agent": _UA,
    "Referer": "https://so.eastmoney.com/",
    "Accept": "*/*",
}


def _eastmoney_news_importance(title: str) -> int:
    if any(k in title for k in ("重磅", "突发", "紧急", "重大", "独家")):
        return 2
    if any(k in title for k in ("快讯", "消息", "公告", "研报")):
        return 1
    return 0


def _build_search_params(keyword: str) -> dict:
    search_param = {
        "uid": "",
        "keyword": keyword,  # 用名称搜索效果远好于代码(照搬原逻辑)
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
                "postTag": "",
            }
        },
    }
    return {"cb": "jQuery", "param": json_module.dumps(search_param, separators=(",", ":"))}


def _parse_jsonp(text: str) -> dict | None:
    """剥 JSONP 外壳:"jQuery({...})" -> {...}。非预期结构返回 None(不 raise)。"""
    stripped = (text or "").strip()
    if not (stripped.startswith("jQuery(") and stripped.endswith(")")):
        return None
    try:
        data = json_module.loads(stripped[len("jQuery("):-1])
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


class EastmoneyStockNewsVendor(_NewsVendorBase):
    name = "eastmoney_news"
    supports_markets = {"CN"}

    def fetch(self, symbols: list[Symbol], config: dict) -> list[NewsArticle]:
        if not symbols:
            return []
        names = (config or {}).get("symbol_names") or {}

        seen: set[str] = set()
        result: list[NewsArticle] = []
        for sym in symbols:
            code = sym.code
            keyword = names.get(code) or code  # 缺名 fallback 用代码搜索(照老逻辑)
            for article in self._search(keyword, code):
                if article.external_id in seen:
                    continue  # 同一新闻可能出现在多只股票搜索结果里,去重(照老逻辑)
                seen.add(article.external_id)
                result.append(article)
        return result

    @classmethod
    def fetch_by_keyword(cls, keyword: str) -> list[NewsArticle]:
        """按任意关键词(行业/主题词,如"新能源汽车")搜中文新闻,不需 symbol_names 映射。
        供 client.news_by_keyword 复用(照搬原 EastMoneyStockNewsCollector.fetch_by_keyword)。"""
        return cls()._search(keyword, keyword)

    def _search(self, keyword: str, symbol_tag: str) -> list[NewsArticle]:
        if not keyword:
            return []

        text = market_get(
            _EM_NEWS_URL,
            host_key=_EM_NEWS_HOST,
            params=_build_search_params(keyword),
            headers=_EM_NEWS_HEADERS,
            min_interval_s=0.2,
            timeout=8,
            retries=1,
            parse="text",
            verify=False,  # 对齐原 EastMoneyStockNewsCollector(verify_ssl=False)
            symbol=symbol_tag,
            log_label="东财个股新闻",
        )
        if text is None:
            return []

        data = _parse_jsonp(text)
        if not data or data.get("code") != 0:
            return []

        items = ((data.get("result") or {}).get("cmsArticleWebOld")) or []
        out: list[NewsArticle] = []
        for item in items:
            try:
                article = _parse_eastmoney_news_item(item, symbol_tag)
                if article:
                    out.append(article)
            except Exception:
                continue
        return out


def _parse_eastmoney_news_item(item: dict, symbol: str) -> NewsArticle | None:
    external_id = str(item.get("code", ""))
    if not external_id:
        return None

    title = item.get("title", "")
    if not title:
        return None
    title = _strip_html(title)
    content = _strip_html(item.get("content", ""))

    url = item.get("url", "") or f"https://finance.eastmoney.com/a/{external_id}.html"
    publish_time = _parse_datetime_str(item.get("date", ""))

    return NewsArticle(
        source="eastmoney_news",
        external_id=external_id,
        title=title,
        content=content,
        publish_time=publish_time,
        symbols=[symbol] if symbol else [],
        importance=_eastmoney_news_importance(title),
        url=url,
    )


# ---------------------------------------------------------------------------
# eastmoney(东财公告,ann API)
# ---------------------------------------------------------------------------

_EM_ANN_URL = "https://np-anotice-stock.eastmoney.com/api/security/ann"
_EM_ANN_HOST = "np-anotice-stock.eastmoney.com"


def _ann_importance(title: str, column_names: list[str]) -> int:
    if any(k in title for k in ("重大", "业绩预告", "业绩快报", "年报", "半年报")):
        return 3
    if any(k in title for k in ("季报", "分红", "增持", "减持")):
        return 2
    if any("临时" in c for c in column_names):
        return 1
    return 0


class EastmoneyAnnNewsVendor(_NewsVendorBase):
    name = "eastmoney"
    supports_markets = {"CN"}

    def fetch(self, symbols: list[Symbol], config: dict) -> list[NewsArticle]:
        a_share = sorted({s.code for s in symbols if len(s.code) == 6 and s.code.isdigit()})
        if not a_share:
            return []

        params = {
            "sr": -1,
            "page_size": int((config or {}).get("page_size") or 50),
            "page_index": 1,
            "ann_type": "A",
            "stock_list": ",".join(a_share),
            "f_node": 0,
            "s_node": 0,
        }
        headers = {"User-Agent": _UA}

        data = market_get(
            _EM_ANN_URL,
            host_key=_EM_ANN_HOST,
            params=params,
            headers=headers,
            min_interval_s=0.2,
            timeout=10,
            retries=1,
            parse="json",
            verify=False,  # 对齐原 EastMoneyNewsCollector(verify_ssl=False,东财 ann 端点 SSL 关闭)
            log_label="东财公告",
        )
        if not data or not data.get("success"):
            return []

        items = ((data.get("data") or {}).get("list")) or []
        result: list[NewsArticle] = []
        for item in items:
            try:
                codes = item.get("codes", []) or []
                stock_codes = [c.get("stock_code", "") for c in codes if c.get("stock_code")]
                if not stock_codes:
                    stock_codes = a_share[:1]
                article = _parse_ann_item(item, stock_codes)
                if article:
                    result.append(article)
            except Exception:
                continue
        return result


def _parse_ann_item(item: dict, symbols: list[str]) -> NewsArticle | None:
    external_id = str(item.get("art_code", ""))
    if not external_id:
        return None

    title = item.get("title", "")
    if not title:
        return None

    publish_time = _parse_datetime_str(item.get("notice_date", ""))

    columns = item.get("columns", []) or []
    column_names = [str(c.get("column_name") or "") for c in columns]
    importance = _ann_importance(title, column_names)

    symbol_for_url = symbols[0] if symbols else ""
    url = f"https://data.eastmoney.com/notices/detail/{symbol_for_url}/{external_id}.html"

    return NewsArticle(
        source="eastmoney",
        external_id=external_id,
        title=title,
        content="",  # 公告通常只有标题,内容需另外获取(照搬原逻辑)
        publish_time=publish_time,
        symbols=symbols,
        importance=importance,
        url=url,
    )
