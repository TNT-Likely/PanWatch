from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

from src.collectors.akshare_collector import AkshareCollector
from src.collectors.kline_collector import KlineCollector
from src.collectors.news_collector import NewsCollector, NewsItem
from src.models.market import MarketCode
from src.models.market import StockData


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PositionSnapshot:
    has_position: bool
    accounts: list[dict] = field(default_factory=list)
    aggregated: dict | None = None


@dataclass(frozen=True)
class NewsSnapshot:
    hours: int
    items: list[dict] = field(default_factory=list)


@dataclass(frozen=True)
class SignalPack:
    symbol: str
    name: str
    market: MarketCode
    computed_at: str
    quote: StockData | None = None
    technical: dict | None = None  # kline_summary
    position: PositionSnapshot | None = None
    news: NewsSnapshot | None = None
    capital_flow: dict | None = None
    sources: dict[str, str] = field(default_factory=dict)
    missing: list[str] = field(default_factory=list)


class SignalPackBuilder:
    """Build structured inputs for agents.

    This is an in-memory per-run cache to reduce repeated network calls.
    """

    def __init__(self):
        self._quote_cache: dict[tuple[MarketCode, str], StockData | None] = {}
        self._quote_source_cache: dict[tuple[MarketCode, str], str] = {}
        self._tech_cache: dict[tuple[MarketCode, str], dict] = {}
        self._tech_source_cache: dict[tuple[MarketCode, str], str] = {}
        self._news_cache: dict[tuple[str, int], list[NewsItem]] = {}
        self._flow_cache: dict[tuple[MarketCode, str], dict] = {}
        self._flow_source_cache: dict[tuple[MarketCode, str], str] = {}

    @staticmethod
    def _source_policy(
        source_type: str, *, default_providers: list[str]
    ) -> tuple[list[tuple[str, dict]], bool]:
        """Return (providers, disabled).

        - If DB has no sources of this type: use defaults.
        - If DB has sources but all are disabled: disabled=True.
        - If some enabled: return them ordered by priority.
        """

        try:
            from src.web.database import SessionLocal
            from src.web.models import DataSource

            db = SessionLocal()
            try:
                total = (
                    db.query(DataSource.id)
                    .filter(DataSource.type == source_type)
                    .count()
                )
                if total == 0:
                    return [(p, {}) for p in default_providers], False
                enabled = (
                    db.query(DataSource)
                    .filter(DataSource.type == source_type, DataSource.enabled == True)
                    .order_by(DataSource.priority)
                    .all()
                )
                if not enabled:
                    return [], True
                return [
                    ((r.provider or "").strip(), (r.config or {}))
                    for r in enabled
                    if (r.provider or "").strip()
                ], False
            finally:
                db.close()
        except Exception:
            return [(p, {}) for p in default_providers], False

    @staticmethod
    def _now_iso() -> str:
        return datetime.now(timezone.utc).isoformat()

    async def build_for_symbols(
        self,
        *,
        symbols: list[tuple[str, MarketCode, str]],
        include_news: bool,
        news_hours: int,
        portfolio,
        include_technical: bool = True,
        include_capital_flow: bool = False,
    ) -> dict[str, SignalPack]:
        """Build packs for multiple symbols.

        Args:
            symbols: list of (symbol, market, name)
            include_news: whether to include news snapshot
            news_hours: window for news
            portfolio: AgentContext.portfolio
            include_capital_flow: whether to include CN capital flow snapshot
        """

        computed_at = self._now_iso()
        symbol_set = {s for s, _, _ in symbols}

        quote_providers, quote_disabled = self._source_policy(
            "quote", default_providers=["tencent"]
        )
        kline_providers, kline_disabled = self._source_policy(
            "kline", default_providers=["tencent"]
        )
        flow_providers, flow_disabled = self._source_policy(
            "capital_flow", default_providers=["eastmoney"]
        )

        # 1) Quotes (batch per market)
        by_market: dict[MarketCode, list[tuple[str, str]]] = {}
        for sym, market, name in symbols:
            by_market.setdefault(market, []).append((sym, name))

        quote_map: dict[str, StockData | None] = {}
        for market, items in by_market.items():
            missing = [s for s, _ in items if (market, s) not in self._quote_cache]
            if missing:
                if quote_disabled:
                    for sym in missing:
                        self._quote_cache[(market, sym)] = None
                        self._quote_source_cache[(market, sym)] = "disabled"
                else:
                    remaining = set(missing)
                    for provider, cfg in quote_providers:
                        if not remaining:
                            break
                        try:
                            if provider == "tencent":
                                collector = AkshareCollector(market)
                            else:
                                logger.info(
                                    f"SignalPack quote 未支持 provider={provider}，跳过"
                                )
                                continue

                            stocks = await collector.get_stock_data(sorted(remaining))
                            got = {s.symbol: s for s in stocks}
                            for sym in list(remaining):
                                sd = got.get(sym)
                                if not sd:
                                    continue
                                self._quote_cache[(market, sym)] = sd
                                self._quote_source_cache[(market, sym)] = provider
                                remaining.discard(sym)
                        except Exception as e:
                            logger.warning(
                                f"SignalPack quotes 采集失败({market.value},{provider}): {e}"
                            )
                            continue

                    for sym in remaining:
                        self._quote_cache[(market, sym)] = None
                        self._quote_source_cache.setdefault(
                            (market, sym), "unavailable"
                        )

            for sym, _ in items:
                quote_map[sym] = self._quote_cache.get((market, sym))
                if (
                    quote_map[sym] is not None
                    and (market, sym) not in self._quote_source_cache
                ):
                    self._quote_source_cache[(market, sym)] = "cache"

        # 2) Technical
        tech_map: dict[str, dict | None] = {}
        if include_technical:
            for sym, market, _ in symbols:
                key = (market, sym)
                if key not in self._tech_cache:
                    if kline_disabled:
                        self._tech_cache[key] = {"error": "K线数据源已禁用"}
                        self._tech_source_cache[key] = "disabled"
                    else:
                        last_err = None
                        for provider, cfg in kline_providers:
                            try:
                                if provider == "tencent":
                                    collector = KlineCollector(market)
                                else:
                                    logger.info(
                                        f"SignalPack kline 未支持 provider={provider}，跳过"
                                    )
                                    continue
                                self._tech_cache[key] = collector.get_kline_summary(sym)
                                self._tech_source_cache[key] = provider
                                last_err = None
                                break
                            except Exception as e:
                                last_err = e
                                continue
                        if key not in self._tech_cache:
                            self._tech_cache[key] = {
                                "error": str(last_err) if last_err else "获取K线失败"
                            }
                            self._tech_source_cache.setdefault(key, "unavailable")
                tech_map[sym] = self._tech_cache[key]
                if key in self._tech_cache and key not in self._tech_source_cache:
                    self._tech_source_cache[key] = "cache"

        # 3) News
        news_by_symbol: dict[str, list[dict]] = {}
        if include_news:
            key = (
                ",".join(sorted(symbol_set)),
                int(news_hours),
            )
            if key not in self._news_cache:
                try:
                    collector = NewsCollector.from_database()
                    all_news = await collector.fetch_all(
                        symbols=sorted(symbol_set),
                        since_hours=news_hours,
                    )
                    self._news_cache[key] = all_news
                except Exception as e:
                    logger.warning(f"SignalPack news 采集失败: {e}")
                    self._news_cache[key] = []

            for it in self._news_cache[key]:
                # attach to each symbol
                for sym in it.symbols or []:
                    if sym not in symbol_set:
                        continue
                    news_by_symbol.setdefault(sym, []).append(
                        {
                            "source": it.source,
                            "external_id": it.external_id,
                            "title": it.title,
                            "time": it.publish_time.strftime("%Y-%m-%d %H:%M"),
                            "importance": it.importance,
                            "url": it.url,
                        }
                    )

        # 4) Capital flow (CN only)
        flow_map: dict[str, dict] = {}
        if include_capital_flow:
            cn_symbols = [sym for sym, market, _ in symbols if market == MarketCode.CN]
            if cn_symbols:
                if flow_disabled:
                    for sym in cn_symbols:
                        key = (MarketCode.CN, sym)
                        self._flow_cache[key] = {"error": "资金流向数据源已禁用"}
                        self._flow_source_cache[key] = "disabled"
                        flow_map[sym] = self._flow_cache[key]
                else:
                    try:
                        from src.collectors.capital_flow_collector import (
                            CapitalFlowCollector,
                        )

                        collector = CapitalFlowCollector(MarketCode.CN)
                        for sym in cn_symbols:
                            key = (MarketCode.CN, sym)
                            if key in self._flow_cache:
                                flow_map[sym] = self._flow_cache[key]
                                if key not in self._flow_source_cache:
                                    self._flow_source_cache[key] = "cache"
                                continue

                            last_err = None
                            for provider, cfg in flow_providers:
                                try:
                                    if provider != "eastmoney":
                                        logger.info(
                                            f"SignalPack capital_flow 未支持 provider={provider}，跳过"
                                        )
                                        continue
                                    self._flow_cache[key] = (
                                        collector.get_capital_flow_summary(sym)
                                    )
                                    self._flow_source_cache[key] = provider
                                    last_err = None
                                    break
                                except Exception as e:
                                    last_err = e
                                    continue

                            if key not in self._flow_cache:
                                self._flow_cache[key] = {
                                    "error": str(last_err)
                                    if last_err
                                    else "获取资金流向失败"
                                }
                                self._flow_source_cache.setdefault(key, "unavailable")
                            flow_map[sym] = self._flow_cache[key]
                    except Exception as e:
                        logger.warning(f"SignalPack capital_flow 采集失败: {e}")

        # 5) Position
        packs: dict[str, SignalPack] = {}
        for sym, market, name in symbols:
            pos_list = []
            try:
                pos_list = portfolio.get_positions_for_stock(sym)
            except Exception:
                pos_list = []

            accounts = []
            for p in pos_list:
                try:
                    accounts.append(
                        {
                            "account_name": getattr(p, "account_name", ""),
                            "cost_price": getattr(p, "cost_price", None),
                            "quantity": getattr(p, "quantity", None),
                            "trading_style": getattr(p, "trading_style", ""),
                        }
                    )
                except Exception:
                    continue

            aggregated = None
            try:
                aggregated = portfolio.get_aggregated_position(sym)
            except Exception:
                aggregated = None

            missing: list[str] = []
            if quote_map.get(sym) is None:
                missing.append("quote")
            if include_technical:
                tech = tech_map.get(sym) or {}
                if not tech or tech.get("error"):
                    missing.append("kline")
            if include_news:
                if not news_by_symbol.get(sym):
                    missing.append("news")
            if include_capital_flow and market == MarketCode.CN:
                flow = flow_map.get(sym) or {}
                if not flow or flow.get("error"):
                    missing.append("capital_flow")

            packs[sym] = SignalPack(
                symbol=sym,
                name=name,
                market=market,
                computed_at=computed_at,
                quote=quote_map.get(sym),
                technical=tech_map.get(sym) if include_technical else None,
                position=PositionSnapshot(
                    has_position=bool(pos_list),
                    accounts=accounts,
                    aggregated=aggregated,
                ),
                news=NewsSnapshot(
                    hours=news_hours, items=news_by_symbol.get(sym, [])[:5]
                )
                if include_news
                else None,
                capital_flow=flow_map.get(sym)
                if (include_capital_flow and market == MarketCode.CN)
                else None,
                sources={
                    "quote": self._quote_source_cache.get((market, sym), "unknown"),
                    "kline": self._tech_source_cache.get((market, sym), "unknown")
                    if include_technical
                    else "skipped",
                    "news": "db" if include_news else "skipped",
                    "capital_flow": self._flow_source_cache.get(
                        (MarketCode.CN, sym), "unknown"
                    )
                    if (include_capital_flow and market == MarketCode.CN)
                    else "skipped",
                },
                missing=missing,
            )

        return packs
