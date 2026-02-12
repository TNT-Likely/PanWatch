from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from src.core.context_store import (
    get_recent_stock_context_snapshots,
    save_news_topic_snapshot,
    save_stock_context_snapshot,
)
from src.core.kline_context import build_kline_history_context
from src.core.news_ranker import (
    dedupe_news_items,
    parse_news_time,
    rank_news_items,
    summarize_news_topics,
)
from src.models.market import MarketCode
from src.web.database import SessionLocal
from src.web.models import AnalysisHistory
from src.core.json_safe import to_jsonable

logger = logging.getLogger(__name__)


def _iso_today() -> str:
    return date.today().strftime("%Y-%m-%d")


def _cut_by_hours(items: list[dict], hours: int) -> list[dict]:
    if not items:
        return []
    cutoff = datetime.now() - timedelta(hours=max(1, int(hours)))
    out: list[dict] = []
    for it in items:
        ts = parse_news_time(str(it.get("time") or ""))
        if ts and ts >= cutoff:
            out.append(it)
    return out


def _estimate_quality_score(coverage: dict) -> int:
    score = 100
    if not coverage.get("quote"):
        score -= 35
    if not coverage.get("technical"):
        score -= 25
    if not coverage.get("kline_history"):
        score -= 10
    if not coverage.get("news_realtime"):
        score -= 15
    if not coverage.get("news_extended"):
        score -= 10
    if not coverage.get("history_news"):
        score -= 10
    if not coverage.get("events"):
        score -= 5
    return max(0, min(100, score))


