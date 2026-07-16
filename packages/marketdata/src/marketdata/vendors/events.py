"""事件 vendor:东财公告(单源)。移植自 PanWatch EastMoneyEventsCollector.fetch_events 抓取核
(src/collectors/events_collector.py:fetch_events/_parse_item/_guess_event_type/_guess_importance)。

原实现是 async(httpx.AsyncClient);此处改为同步 market_get,URL/params/headers/
A股过滤/解析/类型与重要度启发式/排序/去重全部照搬。无 vendor 内缓存(由 Engine 统一管)。
"""
from __future__ import annotations

from datetime import datetime, timedelta

from marketdata.http import market_get
from marketdata.symbol import Symbol
from marketdata.types import EventItem
from marketdata.vendors.base import EventsVendor as _EventsVendorBase

_ANN_API_URL = "https://np-anotice-stock.eastmoney.com/api/security/ann"
_ANN_HOST = "np-anotice-stock.eastmoney.com"
_SOURCE = "eastmoney"


def _guess_event_type(title: str, column_names: list[str]) -> str:
    t = title
    if any(
        k in t
        for k in [
            "业绩预告",
            "业绩快报",
            "年报",
            "半年报",
            "季报",
            "三季报",
            "一季报",
        ]
    ):
        return "earnings"
    if any(k in t for k in ["分红", "派息", "除权", "除息", "送转", "股权登记"]):
        return "dividend"
    if any(k in t for k in ["停牌", "复牌"]):
        return "suspension"
    if any(k in t for k in ["回购", "股份回购"]):
        return "repurchase"
    if any(k in t for k in ["增发", "配股", "定向增发", "发行"]):
        return "financing"
    if any(k in t for k in ["减持", "增持", "股东", "董监高", "持股变动"]):
        return "insider"
    if any(k in t for k in ["诉讼", "仲裁", "立案", "处罚", "监管", "问询函"]):
        return "regulatory"
    if any(k in t for k in ["重组", "并购", "收购", "出售资产", "重大资产"]):
        return "restructuring"
    if any(k in column_names for k in ["临时公告", "重大事项"]):
        return "major"
    return "notice"


def _guess_importance(title: str, column_names: list[str]) -> int:
    t = title
    if any(
        k in t
        for k in [
            "重大",
            "业绩预告",
            "业绩快报",
            "年报",
            "半年报",
            "重组",
            "停牌",
            "复牌",
        ]
    ):
        return 3
    if any(
        k in t for k in ["季报", "分红", "回购", "增持", "减持", "问询函", "处罚"]
    ):
        return 2
    if any("临时" in k for k in column_names):
        return 1
    return 0


def _parse_item(item: dict, stock_codes: list[str]) -> EventItem | None:
    external_id = str(item.get("art_code", ""))
    title = (item.get("title") or "").strip()
    if not external_id or not title:
        return None

    notice_date = item.get("notice_date", "")
    publish_time = datetime.now()
    try:
        publish_time = datetime.strptime(notice_date, "%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        try:
            publish_time = datetime.strptime(str(notice_date)[:10], "%Y-%m-%d")
        except (ValueError, TypeError):
            publish_time = datetime.now()

    columns = item.get("columns", []) or []
    column_names = [str(c.get("column_name") or "") for c in columns]

    event_type = _guess_event_type(title, column_names)
    importance = _guess_importance(title, column_names)

    symbol_for_url = stock_codes[0] if stock_codes else ""
    url = (
        f"https://data.eastmoney.com/notices/detail/{symbol_for_url}/{external_id}.html"
        if symbol_for_url
        else ""
    )

    return EventItem(
        source=_SOURCE,
        external_id=external_id,
        event_type=event_type,
        title=title,
        publish_time=publish_time,
        symbols=stock_codes,
        importance=importance,
        url=url,
    )


class EventsVendor(_EventsVendorBase):
    name = "eastmoney"
    supports_markets = {"CN"}

    def fetch(self, symbols: list[Symbol], config: dict) -> list[EventItem]:
        a_share_symbols = [s.code for s in symbols if len(s.code) == 6 and s.code.isdigit()]
        if not a_share_symbols:
            return []

        since_days = int((config or {}).get("since_days") or 7)
        since = datetime.now() - timedelta(days=max(since_days, 1))
        page_size = int((config or {}).get("page_size") or 50)

        params = {
            "sr": -1,
            "page_size": page_size,
            "page_index": 1,
            "ann_type": "A",
            "stock_list": ",".join(sorted(set(a_share_symbols))),
            "f_node": 0,
            "s_node": 0,
        }
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            )
        }

        data = market_get(
            _ANN_API_URL,
            host_key=_ANN_HOST,
            params=params,
            headers=headers,
            min_interval_s=0.2,
            timeout=10,
            retries=1,
            parse="json",
            verify=False,  # 对齐原 EastMoneyEventsCollector 的 verify_ssl=False(东财 ann 端点 SSL 关闭)
            log_label="事件",
        )
        if not data or not data.get("success"):
            return []

        items = data.get("data", {}).get("list", []) or []
        result: list[EventItem] = []

        for item in items:
            try:
                codes = item.get("codes", []) or []
                stock_codes = [
                    c.get("stock_code", "") for c in codes if c.get("stock_code")
                ]
                if not stock_codes:
                    stock_codes = a_share_symbols[:1]

                ev = _parse_item(item, stock_codes)
                if not ev:
                    continue
                if ev.publish_time < since:
                    continue
                result.append(ev)
            except Exception:
                continue

        result.sort(key=lambda x: (x.publish_time, x.importance), reverse=True)

        seen: set[tuple[str, str]] = set()
        uniq: list[EventItem] = []
        for ev in result:
            key = (ev.source, ev.external_id)
            if key in seen:
                continue
            seen.add(key)
            uniq.append(ev)

        return uniq
