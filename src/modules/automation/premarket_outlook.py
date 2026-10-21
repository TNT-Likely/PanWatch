"""Session collection for the mandatory investment-research graph."""
import asyncio
import logging
import time
from datetime import datetime

from src.modules.automation.base import AgentContext
from src.modules.automation.session_research import SessionResearchAgent
from src.modules.research.context_builder import ContextBuilder
from src.modules.research.signals import SignalPackBuilder
from src.platform.observability.log_context import get_log_context

logger = logging.getLogger(__name__)


class PremarketOutlookAgent(SessionResearchAgent):
    name = "premarket_outlook"
    display_name = "盘前分析"
    description = "开盘前结合行情、技术状态和隔夜资讯，独立生成今日交易展望"

    async def collect(self, context: AgentContext) -> dict:
        """采集盘前数据"""
        trace_id = (
            get_log_context().get("trace_id")
            or datetime.now().strftime("%m%d%H%M%S%f")[-10:]
        )
        start_ts = time.monotonic()
        symbols = [s.symbol for s in context.watchlist]
        logger.info(
            "[%s] 盘前分析采集开始: watchlist=%s symbols=%s",
            trace_id,
            len(symbols),
            ",".join(symbols[:12]),
        )

        # 2. 获取美股指数（隔夜表现）
        us_indices = []
        try:
            from src.platform.marketdata.marketdata_client import get_market_data

            items = await asyncio.to_thread(get_market_data().index_quotes, ["usDJI", "usIXIC", "usINX"])
            for item in items:
                us_indices.append(
                    {
                        "name": item.get("name") or item.get("symbol"),
                        "current": item.get("current_price"),
                        "change_pct": item.get("change_pct"),
                    }
                    )
        except Exception as e:
            logger.warning("[%s] 获取美股指数失败: %s", trace_id, e)
        logger.info("[%s] 隔夜指数采集完成: count=%s", trace_id, len(us_indices))

        # 3/4. SignalPack（技术面+持仓+新闻）
        builder = SignalPackBuilder()
        sym_list = [(s.symbol, s.market, s.name) for s in context.watchlist]
        packs = await builder.build_for_symbols(
            symbols=sym_list,
            include_news=True,
            news_hours=72,
            portfolio=context.portfolio,
            include_technical=True,
            include_capital_flow=True,
            include_events=True,
            events_days=7,
        )
        quote_ok = 0
        technical_ok = 0
        news_total = 0
        event_total = 0
        for sym in symbols:
            pack = packs.get(sym)
            if pack and pack.quote:
                quote_ok += 1
            tech = (pack.technical if pack else None) or {}
            if tech and not tech.get("error"):
                technical_ok += 1
            news_total += len((pack.news.items if (pack and pack.news) else []) or [])
            event_total += len((pack.events.items if (pack and pack.events) else []) or [])
        logger.info(
            "[%s] SignalPack完成: total=%s quote_ok=%s technical_ok=%s news_items=%s events=%s",
            trace_id,
            len(symbols),
            quote_ok,
            technical_ok,
            news_total,
            event_total,
        )

        context_builder = ContextBuilder()
        context_pack = await context_builder.build_symbol_contexts(
            agent_name=self.name,
            context=context,
            packs=packs,
            realtime_hours=12,
            extended_hours=72,
            history_days=30,
            kline_days=120,
            persist_snapshot=not getattr(context, "_research_collect", False),
        )
        symbol_contexts = context_pack.get("symbols", {}) or {}
        quality_overview = context_pack.get("quality_overview", {}) or {}
        low_quality = []
        for sym, item in symbol_contexts.items():
            score = ((item.get("data_quality") or {}).get("score") or 0)
            if int(score) < 70:
                low_quality.append(f"{sym}:{score}")
        logger.info(
            "[%s] 上下文构建完成: symbol_ctx=%s avg=%s min=%s max=%s low_quality=%s",
            trace_id,
            len(symbol_contexts),
            quality_overview.get("avg_score", 0),
            quality_overview.get("min_score", 0),
            quality_overview.get("max_score", 0),
            ",".join(low_quality[:8]) if low_quality else "-",
        )

        # Flatten news for headline section (优先实时，其次扩展，再次历史记忆)
        news_items = []
        try:
            seen = set()
            for sym in [s.symbol for s in context.watchlist]:
                ctx = symbol_contexts.get(sym) or {}
                layered = (ctx.get("news") or {})
                candidates = (
                    layered.get("realtime")
                    or layered.get("extended")
                    or layered.get("history")
                    or []
                )
                for it in candidates[:3]:
                    key = (it.get("source"), it.get("external_id"), it.get("title"))
                    if key in seen:
                        continue
                    seen.add(key)
                    news_items.append(
                        {
                            "source": it.get("source"),
                            "title": it.get("title"),
                            "content": it.get("content") or "",
                            "time": str(it.get("time") or "").split(" ")[-1],
                            "symbols": it.get("symbols") or [sym],
                            "importance": it.get("importance") or 0,
                            "url": it.get("url"),
                        }
                    )
                    if len(news_items) >= 10:
                        break
                if len(news_items) >= 10:
                    break
        except Exception as e:
            logger.warning("[%s] 头条新闻组装失败: %s", trace_id, e)
            news_items = []
        logger.info("[%s] 头条新闻组装完成: count=%s", trace_id, len(news_items))
        logger.info(
            "[%s] 盘前分析采集完成: elapsed_ms=%s",
            trace_id,
            int((time.monotonic() - start_ts) * 1000),
        )

        return {
            "us_indices": us_indices,
            "signal_packs": packs,
            "symbol_contexts": symbol_contexts,
            "quality_overview": quality_overview,
            "news": news_items,
            "timestamp": datetime.now().isoformat(),
            "run_trace_id": trace_id,
        }