class ContextBuilder:
    """统一构建 Agent 上下文（新闻分层 + 历史K线 + 账户约束 + 质量评分）"""

    def __init__(self):
        self._kline_cache: dict[tuple[str, str, int], dict] = {}

    @staticmethod
    def _load_history_news(symbol: str, stock_name: str, days: int = 7) -> list[dict]:
        cutoff = (date.today() - timedelta(days=max(1, days))).strftime("%Y-%m-%d")
        db = SessionLocal()
        try:
            rows = (
                db.query(AnalysisHistory)
                .filter(
                    AnalysisHistory.agent_name.in_(
                        ("news_digest", "premarket_outlook", "daily_report")
                    ),
                    AnalysisHistory.analysis_date >= cutoff,
                )
                .order_by(AnalysisHistory.analysis_date.desc())
                .limit(30)
                .all()
            )
            out: list[dict] = []
            for row in rows:
                raw = row.raw_data or {}
                items = raw.get("news") or []
                if not isinstance(items, list):
                    items = []
                if not items:
                    # 新版本盘前/盘后将新闻放在 context_payload.<symbol>.news.*
                    ctx_payload = raw.get("context_payload") or {}
                    if isinstance(ctx_payload, dict):
                        sym_payload = ctx_payload.get(symbol) or {}
                        if isinstance(sym_payload, dict):
                            layered = sym_payload.get("news") or {}
                            if isinstance(layered, dict):
                                for bucket in ("realtime", "extended", "history"):
                                    rows_bucket = layered.get(bucket) or []
                                    if isinstance(rows_bucket, list):
                                        items.extend(rows_bucket)
                for it in items:
                    if not isinstance(it, dict):
                        continue
                    symbols = it.get("symbols") or []
                    title = str(it.get("title") or "")
                    content = str(it.get("content") or "")
                    matched = False
                    if symbol and symbol in symbols:
                        matched = True
                    if not matched and symbol and symbol in title:
                        matched = True
                    if not matched and stock_name and stock_name in f"{title} {content}":
                        matched = True
                    if not matched:
                        continue
                    out.append(
                        {
                            "source": it.get("source") or "news_digest",
                            "external_id": it.get("external_id") or "",
                            "title": title,
                            "content": content,
                            "time": it.get("publish_time") or it.get("time") or "",
                            "importance": it.get("importance") or 0,
                            "url": it.get("url") or "",
                            "symbols": symbols if isinstance(symbols, list) else [symbol],
                        }
                    )
            return dedupe_news_items(out)
        except Exception as e:
            logger.warning(f"读取历史新闻失败: {symbol} - {e}")
            return []
        finally:
            db.close()

    @staticmethod
    def _build_portfolio_constraints(portfolio, symbol: str) -> dict:
        agg = None
        try:
            agg = portfolio.get_aggregated_position(symbol)
        except Exception:
            agg = None

        accounts = getattr(portfolio, "accounts", []) or []
        total_funds = float(getattr(portfolio, "total_available_funds", 0) or 0)
        total_cost = float(getattr(portfolio, "total_cost", 0) or 0)

        single_position_ratio = 0.0
        if agg and total_cost > 0:
            single_position_ratio = float(agg.get("total_cost") or 0) / total_cost

        safe_position = {}
        if isinstance(agg, dict):
            pos_rows = []
            for p in (agg.get("positions") or []):
                row = to_jsonable(p)
                if isinstance(row, dict):
                    pos_rows.append(
                        {
                            "account_id": row.get("account_id"),
                            "account_name": row.get("account_name"),
                            "quantity": row.get("quantity"),
                            "cost_price": row.get("cost_price"),
                            "trading_style": row.get("trading_style"),
                        }
                    )
            safe_position = {
                "symbol": agg.get("symbol"),
                "name": agg.get("name"),
                "market": (
                    agg.get("market").value
                    if isinstance(agg.get("market"), MarketCode)
                    else str(agg.get("market") or "")
                ),
                "total_quantity": agg.get("total_quantity"),
                "avg_cost": agg.get("avg_cost"),
                "total_cost": agg.get("total_cost"),
                "trading_style": agg.get("trading_style"),
                "positions": pos_rows,
            }

        return {
            "has_position": bool(agg),
            "position": safe_position,
            "total_available_funds": total_funds,
            "total_cost": total_cost,
            "account_count": len(accounts),
            "single_position_ratio": round(single_position_ratio, 4),
            "risk_budget_hint": "strict"
            if single_position_ratio >= 0.35
            else "normal"
            if single_position_ratio >= 0.2
            else "relaxed",
        }

    def _get_kline_history(self, symbol: str, market: MarketCode, days: int) -> dict:
        key = (symbol, str(market), int(days))
        if key in self._kline_cache:
            return self._kline_cache[key]
        ctx = build_kline_history_context(symbol=symbol, market=market, lookback_days=days)
        self._kline_cache[key] = ctx
        return ctx

    @staticmethod
    def _build_snapshot_memory(
        symbol: str,
        market: MarketCode,
        context_type: str,
        days: int = 30,
    ) -> dict:
        try:
            rows = get_recent_stock_context_snapshots(
                symbol=symbol,
                market=(market.value if isinstance(market, MarketCode) else str(market)),
                context_type=context_type,
                days=max(1, days),
                limit=12,
            )
        except Exception:
            rows = []
        if not rows:
            return {}

        scores: list[int] = []
        last_topic = ""
        last_breakout = ""
        for row in rows:
            quality = row.quality or {}
            score = quality.get("score")
            try:
                if score is not None:
                    scores.append(int(score))
            except Exception:
                pass
            payload = row.payload or {}
            if not last_topic:
                last_topic = (
                    ((payload.get("news") or {}).get("history_topic") or {}).get("summary")
                    or ""
                )
            if not last_breakout:
                last_breakout = (
                    (payload.get("kline_history") or {}).get("breakout_state") or ""
                )

        latest_score = scores[0] if scores else 0
        avg_score = round(sum(scores) / len(scores), 1) if scores else 0.0
        trend = "flat"
        if len(scores) >= 2:
            delta = scores[0] - scores[-1]
            if delta >= 5:
                trend = "improving"
            elif delta <= -5:
                trend = "deteriorating"

        return {
            "window_days": max(1, days),
            "sample_count": len(rows),
            "latest_snapshot_date": rows[0].snapshot_date,
            "latest_quality_score": latest_score,
            "avg_quality_score": avg_score,
            "quality_trend": trend,
            "latest_history_topic": last_topic,
            "last_breakout_state": last_breakout,
        }

    async def build_symbol_contexts(
        self,
        *,
        agent_name: str,
        context,
        packs: dict,
        realtime_hours: int = 12,
        extended_hours: int = 72,
        history_days: int = 7,
        kline_days: int = 120,
        persist_snapshot: bool = True,
    ) -> dict:
        symbol_contexts: dict[str, dict] = {}
        all_news_for_topic: list[dict] = []
        snapshot_date = _iso_today()

        for stock in context.watchlist:
            symbol = stock.symbol
            market = stock.market
            stock_name = stock.name or symbol
            pack = packs.get(symbol)

            pack_news = list((pack.news.items if (pack and pack.news) else []) or [])
            realtime_news = _cut_by_hours(pack_news, realtime_hours)
            extended_news = _cut_by_hours(pack_news, extended_hours)
            hist_news = self._load_history_news(symbol, stock_name, days=history_days)

            realtime_ranked = rank_news_items(dedupe_news_items(realtime_news), symbol=symbol)
            extended_ranked = rank_news_items(dedupe_news_items(extended_news), symbol=symbol)
            hist_ranked = rank_news_items(dedupe_news_items(hist_news), symbol=symbol)

            hist_topic = summarize_news_topics(hist_ranked)
            kline_history = self._get_kline_history(symbol, market, kline_days)
            constraints = self._build_portfolio_constraints(context.portfolio, symbol)
            snapshot_memory = self._build_snapshot_memory(
                symbol=symbol,
                market=market,
                context_type=agent_name,
                days=max(history_days, 30),
            )

            coverage = {
                "quote": bool(pack and pack.quote),
                "technical": bool(pack and pack.technical and not pack.technical.get("error")),
                "events": bool(pack and pack.events and pack.events.items),
                "news_realtime": len(realtime_ranked) > 0,
                "news_extended": len(extended_ranked) > 0,
                "history_news": len(hist_ranked) > 0,
                "kline_history": bool(kline_history.get("available")),
            }
            quality_score = _estimate_quality_score(coverage)
            quality = {
                "score": quality_score,
                "coverage": coverage,
                "realtime_news_count": len(realtime_ranked),
                "extended_news_count": len(extended_ranked),
                "history_news_count": len(hist_ranked),
            }

            payload = {
                "symbol": symbol,
                "name": stock_name,
                "market": market.value if isinstance(market, MarketCode) else str(market),
                "technical_current": pack.technical if pack else {},
                "kline_history": kline_history,
                "news": {
                    "realtime": realtime_ranked[:8],
                    "extended": extended_ranked[:12],
                    "history": hist_ranked[:15],
                    "history_topic": hist_topic,
                },
                "events": (pack.events.items if (pack and pack.events) else [])[:8],
                "constraints": constraints,
                "memory": snapshot_memory,
                "data_quality": quality,
            }
            symbol_contexts[symbol] = payload
            all_news_for_topic.extend(realtime_ranked[:5] + hist_ranked[:5])

            if persist_snapshot:
                save_stock_context_snapshot(
                    symbol=symbol,
                    market=(market.value if isinstance(market, MarketCode) else str(market)),
                    snapshot_date=snapshot_date,
                    context_type=agent_name,
                    payload=payload,
                    quality=quality,
                )

        global_topic = summarize_news_topics(
            rank_news_items(dedupe_news_items(all_news_for_topic))
        )
        if persist_snapshot:
            save_news_topic_snapshot(
                snapshot_date=snapshot_date,
                window_days=max(1, history_days),
                symbols=[s.symbol for s in context.watchlist],
                summary=global_topic.get("summary", ""),
                topics=global_topic.get("topics", []),
                sentiment=global_topic.get("sentiment", "neutral"),
                coverage={
                    "stock_count": len(context.watchlist),
                    "news_count": len(all_news_for_topic),
                },
            )

        quality_scores = [
            int((ctx.get("data_quality") or {}).get("score") or 0)
            for ctx in symbol_contexts.values()
        ]
        quality_overview = {
            "avg_score": round(sum(quality_scores) / len(quality_scores), 1)
            if quality_scores
            else 0.0,
            "min_score": min(quality_scores) if quality_scores else 0,
            "max_score": max(quality_scores) if quality_scores else 0,
            "global_news_topic": global_topic,
            "symbol_count": len(symbol_contexts),
        }

        return {
            "symbols": symbol_contexts,
            "quality_overview": quality_overview,
        }
