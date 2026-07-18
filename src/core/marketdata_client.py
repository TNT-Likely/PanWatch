"""PanWatch ↔ marketdata 接线:DB 配置端口 + 单例 + flag 门控的报价兼容层。

- DbConfigProvider:把 DataSource 表映射成 marketdata 的 SourceConfig(实现 ConfigProvider 端口)。
- get_market_data():进程级单例(无状态 vendor + 现查 DB 的配置端口)。
- md_quote_rows():新包 MarketData.quotes 转 dict,返回 list[dict](与旧 orchestrator 输出同形)。
"""

from __future__ import annotations

import logging

from marketdata import MarketData, Quote, SourceConfig, set_default_proxy

logger = logging.getLogger(__name__)


def _truthy(v: str | None) -> bool:
    return str(v or "").strip().lower() in ("1", "true", "yes", "on")


def apply_marketdata_proxy() -> None:
    """按设置决定行情抓取是否走代理。

    开关 `marketdata_use_proxy` 打开时,让 marketdata 全部 vendor 的抓取走 `http_proxy`
    (复用同一代理地址);关闭(默认)则直连(不影响"代理会拦国内接口"的部署)。
    在启动 + 代理相关设置变更后调用。
    """
    from src.config import Settings

    use_proxy = False
    proxy = ""
    try:
        from src.web.database import SessionLocal
        from src.web.models import AppSettings

        db = SessionLocal()
        try:
            rows = {
                s.key: s.value
                for s in db.query(AppSettings).filter(
                    AppSettings.key.in_(["marketdata_use_proxy", "http_proxy"])
                )
            }
        finally:
            db.close()
        use_proxy = _truthy(rows.get("marketdata_use_proxy"))
        proxy = (rows.get("http_proxy") or "").strip()
    except Exception as e:  # DB 不可用时不阻断
        logger.debug(f"[apply_marketdata_proxy] 读设置失败: {e}")

    if not proxy:
        proxy = (Settings().http_proxy or "").strip()

    effective = proxy if (use_proxy and proxy) else None
    set_default_proxy(effective)
    logger.info(f"[marketdata] 行情抓取代理: {'走 ' + effective if effective else '直连(未启用/未配置)'}")


class DbConfigProvider:
    """ConfigProvider 端口实现:从 DataSource 表按 priority 读某类型的启用源。"""

    def _query_rows(self, datatype: str) -> list:
        from src.web.database import SessionLocal
        from src.web.models import DataSource

        db = SessionLocal()
        try:
            return (
                db.query(DataSource)
                .filter(DataSource.type == datatype, DataSource.enabled == True)  # noqa: E712
                .order_by(DataSource.priority)
                .all()
            )
        finally:
            db.close()

    def sources_for(self, datatype: str, market: str | None) -> list[SourceConfig]:
        return [
            SourceConfig(
                vendor=r.provider,
                priority=r.priority,
                enabled=True,
                config=r.config or {},
                supports_batch=bool(r.supports_batch),
            )
            for r in self._query_rows(datatype)
        ]


_md: MarketData | None = None


def get_market_data() -> MarketData:
    """进程级单例。vendor 无状态、配置现查 DB,故无需失效钩子。"""
    global _md
    if _md is None:
        _md = MarketData(config=DbConfigProvider())
    return _md


def reset_market_data() -> None:
    """测试或热重载时重置单例。"""
    global _md
    _md = None


def _quote_to_row(q: Quote) -> dict:
    """marketdata.Quote → 旧 orchestrator 同形 dict。"""
    return {
        "symbol": q.symbol,
        "name": q.name,
        "market": q.market,
        "current_price": q.current_price,
        "change_pct": q.change_pct,
        "change_amount": q.change_amount,
        "prev_close": q.prev_close,
        "open_price": q.open_price,
        "high_price": q.high_price,
        "low_price": q.low_price,
        "volume": q.volume,
        "turnover": q.turnover,
        "turnover_rate": q.turnover_rate,
        "volume_ratio": q.volume_ratio,
        "pe_ratio": q.pe_ratio,
        "circulating_market_value": q.circulating_market_value,
        "total_market_value": q.total_market_value,
    }


def md_quote_rows(symbols: list[str], market: str) -> list[dict]:
    """批量报价,返回 list[dict](与旧 orchestrator 输出同形)。

    同步函数;async 调用方用 `await asyncio.to_thread(md_quote_rows, ...)`。
    """
    syms = list(symbols)
    if not syms:
        return []
    quotes = get_market_data().quotes(syms, market=market)
    return [_quote_to_row(q) for q in quotes]


def md_stock_data(symbols: list[str], market: str) -> list:
    """返回 list[StockData](旧 AkshareCollector.get_stock_data 同形)。同步。"""
    from src.models.market import MarketCode, StockData

    syms = list(symbols)
    if not syms:
        return []
    quotes = get_market_data().quotes(syms, market=market)
    return [StockData(
        symbol=q.symbol, name=q.name or "", market=MarketCode(q.market),
        current_price=q.current_price or 0.0, change_pct=q.change_pct or 0.0,
        change_amount=q.change_amount or 0.0, volume=q.volume or 0.0,
        turnover=q.turnover or 0.0, open_price=q.open_price or 0.0,
        high_price=q.high_price or 0.0, low_price=q.low_price or 0.0,
        prev_close=q.prev_close or 0.0) for q in quotes]
