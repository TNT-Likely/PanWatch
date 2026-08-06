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

    def get_limit_up_pool(self, date: str | None = None) -> list[dict]:
        """获取涨停池(东财 getTopicZTPool)。

        date: YYYYMMDD,默认今天。
        返回: [{code, name, price, pct, amount, ltsz, first_time, last_time, days(连板数), ...}]
        """
        date = date or datetime.now().strftime("%Y%m%d")
        params = {
            "ut": "7eea3edcaed734bea9cbfc24409ed989",
            "dpt": "wz.ztzt",
            "Pageindex": "0",
            "pagesize": "60",
            "sort": "fbt:asc",
            "date": date,
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
        if not data:
            return []
        pool = (data.get("data") or {}).get("pool") or []
        result = []
        for item in pool:
            result.append(
                {
                    "code": item.get("c", ""),
                    "name": item.get("n", ""),
                    "price": _safe_float(item.get("p")) / 100 if item.get("p") else 0,
                    "pct": _safe_float(item.get("zdp")),
                    "amount": _safe_float(item.get("amount")),
                    "ltsz": _safe_float(item.get("ltsz")),
                    "first_time": item.get("fbt", ""),
                    "last_time": item.get("lbt", ""),
                    "days": int(item.get("days", 1) or 1),  # 连板天数
                }
            )
        return result

    def get_sentiment_summary(self) -> dict:
        """市场情绪摘要:涨停家数/连板梯队/最高板。"""
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

        return {
            "limit_up_count": total,
            "max_streak": max_days,
            "ladder": dict(sorted(ladder.items(), reverse=True)),
            "top_stocks": [f"{p['name']}({p['code']}){p['days']}板" for p in top_stocks],
        }

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
