from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from src.collectors.events_collector import fetch_announcement_fulltext
from src.core.analysis_history import get_latest_ta_verdict
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

# 各市场用于相对强度对比的大盘指数(代码 + 中文标签)。
# A股优先沪深300(000300);港股恒生指数;美股标普500(efinance 用 .INX)。
_INDEX_BY_MARKET: dict[str, tuple[str, str]] = {
    "CN": ("000300", "沪深300"),
    "HK": ("HSI", "恒生指数"),
    "US": (".INX", "标普500"),
}
# A股若 000300 取数失败时的兜底指数(上证指数)。
_CN_INDEX_FALLBACK: tuple[str, str] = ("000001", "上证指数")


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
        # 每次构建内,各市场大盘指数只取一次(避免逐股重复请求)。
        self._index_cache: dict[str, dict | None] = {}

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

    # ----- ② 相对大盘强度 ------------------------------------------------- #

    @staticmethod
    def _index_for_market(market) -> tuple[str, str]:
        """市场 -> (指数代码, 中文标签)。未知市场回退到沪深300。"""
        mkt = market.value if isinstance(market, MarketCode) else str(market or "")
        return _INDEX_BY_MARKET.get(mkt, _INDEX_BY_MARKET["CN"])

    def _fetch_index_context(self, symbol: str, market) -> dict:
        """取指数多周期收益。指数 secid 规则与个股不同,用 get_index_klines 显式映射直取;
        失败/不支持(如美股指数东财无K线)→ available False(fail-soft)。可被测试打桩。"""
        try:
            from src.collectors.kline_collector import get_index_klines
            from src.core.kline_context import _pct

            klines = get_index_klines(symbol, market, days=120)
            closes = [float(k.close) for k in klines if k.close is not None]
            if len(closes) < 6:
                return {"available": False}
            cur = closes[-1]
            return {
                "available": True,
                "ret_5d": _pct(cur, closes[-6]),
                "ret_20d": _pct(cur, closes[-21] if len(closes) >= 21 else None),
            }
        except Exception as e:
            logger.debug(f"指数K线获取失败 {symbol}: {e}")
            return {"available": False}

    def _get_index_context(self, market) -> dict | None:
        """取某市场大盘指数上下文,每次构建内按市场缓存一次。"""
        mkt = market.value if isinstance(market, MarketCode) else str(market or "")
        if mkt in self._index_cache:
            return self._index_cache[mkt]

        sym, _label = self._index_for_market(market)
        ctx = self._fetch_index_context(sym, market)
        # A股 000300 取不到时兜底上证指数
        if (not ctx or not ctx.get("available")) and mkt == "CN":
            ctx = self._fetch_index_context(_CN_INDEX_FALLBACK[0], market)
        self._index_cache[mkt] = ctx
        return ctx

    def _compute_relative_strength(
        self,
        *,
        market,
        kline_history: dict,
        index_ctx: dict | None,
    ) -> dict | None:
        """个股 vs 大盘的 5日/20日超额收益。任一侧数据缺失 → None(fail-soft)。"""
        try:
            if not kline_history or not kline_history.get("available"):
                return None
            if not index_ctx or not index_ctx.get("available"):
                return None

            stock_5d = kline_history.get("ret_5d")
            stock_20d = kline_history.get("ret_20d")
            index_5d = index_ctx.get("ret_5d")
            index_20d = index_ctx.get("ret_20d")

            def _excess(a, b):
                if a is None or b is None:
                    return None
                return round(float(a) - float(b), 2)

            excess_5d = _excess(stock_5d, index_5d)
            excess_20d = _excess(stock_20d, index_20d)
            if excess_5d is None and excess_20d is None:
                return None

            _sym, label = self._index_for_market(market)
            # 兜底场景下标签可能是上证,这里用实际命中的标签做近似(沪深300/上证差异不影响语义)
            return {
                "index_label": label if market != MarketCode.CN else label,
                "stock_5d": stock_5d,
                "index_5d": index_5d,
                "excess_5d": excess_5d,
                "stock_20d": stock_20d,
                "index_20d": index_20d,
                "excess_20d": excess_20d,
            }
        except Exception as e:
            logger.debug(f"相对强度计算失败: {e}")
            return None

    # ----- ① 公告全文 + 头部新闻正文保留 --------------------------------- #

    @staticmethod
    def _enrich_events_fulltext(
        events: list[dict],
        *,
        top_k: int = 3,
        importance_min: int = 2,
        max_chars: int = 1000,
    ) -> list[dict]:
        """给最重要的 top_k 条公告(importance>=importance_min)附加 content_fulltext。

        逐条 fail-soft:抓取失败/空 → 只保留标题(不加字段),绝不抛异常。
        """
        if not events:
            return events
        # 按重要性降序挑候选,保留原顺序输出
        important_idx = [
            i
            for i, ev in enumerate(events)
            if isinstance(ev, dict) and int(ev.get("importance") or 0) >= importance_min
        ]
        important_idx = important_idx[: max(0, int(top_k))]
        for i in important_idx:
            ev = events[i]
            art_code = str(ev.get("external_id") or "")
            if not art_code:
                continue
            try:
                text = fetch_announcement_fulltext(art_code)
            except Exception as e:
                logger.debug(f"公告全文注入失败 {art_code}: {e}")
                continue
            if text:
                ev["content_fulltext"] = text[:max_chars]
        return events

    @staticmethod
    def _retain_news_content(
        news: list[dict],
        *,
        top_k: int = 2,
        max_chars: int = 800,
    ) -> list[dict]:
        """头部 top_k 条新闻保留更多已有正文(放宽到 max_chars),其余维持原样。

        不抓网络,只是放宽采集层 300 字截断 —— 没有正文的条目自然保持原样。
        """
        if not news:
            return news
        for i, it in enumerate(news[: max(0, int(top_k))]):
            if not isinstance(it, dict):
                continue
            content = str(it.get("content") or "")
            if content:
                it["content"] = content[:max_chars]
        return news

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

            # ① 头部实时新闻保留更多正文(放宽采集层 300 字截断)
            realtime_for_payload = self._retain_news_content(
                [dict(it) for it in realtime_ranked[:8]], top_k=2, max_chars=800
            )
            # ① 重要公告(importance>=2)的前 2-3 条附加东财全文(纯文本,~1000 字)
            events_for_payload = self._enrich_events_fulltext(
                [dict(ev) for ev in ((pack.events.items if (pack and pack.events) else [])[:8])],
                top_k=3,
                importance_min=2,
            )

            # ② 个股相对大盘强度(指数按市场缓存一次)
            relative_strength = self._compute_relative_strength(
                market=market,
                kline_history=kline_history,
                index_ctx=self._get_index_context(market),
            )

            # ④ 最近一次 TradingAgents 深度结论(高权重先验,仅紧凑版本)
            try:
                ta_verdict = get_latest_ta_verdict(symbol, within_days=14)
            except Exception as e:
                logger.debug(f"注入 TA 深度结论失败 {symbol}: {e}")
                ta_verdict = None

            payload = {
                "symbol": symbol,
                "name": stock_name,
                "market": market.value if isinstance(market, MarketCode) else str(market),
                "technical_current": pack.technical if pack else {},
                "kline_history": kline_history,
                "relative_strength": relative_strength,
                "ta_verdict": ta_verdict,
                "news": {
                    "realtime": realtime_for_payload,
                    "extended": extended_ranked[:12],
                    "history": hist_ranked[:15],
                    "history_topic": hist_topic,
                },
                "events": events_for_payload,
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
