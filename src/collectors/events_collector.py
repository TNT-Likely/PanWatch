import logging
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

import httpx

from src.collectors.market_http import source_suffix

logger = logging.getLogger(__name__)


@dataclass
class EventItem:
    source: str
    external_id: str
    event_type: str
    title: str
    publish_time: datetime
    symbols: list[str]
    importance: int
    url: str


# 东财公告全文(纯文本)content API:art_code -> data.notice_content。
# 直连需 trust_env=False + verify=False(同 CN 采集器,绕 LAN 代理)。
ANN_CONTENT_API_URL = "https://np-cnotice-stock.eastmoney.com/api/content/ann"


def fetch_announcement_fulltext(
    art_code: str,
    *,
    timeout_s: float = 8.0,
    proxy: str | None = None,
) -> str:
    """按 art_code 取东方财富公告全文(纯文本)。

    成功返回 ``data.notice_content`` 去空白后的纯文本;任何失败(网络/解析/空)
    返回空串 —— 调用方据此 fail-soft 只保留标题。

    Args:
        art_code: 公告唯一编号(EventItem.external_id)
        timeout_s: 请求超时
        proxy: 显式代理(默认不走 env 代理)
    """
    if not art_code:
        return ""
    params = {
        "art_code": str(art_code),
        "client_source": "web",
        "page_index": 1,
    }
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/122.0.0.0 Safari/537.36"
        )
    }
    try:
        timeout = httpx.Timeout(timeout_s, connect=min(timeout_s, 5.0))
        with httpx.Client(
            timeout=timeout,
            verify=False,
            headers=headers,
            follow_redirects=True,
            trust_env=False,  # 绕 LAN 代理(Telegram/AI 用),仅显式 proxy
            proxy=proxy,
        ) as client:
            resp = client.get(ANN_CONTENT_API_URL, params=params)
            resp.raise_for_status()
            data = resp.json() or {}
        content = ((data.get("data") or {}).get("notice_content")) or ""
        return str(content).strip()
    except Exception as e:
        logger.debug(f"公告全文获取失败 art_code={art_code}: {type(e).__name__}: {e!r}")
        return ""


class EastMoneyEventsCollector:
    """A-share event collector based on EastMoney notices.

    Uses the same notices endpoint as news collector, but returns structured event types.
    """

    source = "eastmoney"
    API_URL = "https://np-anotice-stock.eastmoney.com/api/security/ann"

    def __init__(
        self,
        *,
        timeout_s: float = 10.0,
        verify_ssl: bool = False,
        retries: int = 1,
        backoff_s: float = 0.6,
    ):
        self.timeout_s = float(timeout_s)
        self.verify_ssl = bool(verify_ssl)
        self.retries = int(retries)
        self.backoff_s = float(backoff_s)
        self.last_error: str | None = None

    def __init__(
        self,
        *,
        timeout_s: float = 10.0,
        connect_timeout_s: float | None = None,
        verify_ssl: bool = False,
        proxy: str | None = None,
        retries: int = 1,
        backoff_s: float = 0.6,
    ):
        self.timeout_s = float(timeout_s)
        self.connect_timeout_s = (
            float(connect_timeout_s) if connect_timeout_s is not None else None
        )
        self.verify_ssl = bool(verify_ssl)
        self.proxy = proxy
        self.retries = int(retries)
        self.backoff_s = float(backoff_s)
        self.last_error: str | None = None

    async def fetch_events(
        self,
        symbols: list[str] | None = None,
        *,
        since: datetime | None = None,
        page_size: int = 50,
    ) -> list[EventItem]:
        if not symbols:
            return []

        a_share_symbols = [s for s in symbols if len(s) == 6 and s.isdigit()]
        if not a_share_symbols:
            return []

        params = {
            "sr": -1,
            "page_size": int(page_size),
            "page_index": 1,
            "ann_type": "A",
            "stock_list": ",".join(sorted(set(a_share_symbols))),
            "f_node": 0,
            "s_node": 0,
        }

        self.last_error = None

        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/122.0.0.0 Safari/537.36"
            )
        }

        last_exc: Exception | None = None
        attempts = max(self.retries, 0) + 1

        for attempt in range(attempts):
            try:
                timeout = httpx.Timeout(
                    self.timeout_s, connect=min(self.timeout_s, 6.0)
                )
                if self.connect_timeout_s is not None:
                    timeout = httpx.Timeout(
                        self.timeout_s,
                        connect=max(0.1, float(self.connect_timeout_s)),
                    )
                async with httpx.AsyncClient(
                    timeout=timeout,
                    verify=self.verify_ssl,
                    headers=headers,
                    follow_redirects=True,
                    trust_env=False,  # 不吃 env 代理(Telegram/AI 用),仅用显式配置的 self.proxy
                    proxy=self.proxy,
                ) as client:
                    resp = await client.get(self.API_URL, params=params)
                    resp.raise_for_status()
                    try:
                        data = resp.json()
                    except Exception as e:
                        snippet = (resp.text or "")[:200].replace("\n", " ")
                        raise RuntimeError(f"JSON解析失败: {e}; body={snippet}")

                if not data.get("success"):
                    return []

                items = data.get("data", {}).get("list", []) or []
                result: list[EventItem] = []

                for item in items:
                    try:
                        codes = item.get("codes", []) or []
                        stock_codes = [
                            c.get("stock_code", "")
                            for c in codes
                            if c.get("stock_code")
                        ]
                        if not stock_codes:
                            stock_codes = a_share_symbols[:1]

                        ev = self._parse_item(item, stock_codes)
                        if not ev:
                            continue
                        if since and ev.publish_time < since:
                            continue
                        result.append(ev)
                    except Exception as e:
                        logger.debug(
                            f"解析 EastMoney 事件失败: {type(e).__name__}: {e!r}"
                        )

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

            except Exception as e:
                last_exc = e
                if attempt < attempts - 1:
                    try:
                        import asyncio

                        await asyncio.sleep(self.backoff_s * (attempt + 1))
                    except Exception:
                        pass
                    continue

        if last_exc is not None:
            self.last_error = f"{type(last_exc).__name__}: {last_exc!r}"
            logger.warning(f"EastMoney 事件采集失败: {self.last_error}{source_suffix()}")
        return []

    def _parse_item(self, item: dict, stock_codes: list[str]) -> EventItem | None:
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

        event_type = self._guess_event_type(title, column_names)
        importance = self._guess_importance(title, column_names)

        symbol_for_url = stock_codes[0] if stock_codes else ""
        url = (
            f"https://data.eastmoney.com/notices/detail/{symbol_for_url}/{external_id}.html"
            if symbol_for_url
            else ""
        )

        return EventItem(
            source=self.source,
            external_id=external_id,
            event_type=event_type,
            title=title,
            publish_time=publish_time,
            symbols=stock_codes,
            importance=importance,
            url=url,
        )

    @staticmethod
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

    @staticmethod
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


