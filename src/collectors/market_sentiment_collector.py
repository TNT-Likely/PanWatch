"""市场情绪采集器:涨停池 + 涨跌家数统计 + 连板梯队。

数据源:东财 push2ex getTopicZTPool(涨停池) + push2 ulist.np(指数)。
替代 PanWatch 缺失的 wudao short_term_emotion / limit_up_pool 能力,
纯东财 HTTP 直连,免 key,适配云服务器环境。
"""
from __future__ import annotations

import logging
import time
from datetime import datetime

from src.collectors.market_http import market_get

logger = logging.getLogger(__name__)

_ZTPOOL_URL = "https://push2ex.eastmoney.com/getTopicZTPool"
_INDEX_URL = "https://push2.eastmoney.com/api/qt/ulist.np/get"

_ZT_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://quote.eastmoney.com/",
}


def _safe_float(v, default=0.0):
    try:
        return float(v) if v is not None else default
    except (TypeError, ValueError):
        return default


class MarketSentimentCollector:
    """市场情绪采集器:涨停池 / 涨跌家数 / 连板梯队。"""

    def __init__(self):
        self._cache: dict | None = None
        self._cache_ts: float = 0.0
        self._cache_ttl = 300  # 5 分钟缓存

    def _limit_up_pool_wudao(self, date: str) -> list[dict]:
        """wudao 涨停事件池(limit_up_filter) → 统一字段。

        覆盖东财 push2ex 在云服务器被断的场景;wudao 字段更丰富:
        primaryTheme(开盘啦主类题材) / reason_type / turnover_rate / order_amount / continue_num。
        """
        from src.collectors.wudao_mcp_client import WudaoMCPClient

        client = WudaoMCPClient()
        resp = client.call_tool(
            "limit_up_filter",
            {"date": date, "limit": 100, "sortBy": "continue_num", "format": "json"},
        )
        if not isinstance(resp, dict):
            return []
        # 结构化返回: rows 或 items(primaryThemeStats 是题材聚合,不用)
        rows = resp.get("rows") or resp.get("items") or []
        if not rows:
            return []
        out = []
        for item in rows:
            if not isinstance(item, dict):
                continue
            code = str(item.get("code") or "").strip()
            name = str(item.get("name") or "").strip()
            if not code:
                continue
            def _f(v):
                try:
                    return float(v) if v is not None else 0.0
                except (TypeError, ValueError):
                    return 0.0
            out.append(
                {
                    "code": code,
                    "name": name,
                    "price": _f(item.get("closePrice") or item.get("lastPrice") or item.get("price")),
                    "pct": _f(item.get("changeRate") or item.get("pct")),
                    "amount": _f(item.get("tradingAmount") or item.get("amount")),
                    "ltsz": _f(item.get("actualCurrencyValue") or item.get("currencyValue")),
                    "first_time": str(item.get("firstLimitUpTimeText") or item.get("first_limit_up_time") or ""),
                    "last_time": str(item.get("lastLimitUpTimeText") or item.get("last_limit_up_time") or ""),
                    "days": int(_f(item.get("continueNum") or item.get("continue_num") or 1)),
                    "sector": str(item.get("industry") or ""),
                    "theme": str(item.get("primaryTheme") or item.get("concept") or ""),
                    "reason": str(item.get("reasonType") or item.get("reason_type") or ""),
                    "turnover_rate": _f(item.get("turnoverRate")),
                    "order_amount": _f(item.get("orderAmount")),
                }
            )
        return out

    def get_limit_up_pool(self, date: str | None = None) -> list[dict]:
        """获取涨停池(wudao 优先,东财 getTopicZTPool 兜底)。

        date: YYYYMMDD,默认今天。当天(盘前)无数据时自动回退最近交易日(最多5天)。
        返回: [{code, name, price, pct, amount, ltsz, first_time, last_time, days(连板数), sector/theme, ...}]
        wudao 源额外带: theme(开盘啦主类题材) / reason(涨停原因) / turnover_rate / order_amount(封单额)。
        """
        date = date or datetime.now().strftime("%Y%m%d")
        from datetime import timedelta

        def _probe(back: int) -> str:
            if not back:
                return date
            try:
                return (datetime.strptime(date, "%Y%m%d") - timedelta(days=back)).strftime("%Y%m%d")
            except Exception:
                return date

        # ① wudao 优先:找最近非空交易日(最多 5 天)。wudao 字段更全(题材/原因/封单/换手)
        for back in range(6):
            probe = _probe(back)
            try:
                pool = self._limit_up_pool_wudao(probe)
            except Exception as e:
                logger.warning("wudao 涨停池失败(%s): %s", probe, e)
                pool = []
            if pool:
                return pool

        # ② 东财兜底:同逻辑找最近非空交易日
        for back in range(6):
            probe = _probe(back)
            params = {
                "ut": "7eea3edcaed734bea9cbfc24409ed989",
                "dpt": "wz.ztzt",
                "Pageindex": "0",
                "pagesize": "60",
                "sort": "fbt:asc",
                "date": probe,
            }
            data = market_get(
                _ZTPOOL_URL,
                host_key="push2ex.eastmoney.com",
                params=params,
                headers=_ZT_HEADERS,
                timeout=10,
                retries=2,
                parse="json",
                log_label="涨停池",
            )
            pool = []
            if data:
                raw_pool = (data.get("data") or {}).get("pool") or []
                for item in raw_pool:
                    pool.append(
                        {
                            "code": item.get("c", ""),
                            "name": item.get("n", ""),
                            "price": _safe_float(item.get("p")) / 1000 if item.get("p") else 0,
                            "pct": _safe_float(item.get("zdp")),
                            "amount": _safe_float(item.get("amount")),
                            "ltsz": _safe_float(item.get("ltsz")),
                            "first_time": item.get("fbt", ""),
                            "last_time": item.get("lbt", ""),
                            "days": int(item.get("days", 1) or 1),
                            "sector": item.get("hybk", "") or "",
                        }
                    )
            if pool:
                return pool
        return []

    def get_sentiment_summary(self) -> dict:
        """市场情绪摘要:涨停家数/连板梯队/最高板/涨停板块分布。"""
        pool = self.get_limit_up_pool()
        if not pool:
            return {"error": "无涨停池数据"}

        total = len(pool)
        # 连板梯队
        ladder = {}
        for p in pool:
            d = p["days"]
            ladder[d] = ladder.get(d, 0) + 1
        max_days = max(ladder.keys()) if ladder else 0

        # 最高板股票
        top_stocks = [p for p in pool if p["days"] == max_days][:5]

        # 涨停板块分布(从涨停股所属行业反推主线题材)
        # wudao 源 theme(开盘啦主类题材)优先,行业兜底
        sector_dist = {}
        for p in pool:
            sector = p.get("theme") or p.get("sector") or "其他"
            sector_dist[sector] = sector_dist.get(sector, 0) + 1
        top_sectors = sorted(
            sector_dist.items(), key=lambda x: x[1], reverse=True
        )[:6]

        # 主线题材龙头候选(全市场推荐:连板≥2 优先,其次早盘首板)
        def _first_time_str(p) -> str:
            ft = p.get("first_time")
            if ft is None:
                return ""
            s = str(ft).strip()
            # 东财 fbt 是 HHMMSS 数字(如 92501),wudao 是 HH:MM:SS
            if s.isdigit() and len(s) == 6:
                return f"{s[0:2]}:{s[2:4]}:{s[4:6]}"
            if s.isdigit() and len(s) == 5:
                return f"{s[0:1]}:{s[1:3]}:{s[3:5]}"
            return s

        candidates = []
        for p in pool:
            if p["days"] >= 2:
                candidates.append(p)
        candidates.sort(key=lambda x: (-x["days"], _first_time_str(x)))
        # 补足早盘首板(首封 10:00 前)
        if len(candidates) < 10:
            for p in pool:
                if p["days"] < 2 and _first_time_str(p) and _first_time_str(p) <= "10:00":
                    candidates.append(p)
                if len(candidates) >= 12:
                    break
        candidate_list = [
            {
                "code": p["code"],
                "name": p["name"],
                "days": p["days"],
                "theme": p.get("theme") or p.get("sector") or "",
                "reason": p.get("reason") or "",
                "first_time": _first_time_str(p),
                "turnover_rate": p.get("turnover_rate"),
                "order_amount": p.get("order_amount"),
            }
            for p in candidates[:12]
        ]

        return {
            "limit_up_count": total,
            "max_streak": max_days,
            "ladder": dict(sorted(ladder.items(), reverse=True)),
            "top_stocks": [f"{p['name']}({p['code']}){p['days']}板" for p in top_stocks],
            "top_sectors": [
                {"name": k, "count": v} for k, v in top_sectors
            ],
            "candidates": candidate_list,
        }

    def get_sector_rotation(self, top_n: int = 10) -> dict:
        """板块轮动:行业板块涨幅榜 + 概念板块涨幅榜(含主力净额)。

        返回: {"industries": [...], "concepts": [...]}
        每项: {name, pct(涨幅%), main_net(主力净额)}
        """
        url = "https://push2.eastmoney.com/api/qt/clist/get"
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://quote.eastmoney.com/",
        }
        base_params = {
            "pn": "1", "pz": str(top_n), "po": "1", "np": "1",
            "ut": "bd1d9ddb04089700cf9c27f6f7426281",
            "fltt": "2", "invt": "2",
            "fid": "f3",
            "fields": "f3,f12,f14,f62",
        }
        result = {}

        for key, fs in (("industries", "m:90+t:2+f:!50"), ("concepts", "m:90+t:3+f:!50")):
            params = {**base_params, "fs": fs}
            data = market_get(
                url,
                host_key="push2.eastmoney.com",
                params=params,
                headers=headers,
                timeout=10,
                retries=2,
                parse="json",
                log_label=f"板块轮动-{key}",
            )
            if not data:
                result[key] = []
                continue
            diff = (data.get("data") or {}).get("diff") or []
            items = []
            for item in diff:
                items.append(
                    {
                        "name": item.get("f14", ""),
                        "pct": _safe_float(item.get("f3")),
                        "main_net": _safe_float(item.get("f62")),
                    }
                )
            result[key] = items

        return result

    def get_index_snapshot(self) -> list[dict]:
        """主要指数快照(上证/深成/创业板)。优先腾讯接口(更稳),失败退回东财。"""
        # 腾讯行情接口(和 PanWatch quote vendor 同源,稳定)
        try:
            import requests

            url = "https://qt.gtimg.cn/q=sh000001,sz399001,sz399006"
            r = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=8)
            if r.status_code == 200 and r.text.strip():
                result = []
                for line in r.text.strip().split(";"):
                    line = line.strip()
                    if not line or "=" not in line:
                        continue
                    parts = line.split("~")
                    if len(parts) < 6:
                        continue
                    result.append(
                        {
                            "name": parts[1],
                            "price": _safe_float(parts[3]),
                            "pct": _safe_float(parts[32]) if len(parts) > 32 else 0.0,
                            "change": _safe_float(parts[31]) if len(parts) > 31 else 0.0,
                        }
                    )
                if result:
                    return result
        except Exception as e:
            logger.debug("腾讯指数接口失败: %s", e)

        # 退回东财
        params = {
            "fltt": "2",
            "invt": "2",
            "fields": "f2,f3,f4,f12,f14",
            "secids": "1.000001,0.399001,0.399006",
        }
        data = market_get(
            _INDEX_URL,
            host_key="push2.eastmoney.com",
            params=params,
            headers=_ZT_HEADERS,
            timeout=10,
            retries=2,
            parse="json",
            log_label="指数快照",
        )
        if not data:
            return []
        diff = (data.get("data") or {}).get("diff") or []
        result = []
        for item in diff:
            result.append(
                {
                    "name": item.get("f14", ""),
                    "price": _safe_float(item.get("f2")),
                    "pct": _safe_float(item.get("f3")),
                    "change": _safe_float(item.get("f4")),
                }
            )
        return result