class EventsCollector:
    """Aggregate events collectors."""

    COLLECTOR_MAP = {
        "eastmoney": lambda config: EastMoneyEventsCollector(
            timeout_s=(config or {}).get("timeout_s", 10.0),
            connect_timeout_s=(config or {}).get("connect_timeout_s"),
            verify_ssl=(config or {}).get("verify_ssl", False),
            proxy=(config or {}).get("proxy"),
            retries=(config or {}).get("retries", 1),
            backoff_s=(config or {}).get("backoff_s", 0.6),
        ),
    }

    def __init__(self, collectors: list[EastMoneyEventsCollector] | None = None):
        self.collectors = collectors or [EastMoneyEventsCollector()]

    @classmethod
    def from_database(cls) -> "EventsCollector":
        from src.web.database import SessionLocal
        from src.web.models import DataSource

        collectors = []
        db = SessionLocal()
        try:
            data_sources = (
                db.query(DataSource)
                .filter(DataSource.type == "events", DataSource.enabled == True)
                .order_by(DataSource.priority)
                .all()
            )
            for ds in data_sources:
                factory = cls.COLLECTOR_MAP.get(ds.provider)
                if not factory:
                    continue
                try:
                    collectors.append(factory(ds.config or {}))
                except Exception:
                    pass
        finally:
            db.close()

        if not collectors:
            collectors = [EastMoneyEventsCollector()]
        return cls(collectors=collectors)

    async def fetch_all(
        self,
        *,
        symbols: list[str] | None = None,
        since_days: int = 7,
    ) -> list[EventItem]:
        import asyncio

        since = datetime.now() - timedelta(days=max(int(since_days), 1))

        async def fetch_one(c) -> list[EventItem]:
            try:
                return await c.fetch_events(symbols=symbols, since=since)
            except Exception as e:
                logger.warning(f"Events collector failed: {e}")
                return []

        results = await asyncio.gather(*[fetch_one(c) for c in self.collectors])
        all_items: list[EventItem] = []
        for items in results:
            all_items.extend(items)

        all_items.sort(key=lambda x: (x.publish_time, x.importance), reverse=True)
        seen: set[tuple[str, str]] = set()
        uniq: list[EventItem] = []
        for it in all_items:
            key = (it.source, it.external_id)
            if key in seen:
                continue
            seen.add(key)
            uniq.append(it)
        return uniq
