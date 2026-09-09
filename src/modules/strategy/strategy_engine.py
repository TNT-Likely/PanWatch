"""策略层：信号生成、后验评估、调权与统计。"""

from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from math import sqrt

from sqlalchemy import and_, case, func

from src.collectors.kline_collector import KlineCollector
from src.core.entry_candidates import refresh_entry_candidates
from src.core.json_safe import to_jsonable
from src.core.strategy_catalog import (
    ensure_strategy_catalog,
    get_effective_weight_map,
    get_strategy_profile_map,
    list_strategy_catalog,
)
from src.core.factor_weights import get_factor_weights
from src.core.timezone import to_iso_with_tz, utc_now
from src.models.market import MarketCode
from src.web.database import SessionLocal
from src.web.models import (
    EntryCandidate,
    MarketRegimeSnapshot,
    NewsCache,
    PortfolioRiskSnapshot,
    StrategyFactorSnapshot,
    StrategyOutcome,
    StrategySignalRun,
    StrategyWeight,
    StrategyWeightHistory,
)

logger = logging.getLogger(__name__)


SOURCE_POOL_LABELS = {
    "watchlist": "关注池",
    "market_scan": "市场池",
    "mixed": "市场+关注",
}


def _compact_source_meta(meta: dict | None) -> dict:
    raw = meta if isinstance(meta, dict) else {}
    if not raw:
        return {}
    out: dict[str, object] = {}
    for key in (
        "trace_id",
        "context_quality_score",
        "trigger_price",
        "entry_low",
        "entry_high",
        "stop_loss",
        "target_price",
        "invalidation",
        "source",
    ):
        value = raw.get(key)
        if value is None:
            continue
        if isinstance(value, str):
            text = value.strip()
            if not text:
                continue
            out[key] = text[:240]
        else:
            out[key] = value

    quote = raw.get("quote") if isinstance(raw.get("quote"), dict) else {}
    if quote:
        out["quote"] = {
            "current_price": _safe_float(quote.get("current_price")),
            "change_pct": _safe_float(quote.get("change_pct")),
            "turnover": _safe_float(quote.get("turnover")),
            "volume": _safe_float(quote.get("volume")),
        }
    plan = raw.get("plan") if isinstance(raw.get("plan"), dict) else {}
    if plan:
        compact_plan = {
            "entry_low": _safe_float(plan.get("entry_low")),
            "entry_high": _safe_float(plan.get("entry_high")),
            "stop_loss": _safe_float(plan.get("stop_loss")),
            "target_price": _safe_float(plan.get("target_price")),
            "invalidation": str(plan.get("invalidation") or "").strip()[:160],
        }
        out["plan"] = compact_plan
    return to_jsonable(out)


def _compact_signal_payload(payload: dict | None) -> dict:
    raw = payload if isinstance(payload, dict) else {}
    if not raw:
        return {}
    compact = {
        "source_meta": _compact_source_meta(
            raw.get("source_meta") if isinstance(raw.get("source_meta"), dict) else {}
        ),
        "score_breakdown": raw.get("score_breakdown") if isinstance(raw.get("score_breakdown"), dict) else {},
        "market_regime": raw.get("market_regime") if isinstance(raw.get("market_regime"), dict) else {},
        "cross_feature": raw.get("cross_feature") if isinstance(raw.get("cross_feature"), dict) else {},
        "news_metric": raw.get("news_metric") if isinstance(raw.get("news_metric"), dict) else {},
        "constrained": bool(raw.get("constrained")),
        "constraint_reasons": raw.get("constraint_reasons") if isinstance(raw.get("constraint_reasons"), list) else [],
    }
    return to_jsonable(compact)


def _normalize_action_view(
    *,
    action: str,
    action_label: str,
    is_holding: bool,
    rank_score: float,
    has_entry_plan: bool,
) -> tuple[str, str, float]:
    act = (action or "watch").strip().lower() or "watch"
    label = (action_label or "").strip()
    score = float(rank_score or 0.0)

    if is_holding:
        if act == "buy":
            act = "add"
            label = "准备加仓"
    else:
        if act == "add":
            act = "buy"
            label = "建仓"
        elif act == "hold":
            act = "watch"
            label = "观望"
        if label in ("持有", "继续持有"):
            act = "watch"
            label = "观望"

    if act in ("watch", "hold"):
        score = min(score, 78.0 if is_holding else 65.0)
        if not label:
            label = "持有" if is_holding else "观望"
    elif act == "buy":
        if not label:
            label = "建仓"
    elif act == "add":
        if not label:
            label = "准备加仓"
    else:
        if not label:
            label = "观望"

    if act in ("buy", "add") and not has_entry_plan:
        score = min(score, 66.0)

    return act, label, _clamp(score, 0.0, 100.0)

RISK_LEVEL_LABELS = {
    "low": "低风险",
    "medium": "中风险",
    "high": "高风险",
}

REGIME_LABELS = {
    "bullish": "多头",
    "neutral": "震荡",
    "bearish": "空头",
}

POSITIVE_EVENT_KEYWORDS = (
    "增持",
    "中标",
    "合作",
    "回购",
    "盈利",
    "订单",
    "突破",
    "上调",
    "增长",
    "利好",
    "buyback",
    "contract",
    "beat",
    "upgrade",
)

NEGATIVE_EVENT_KEYWORDS = (
    "减持",
    "诉讼",
    "亏损",
    "下调",
    "违约",
    "处罚",
    "暴跌",
    "利空",
    "预警",
    "st",
    "downgrade",
    "miss",
    "fraud",
    "investigation",
)

MAX_UNHELD_ACTIVE_BY_MARKET = {
    "CN": 30,
    "HK": 20,
    "US": 20,
}

MAX_HIGH_RISK_RATIO_BY_MARKET = {
    "CN": 0.35,
    "HK": 0.32,
    "US": 0.30,
}

MAX_SINGLE_STRATEGY_SHARE = 0.42


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _safe_float(value) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except Exception:
        return None


def _to_market(value: str | None) -> MarketCode:
    try:
        return MarketCode((value or "CN").strip().upper())
    except Exception:
        return MarketCode.CN


def _parse_day(value: str | None) -> date | None:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None
    for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except Exception:
            continue
    return None


def _pick_close_on_or_before(klines: list, target: date) -> float | None:
    if not klines:
        return None
    rows: list[tuple[date, float]] = []
    for k in klines:
        d = _parse_day(getattr(k, "date", None))
        c = getattr(k, "close", None)
        if d is None or c is None:
            continue
        try:
            rows.append((d, float(c)))
        except Exception:
            continue
    if not rows:
        return None
    rows.sort(key=lambda x: x[0])
    for d, c in reversed(rows):
        if d <= target:
            return c
    return None


def _strategy_codes_for_candidate(row: EntryCandidate) -> list[str]:
    tags = [str(x).strip() for x in (row.strategy_tags or []) if str(x).strip()]
    codes: list[str] = []
    for tag in tags:
        codes.append(tag)
    if (row.candidate_source or "").strip() in ("market_scan", "mixed"):
        codes.append("market_scan")
    if not codes:
        codes.append("watchlist_agent")
    out: list[str] = []
    seen = set()
    for c in codes:
        if c in seen:
            continue
        out.append(c)
        seen.add(c)
    return out


def _compute_rank_score(
    *,
    row: EntryCandidate,
    weight: float,
    risk_level: str,
) -> float:
    base = float(row.score or 0.0)
    quality_bonus = min(12.0, max(0.0, float(row.plan_quality or 0) / 12.0))
    source_bonus = (
        2.0 if (row.candidate_source or "") == "market_scan" else 1.2 if (row.candidate_source or "") == "mixed" else 0.0
    )
    status_penalty = -14.0 if (row.status or "inactive") != "active" else 0.0
    risk_penalty = 0.0
    if risk_level == "high":
        risk_penalty = -1.0
    rank = base * float(weight or 1.0) + quality_bonus + source_bonus + status_penalty + risk_penalty
    return _clamp(rank, 0.0, 100.0)


def _iso(dt) -> str:
    if not dt:
        return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=utc_now().tzinfo)
    return to_iso_with_tz(dt)


def _source_label(value: str) -> str:
    return SOURCE_POOL_LABELS.get((value or "").strip(), value or "")


def _risk_label(value: str) -> str:
    return RISK_LEVEL_LABELS.get((value or "").strip(), value or "")


def _regime_label(value: str) -> str:
    return REGIME_LABELS.get((value or "").strip(), value or "")


def _stdev(values: list[float]) -> float | None:
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    var = sum((x - mean) ** 2 for x in values) / (len(values) - 1)
    return sqrt(max(var, 0.0))


def _extract_candidate_quote_change_pct(row: EntryCandidate) -> float | None:
    meta = row.meta if isinstance(row.meta, dict) else {}
    quote = meta.get("quote") if isinstance(meta.get("quote"), dict) else {}
    if quote.get("change_pct") is not None:
        return _safe_float(quote.get("change_pct"))
    source_meta = meta.get("source_meta") if isinstance(meta.get("source_meta"), dict) else {}
    source_quote = source_meta.get("quote") if isinstance(source_meta.get("quote"), dict) else {}
    return _safe_float(source_quote.get("change_pct"))


def _extract_candidate_volume_ratio(row: EntryCandidate) -> float | None:
    meta = row.meta if isinstance(row.meta, dict) else {}
    kline = meta.get("kline") if isinstance(meta.get("kline"), dict) else {}
    return _safe_float(kline.get("volume_ratio"))


def _extract_candidate_turnover(row: EntryCandidate) -> float | None:
    meta = row.meta if isinstance(row.meta, dict) else {}
    quote = meta.get("quote") if isinstance(meta.get("quote"), dict) else {}
    if quote.get("turnover") is not None:
        return _safe_float(quote.get("turnover"))
    source_meta = meta.get("source_meta") if isinstance(meta.get("source_meta"), dict) else {}
    source_quote = source_meta.get("quote") if isinstance(source_meta.get("quote"), dict) else {}
    return _safe_float(source_quote.get("turnover"))


def _classify_market_regime(
    *,
    breadth_up_pct: float | None,
    avg_change_pct: float | None,
    active_ratio: float,
) -> tuple[str, float, float]:
    breadth_norm = (
        _clamp(((breadth_up_pct or 50.0) - 50.0) / 50.0, -1.0, 1.0)
        if breadth_up_pct is not None
        else 0.0
    )
    change_norm = (
        _clamp((avg_change_pct or 0.0) / 3.2, -1.0, 1.0)
        if avg_change_pct is not None
        else 0.0
    )
    active_norm = _clamp((active_ratio - 0.5) / 0.5, -1.0, 1.0)
    score = 0.45 * breadth_norm + 0.30 * change_norm + 0.25 * active_norm
    if score >= 0.20:
        regime = "bullish"
    elif score <= -0.20:
        regime = "bearish"
    else:
        regime = "neutral"
    confidence = _clamp(abs(score) * 1.45 + 0.15, 0.0, 1.0)
    return regime, round(score, 4), round(confidence, 4)


def _build_market_regime_rows(
    *,
    snapshot: str,
    candidates: list[EntryCandidate],
) -> dict[str, dict]:
    by_market: dict[str, list[EntryCandidate]] = {}
    for row in candidates:
        mkt = (row.stock_market or "CN").strip().upper() or "CN"
        by_market.setdefault(mkt, []).append(row)

    out: dict[str, dict] = {}
    for market, rows in by_market.items():
        sample_size = len(rows)
        if sample_size <= 0:
            continue
        active_count = sum(1 for x in rows if (x.status or "inactive") == "active")
        active_ratio = active_count / sample_size if sample_size else 0.0
        changes = [x for x in (_extract_candidate_quote_change_pct(r) for r in rows) if x is not None]
        breadth_up_pct = (sum(1 for c in changes if c > 0) / len(changes) * 100.0) if changes else None
        avg_change_pct = (sum(changes) / len(changes)) if changes else None
        volatility_pct = _stdev(changes) if len(changes) >= 2 else None
        regime, regime_score, confidence = _classify_market_regime(
            breadth_up_pct=breadth_up_pct,
            avg_change_pct=avg_change_pct,
            active_ratio=active_ratio,
        )
        out[market] = {
            "snapshot_date": snapshot,
            "market": market,
            "regime": regime,
            "regime_label": _regime_label(regime),
            "regime_score": regime_score,
            "confidence": confidence,
            "breadth_up_pct": round(breadth_up_pct, 4) if breadth_up_pct is not None else None,
            "avg_change_pct": round(avg_change_pct, 4) if avg_change_pct is not None else None,
            "volatility_pct": round(volatility_pct, 4) if volatility_pct is not None else None,
            "active_ratio": round(active_ratio, 4),
            "sample_size": sample_size,
            "meta": {
                "active_signals": active_count,
                "total_signals": sample_size,
            },
        }
    return out


def _upsert_market_regime_snapshots(
    *,
    db,
    snapshot: str,
    candidates: list[EntryCandidate],
) -> dict[str, dict]:
    rows = _build_market_regime_rows(snapshot=snapshot, candidates=candidates)
    for market, data in rows.items():
        row = (
            db.query(MarketRegimeSnapshot)
            .filter(
                MarketRegimeSnapshot.snapshot_date == snapshot,
                MarketRegimeSnapshot.market == market,
            )
            .first()
        )
        if not row:
            row = MarketRegimeSnapshot(
                snapshot_date=snapshot,
                market=market,
            )
            db.add(row)
        row.regime = data["regime"]
        row.regime_score = float(data["regime_score"] or 0.0)
        row.confidence = float(data["confidence"] or 0.0)
        row.breadth_up_pct = data["breadth_up_pct"]
        row.avg_change_pct = data["avg_change_pct"]
        row.volatility_pct = data["volatility_pct"]
        row.active_ratio = float(data["active_ratio"] or 0.0)
        row.sample_size = int(data["sample_size"] or 0)
        row.meta = to_jsonable(data.get("meta") or {})
        row.updated_at = utc_now()
    return rows


def _load_news_metrics(
    *,
    db,
    candidates: list[EntryCandidate],
    lookback_hours: int = 72,
    max_rows: int = 5000,
) -> dict[str, dict]:
    if not candidates:
        return {}
    symbol_set = {
        (c.stock_symbol or "").strip().upper()
        for c in candidates
        if (c.stock_symbol or "").strip()
    }
    symbol_name_map = {
        (c.stock_symbol or "").strip().upper(): (c.stock_name or "").strip()
        for c in candidates
        if (c.stock_symbol or "").strip()
    }
    name_symbol_pairs = [
        (name, sym)
        for sym, name in symbol_name_map.items()
        if name and len(name) >= 2
    ]
    if not symbol_set:
        return {}

    cutoff = utc_now() - timedelta(hours=max(1, int(lookback_hours)))
    rows = (
        db.query(NewsCache)
        .filter(NewsCache.publish_time >= cutoff)
        .order_by(NewsCache.publish_time.desc())
        .limit(max(100, int(max_rows)))
        .all()
    )
    if not rows:
        return {}

    now = utc_now()
    metrics: dict[str, dict] = {}

    for n in rows:
        linked = set()
        for s in (n.symbols or []):
            x = str(s or "").strip().upper()
            if x and x in symbol_set:
                linked.add(x)
        if not linked:
            # Fallback: match by stock name mention in title/content to improve hit rate.
            text_for_match = f"{n.title or ''} {n.content or ''}"
            if text_for_match:
                for name, sym in name_symbol_pairs:
                    if name in text_for_match:
                        linked.add(sym)
            if not linked:
                continue
        title = str(n.title or "")
        content = str(n.content or "")
        text = f"{title} {content}".lower()
        event_bias = 0.0
        for kw in POSITIVE_EVENT_KEYWORDS:
            if kw.lower() in text:
                event_bias += 1.0
        for kw in NEGATIVE_EVENT_KEYWORDS:
            if kw.lower() in text:
                event_bias -= 1.2

        importance = int(n.importance or 0)
        published_at = n.publish_time
        if published_at is None:
            published_at = now
        if published_at.tzinfo is None:
            published_at = published_at.replace(tzinfo=now.tzinfo)
        age_hours = max(0.0, (now - published_at).total_seconds() / 3600.0)
        recency_weight = _clamp(1.0 - age_hours / 72.0, 0.05, 1.0)
        event_weight = recency_weight * (0.8 + 0.6 * importance)

        for sym in linked:
            m = metrics.setdefault(
                sym,
                {
                    "news_count": 0,
                    "high_importance_count": 0,
                    "importance_weighted": 0.0,
                    "event_bias_sum": 0.0,
                    "latest_age_hours": None,
                    "event_score": 0.0,
                    "event_bias": 0.0,
                },
            )
            m["news_count"] += 1
            if importance >= 2:
                m["high_importance_count"] += 1
            m["importance_weighted"] += float(event_weight)
            m["event_bias_sum"] += float(event_bias * recency_weight)
            if (m["latest_age_hours"] is None) or (age_hours < float(m["latest_age_hours"])):
                m["latest_age_hours"] = float(age_hours)

    for sym, m in metrics.items():
        cnt = int(m["news_count"] or 0)
        imp = float(m["importance_weighted"] or 0.0)
        hi_cnt = int(m["high_importance_count"] or 0)
        latest_age = float(m["latest_age_hours"] or 72.0)
        freshness_bonus = _clamp((24.0 - latest_age) / 8.0, -2.0, 3.0)
        event_score = _clamp(imp * 1.2 + hi_cnt * 1.4 + freshness_bonus, -6.0, 12.0)
        event_bias = _clamp(float(m["event_bias_sum"] or 0.0) / max(1.0, cnt), -3.0, 3.0)
        m["event_score"] = round(event_score, 4)
        m["event_bias"] = round(event_bias, 4)
        m["event_tier"] = (
            "high" if event_score >= 6.5 else "medium" if event_score >= 3.0 else "low"
        )
    return metrics


def _default_news_metric() -> dict:
    return {
        "news_count": 0,
        "high_importance_count": 0,
        "importance_weighted": 0.0,
        "event_bias_sum": 0.0,
        "latest_age_hours": None,
        "event_score": 0.0,
        "event_bias": 0.0,
        "event_tier": "low",
    }


def _normalize_news_metric(value: dict | None) -> dict:
    base = _default_news_metric()
    if isinstance(value, dict):
        for key in base.keys():
            if key in value and value.get(key) is not None:
                base[key] = value.get(key)
    return base


def _build_cross_section_features(candidates: list[EntryCandidate]) -> dict[int, dict]:
    by_market: dict[str, list[EntryCandidate]] = {}
    for c in candidates:
        market = (c.stock_market or "CN").strip().upper() or "CN"
        by_market.setdefault(market, []).append(c)

    out: dict[int, dict] = {}
    for market, rows in by_market.items():
        n = len(rows)
        if n <= 0:
            continue

        def _rank_map(values: dict[int, float], reverse: bool = True) -> dict[int, float]:
            sorted_items = sorted(values.items(), key=lambda x: x[1], reverse=reverse)
            rank: dict[int, float] = {}
            if not sorted_items:
                return rank
            for i, (rid, _val) in enumerate(sorted_items):
                pct = 100.0 * (1.0 - (i / max(1, len(sorted_items) - 1)))
                rank[rid] = round(_clamp(pct, 0.0, 100.0), 4)
            return rank

        score_values = {int(c.id): float(c.score or 0.0) for c in rows if c.id is not None}
        change_values = {
            int(c.id): float(_extract_candidate_quote_change_pct(c) or 0.0)
            for c in rows
            if c.id is not None
        }
        turnover_values = {
            int(c.id): float(_extract_candidate_turnover(c) or 0.0)
            for c in rows
            if c.id is not None
        }
        vol_values = {
            int(c.id): float(_extract_candidate_volume_ratio(c) or 0.0)
            for c in rows
            if c.id is not None
        }
        score_pct = _rank_map(score_values, reverse=True)
        change_pct = _rank_map(change_values, reverse=True)
        turnover_pct = _rank_map(turnover_values, reverse=True)
        vol_pct = _rank_map(vol_values, reverse=True)

        for c in rows:
            if c.id is None:
                continue
            cid = int(c.id)
            rs = (
                0.45 * float(score_pct.get(cid, 50.0))
                + 0.25 * float(change_pct.get(cid, 50.0))
                + 0.20 * float(turnover_pct.get(cid, 50.0))
                + 0.10 * float(vol_pct.get(cid, 50.0))
            )
            crowd = 0.0
            if rs >= 92:
                crowd += 2.5
            elif rs >= 85:
                crowd += 1.5
            if (turnover_pct.get(cid, 0.0) >= 95.0) and (change_pct.get(cid, 0.0) >= 92.0):
                crowd += 1.5

            out[cid] = {
                "market": market,
                "score_pct": float(score_pct.get(cid, 50.0)),
                "change_pct_rank": float(change_pct.get(cid, 50.0)),
                "turnover_pct_rank": float(turnover_pct.get(cid, 50.0)),
                "volume_pct_rank": float(vol_pct.get(cid, 50.0)),
                "relative_strength_pct": round(_clamp(rs, 0.0, 100.0), 4),
                "crowding_risk": round(_clamp(crowd, 0.0, 6.0), 4),
            }
    return out


def _demote_signal(row: StrategySignalRun, *, reason: str) -> None:
    row.status = "inactive"
    row.action = "watch"
    row.action_label = "观望"
    payload = row.payload if isinstance(row.payload, dict) else {}
    demoted_cap = 69.0 if bool(row.is_holding_snapshot) else 65.0
    row.rank_score = min(float(row.rank_score or 0.0), demoted_cap)
    if row.confidence is not None:
        row.confidence = min(float(row.confidence or 0.0), demoted_cap / 100.0)
    breakdown = payload.get("score_breakdown")
    if isinstance(breakdown, dict):
        breakdown["weighted_score"] = round(float(row.rank_score or 0.0), 4)
        payload["score_breakdown"] = breakdown
    reasons = payload.get("constraint_reasons")
    if not isinstance(reasons, list):
        reasons = []
    reasons = [str(x) for x in reasons if str(x).strip()]
    reasons.append(reason)
    payload["constraint_reasons"] = reasons[:6]
    payload["constrained"] = True
    if row.reason:
        if reason not in row.reason:
            row.reason = f"{row.reason}；{reason}"
    else:
        row.reason = reason
    row.payload = to_jsonable(payload)
    row.updated_at = utc_now()


def _apply_portfolio_constraints(*, rows: list[StrategySignalRun]) -> dict:
    by_market: dict[str, list[StrategySignalRun]] = {}
    for r in rows:
        m = (r.stock_market or "CN").strip().upper() or "CN"
        by_market.setdefault(m, []).append(r)

    demoted = 0
    by_reason: dict[str, int] = {}
    for market, market_rows in by_market.items():
        active_unheld = [
            x
            for x in market_rows
            if (x.status or "inactive") == "active"
            and not bool(x.is_holding_snapshot)
            and (x.action or "watch") in ("buy", "add")
        ]
        active_unheld.sort(key=lambda x: float(x.rank_score or 0.0), reverse=True)

        max_unheld = int(MAX_UNHELD_ACTIVE_BY_MARKET.get(market, 20))
        for idx, row in enumerate(active_unheld):
            if idx < max_unheld:
                continue
            _demote_signal(row, reason=f"组合约束: {market} 未持仓机会超限({max_unheld})")
            demoted += 1
            by_reason["cap_unheld"] = by_reason.get("cap_unheld", 0) + 1

        remaining = [
            x
            for x in active_unheld[:max_unheld]
            if (x.status or "inactive") == "active"
        ]
        if not remaining:
            continue

        high_rows = [x for x in remaining if (x.risk_level or "medium") == "high"]
        max_ratio = float(MAX_HIGH_RISK_RATIO_BY_MARKET.get(market, 0.32))
        allow_high = max(1, int(round(len(remaining) * max_ratio)))
        high_rows.sort(key=lambda x: float(x.rank_score or 0.0), reverse=True)
        for idx, row in enumerate(high_rows):
            if idx < allow_high:
                continue
            _demote_signal(row, reason=f"组合约束: {market} 高风险占比超限({int(max_ratio*100)}%)")
            demoted += 1
            by_reason["cap_high_risk"] = by_reason.get("cap_high_risk", 0) + 1

        final_rows = [
            x for x in remaining if (x.status or "inactive") == "active"
        ]
        if not final_rows:
            continue
        cap_per_strategy = max(1, int(round(len(final_rows) * MAX_SINGLE_STRATEGY_SHARE)))
        by_strategy: dict[str, list[StrategySignalRun]] = {}
        for x in final_rows:
            by_strategy.setdefault(x.strategy_code or "unknown", []).append(x)
        for code, srows in by_strategy.items():
            srows.sort(key=lambda x: float(x.rank_score or 0.0), reverse=True)
            for idx, row in enumerate(srows):
                if idx < cap_per_strategy:
                    continue
                _demote_signal(row, reason=f"组合约束: {market} 策略{code}集中度过高")
                demoted += 1
                by_reason["cap_strategy_concentration"] = by_reason.get(
                    "cap_strategy_concentration", 0
                ) + 1

    return {"demoted": demoted, "by_reason": by_reason}


def _compute_factor_breakdown(
    *,
    row: EntryCandidate,
    strategy_code: str,
    weight: float,
    risk_level: str,
    regime_info: dict | None,
    cross_feature: dict | None = None,
    news_metric: dict | None = None,
    factor_weights: dict | None = None,
) -> dict:
    base_score = float(row.score or 0.0)
    action = (row.action or "watch").strip().lower() or "watch"
    is_holding = bool(row.is_holding_snapshot)
    signal_text = f"{row.signal or ''} {row.reason or ''}".lower()
    plan_quality = int(row.plan_quality or 0)
    quote_change_pct = _extract_candidate_quote_change_pct(row)
    volume_ratio = _extract_candidate_volume_ratio(row)
    turnover = _extract_candidate_turnover(row)
    is_market_scan = (row.candidate_source or "").strip() in ("market_scan", "mixed")
    cf = cross_feature if isinstance(cross_feature, dict) else {}
    nm = news_metric if isinstance(news_metric, dict) else {}
    relative_strength_pct = _safe_float(cf.get("relative_strength_pct"))
    crowding_risk = _safe_float(cf.get("crowding_risk")) or 0.0
    event_score = float(_safe_float(nm.get("event_score")) or 0.0)
    event_bias = float(_safe_float(nm.get("event_bias")) or 0.0)
    event_count = int(nm.get("news_count") or 0)

    alpha_score = _clamp((base_score - 50.0) * 0.45, -12.0, 18.0)
    if relative_strength_pct is not None:
        alpha_score += _clamp((relative_strength_pct - 50.0) / 15.0, -2.0, 4.0)
    catalyst_score = 0.0
    if is_market_scan:
        catalyst_score += 2.5
    if quote_change_pct is not None:
        if 1.0 <= quote_change_pct <= 7.0:
            catalyst_score += 4.0
        elif quote_change_pct > 9.0:
            catalyst_score += 1.5
        elif quote_change_pct < -4.0:
            catalyst_score -= 2.5
    if ("突破" in signal_text) or ("breakout" in signal_text):
        catalyst_score += 2.5
    if "回踩" in signal_text:
        catalyst_score += 1.5
    if "超跌" in signal_text:
        catalyst_score += 1.0
    catalyst_score += _clamp(event_score * 0.55, -3.0, 6.5)
    if event_bias > 0.8:
        catalyst_score += 1.2
    if relative_strength_pct is not None:
        catalyst_score += _clamp((relative_strength_pct - 60.0) / 12.0, -2.5, 4.5)

    quality_score = _clamp((plan_quality - 50.0) / 5.0, -8.0, 10.0)
    if event_count >= 3:
        quality_score += 0.8

    risk_penalty = 0.0
    if risk_level == "high":
        risk_penalty += 1.5
    if quote_change_pct is not None and abs(quote_change_pct) >= 8.0:
        risk_penalty += 2.0
    if (row.status or "inactive") != "active":
        risk_penalty += 2.5
    if plan_quality < 70:
        risk_penalty += 1.5
    if event_bias < -0.9:
        risk_penalty += 2.2

    crowd_penalty = 0.0
    if quote_change_pct is not None and quote_change_pct >= 9.0:
        crowd_penalty += 2.5
    if volume_ratio is not None and volume_ratio >= 3.0:
        crowd_penalty += 1.5
    if turnover is not None and turnover >= 8_000_000_000:
        crowd_penalty += 1.0
    crowd_penalty += _clamp(crowding_risk, 0.0, 6.0)

    source_bonus = 0.0
    if (row.source_agent or "") in ("premarket_outlook", "intraday_monitor"):
        source_bonus += 1.0
    if strategy_code in ("trend_follow", "volume_breakout", "macd_golden"):
        source_bonus += 0.8
    if relative_strength_pct is not None and relative_strength_pct >= 80:
        source_bonus += 0.8

    regime = (regime_info or {}).get("regime") or "neutral"
    regime_confidence = float((regime_info or {}).get("confidence") or 0.0)
    regime_multiplier = 1.0
    if regime == "bullish":
        regime_multiplier = 1.06 if action in ("buy", "add") else 1.01
    elif regime == "bearish":
        regime_multiplier = 0.90 if action in ("buy", "add") else 0.97
    regime_multiplier += _clamp((regime_confidence - 0.5) * 0.06, -0.03, 0.03)
    regime_multiplier = _clamp(regime_multiplier, 0.85, 1.12)

    # 每因子外置权重(默认 1.0 → 行为 = 现状,零回归)。snapshot 仍存 raw 因子分,
    # 权重只作用于合成,确保 IC 测在原始因子上(见 factor_calibration 设计要点)。
    fw = factor_weights or {}
    raw_score = (
        base_score
        + fw.get("alpha_score", 1.0) * alpha_score
        + fw.get("catalyst_score", 1.0) * catalyst_score
        + fw.get("quality_score", 1.0) * quality_score
        + source_bonus  # v1: source_bonus 权重固定 1.0
    )
    raw_score -= fw.get("risk_penalty", 1.0) * risk_penalty
    raw_score -= fw.get("crowd_penalty", 1.0) * crowd_penalty
    has_entry = row.entry_low is not None or row.entry_high is not None
    if action in ("buy", "add") and not has_entry:
        # No entry window means this is not executable; force into watch semantics.
        raw_score -= 8.0
    if action in ("buy", "add") and plan_quality < 90:
        raw_score -= 6.0
    final_score = _clamp(raw_score * float(weight or 1.0) * regime_multiplier, 0.0, 100.0)
    # Keep score semantics aligned with action/status: high scores should be actionable.
    if (row.status or "inactive") != "active":
        final_score = min(final_score, 69.0)
    if action in ("hold", "watch"):
        final_score = min(final_score, 78.0 if is_holding else 65.0)
    if action in ("buy", "add") and not has_entry:
        final_score = min(final_score, 66.0)

    return {
        "base_score": round(base_score, 4),
        "alpha_score": round(alpha_score, 4),
        "catalyst_score": round(catalyst_score, 4),
        "quality_score": round(quality_score, 4),
        "risk_penalty": round(risk_penalty, 4),
        "crowd_penalty": round(crowd_penalty, 4),
        "source_bonus": round(source_bonus, 4),
        "regime": regime,
        "regime_label": _regime_label(regime),
        "regime_multiplier": round(regime_multiplier, 4),
        "raw_score": round(raw_score, 4),
        "weighted_score": round(final_score, 4),
        "weight": round(float(weight or 1.0), 4),
        "relative_strength_pct": round(float(relative_strength_pct or 0.0), 4)
        if relative_strength_pct is not None
        else None,
        "event_score": round(float(event_score), 4),
        "event_bias": round(float(event_bias), 4),
        "event_count": event_count,
        "crowding_risk": round(float(crowding_risk or 0.0), 4),
        "has_entry_plan": bool(has_entry),
    }


def _sync_factor_and_risk_snapshots(
    *,
    db,
    snapshot: str,
    signals: list[StrategySignalRun],
) -> None:
    run_ids = [int(s.id) for s in signals if s.id is not None]
    if not run_ids:
        return

    existing_factors = (
        db.query(StrategyFactorSnapshot)
        .filter(StrategyFactorSnapshot.snapshot_date == snapshot)
        .all()
    )
    factor_map = {int(x.signal_run_id): x for x in existing_factors}
    touched_ids: set[int] = set()

    for s in signals:
        if s.id is None:
            continue
        sid = int(s.id)
        payload = s.payload if isinstance(s.payload, dict) else {}
        breakdown = payload.get("score_breakdown") if isinstance(payload.get("score_breakdown"), dict) else {}
        row = factor_map.get(sid)
        if not row:
            row = StrategyFactorSnapshot(
                signal_run_id=sid,
                snapshot_date=snapshot,
                stock_symbol=s.stock_symbol,
                stock_market=s.stock_market,
                strategy_code=s.strategy_code,
            )
            db.add(row)
            factor_map[sid] = row
        row.snapshot_date = snapshot
        row.stock_symbol = s.stock_symbol
        row.stock_market = s.stock_market
        row.strategy_code = s.strategy_code
        row.alpha_score = float(breakdown.get("alpha_score") or 0.0)
        row.catalyst_score = float(breakdown.get("catalyst_score") or 0.0)
        row.quality_score = float(breakdown.get("quality_score") or 0.0)
        row.risk_penalty = float(breakdown.get("risk_penalty") or 0.0)
        row.crowd_penalty = float(breakdown.get("crowd_penalty") or 0.0)
        row.source_bonus = float(breakdown.get("source_bonus") or 0.0)
        row.regime_multiplier = float(breakdown.get("regime_multiplier") or 1.0)
        row.final_score = float(breakdown.get("weighted_score") or s.rank_score or 0.0)
        row.factor_payload = to_jsonable(
            {
                "score_breakdown": breakdown,
                "source_pool": s.source_pool or "watchlist",
                "risk_level": s.risk_level or "medium",
                "cross_feature": payload.get("cross_feature")
                if isinstance(payload.get("cross_feature"), dict)
                else {},
                "news_metric": _normalize_news_metric(
                    payload.get("news_metric")
                    if isinstance(payload.get("news_metric"), dict)
                    else None
                ),
                "constrained": bool(payload.get("constrained")),
                "constraint_reasons": payload.get("constraint_reasons")
                if isinstance(payload.get("constraint_reasons"), list)
                else [],
            }
        )
        row.updated_at = utc_now()
        touched_ids.add(sid)

    stale_factor_ids = [
        int(x.id)
        for x in existing_factors
        if x.id is not None and int(x.signal_run_id or -1) not in touched_ids
    ]
    if stale_factor_ids:
        db.query(StrategyFactorSnapshot).filter(
            StrategyFactorSnapshot.id.in_(stale_factor_ids)
        ).delete(synchronize_session=False)

    # Risk snapshot by snapshot_date + market
    by_market: dict[str, list[StrategySignalRun]] = {}
    for s in signals:
        market = (s.stock_market or "CN").strip().upper() or "CN"
        by_market.setdefault(market, []).append(s)

    for market, rows in by_market.items():
        total = len(rows)
        active = sum(1 for x in rows if (x.status or "inactive") == "active")
        held = sum(1 for x in rows if bool(x.is_holding_snapshot))
        unheld = max(0, total - held)
        high_risk = sum(1 for x in rows if (x.risk_level or "medium") == "high")
        high_risk_ratio = (high_risk / total) if total else 0.0
        sorted_scores = sorted([float(x.rank_score or 0.0) for x in rows], reverse=True)
        score_sum = sum(sorted_scores)
        top5 = sum(sorted_scores[:5])
        concentration_top5 = (top5 / score_sum) if score_sum > 0 else 0.0
        avg_rank_score = (score_sum / total) if total else 0.0

        if high_risk_ratio >= 0.45 or concentration_top5 >= 0.65:
            risk_level = "high"
        elif high_risk_ratio >= 0.28 or concentration_top5 >= 0.48:
            risk_level = "medium"
        else:
            risk_level = "low"

        row = (
            db.query(PortfolioRiskSnapshot)
            .filter(
                PortfolioRiskSnapshot.snapshot_date == snapshot,
                PortfolioRiskSnapshot.market == market,
            )
            .first()
        )
        if not row:
            row = PortfolioRiskSnapshot(
                snapshot_date=snapshot,
                market=market,
            )
            db.add(row)
        row.total_signals = total
        row.active_signals = active
        row.held_signals = held
        row.unheld_signals = unheld
        row.high_risk_ratio = round(high_risk_ratio, 4)
        row.concentration_top5 = round(concentration_top5, 4)
        row.avg_rank_score = round(avg_rank_score, 4)
        row.risk_level = risk_level
        row.meta = to_jsonable(
            {
                "score_sum": round(score_sum, 4),
                "top5_score_sum": round(top5, 4),
            }
        )
        row.updated_at = utc_now()


def _format_signal(
    row: StrategySignalRun,
    *,
    include_payload: bool = True,
    factor_snapshot: StrategyFactorSnapshot | None = None,
) -> dict:
    payload_raw = row.payload if isinstance(row.payload, dict) else {}
    payload = _compact_signal_payload(payload_raw) if include_payload else {}

    if include_payload:
        score_breakdown = (
            payload.get("score_breakdown")
            if isinstance(payload.get("score_breakdown"), dict)
            else {}
        )
        market_regime = (
            payload.get("market_regime")
            if isinstance(payload.get("market_regime"), dict)
            else {}
        )
        cross_feature = (
            payload.get("cross_feature")
            if isinstance(payload.get("cross_feature"), dict)
            else {}
        )
        news_metric = _normalize_news_metric(
            payload.get("news_metric") if isinstance(payload.get("news_metric"), dict) else None
        )
        constraint_reasons = payload.get("constraint_reasons")
        if not isinstance(constraint_reasons, list):
            constraint_reasons = []
        constrained = bool(payload.get("constrained"))
    else:
        fp = (
            factor_snapshot.factor_payload
            if factor_snapshot is not None and isinstance(factor_snapshot.factor_payload, dict)
            else {}
        )
        market_regime = fp.get("market_regime") if isinstance(fp.get("market_regime"), dict) else {}
        cross_feature = fp.get("cross_feature") if isinstance(fp.get("cross_feature"), dict) else {}
        news_metric = _normalize_news_metric(
            fp.get("news_metric") if isinstance(fp.get("news_metric"), dict) else None
        )
        raw_reasons = fp.get("constraint_reasons")
        constraint_reasons = raw_reasons if isinstance(raw_reasons, list) else []
        constrained = bool(fp.get("constrained"))
        score_breakdown = {
            "alpha_score": round(float(getattr(factor_snapshot, "alpha_score", 0.0) or 0.0), 4),
            "catalyst_score": round(float(getattr(factor_snapshot, "catalyst_score", 0.0) or 0.0), 4),
            "quality_score": round(float(getattr(factor_snapshot, "quality_score", 0.0) or 0.0), 4),
            "risk_penalty": round(float(getattr(factor_snapshot, "risk_penalty", 0.0) or 0.0), 4),
            "crowd_penalty": round(float(getattr(factor_snapshot, "crowd_penalty", 0.0) or 0.0), 4),
            "source_bonus": round(float(getattr(factor_snapshot, "source_bonus", 0.0) or 0.0), 4),
            "regime_multiplier": round(
                float(getattr(factor_snapshot, "regime_multiplier", 1.0) or 1.0), 4
            ),
            "weighted_score": round(
                float(getattr(factor_snapshot, "final_score", row.rank_score) or row.rank_score or 0.0),
                4,
            ),
            "has_entry_plan": bool(row.entry_low is not None or row.entry_high is not None),
        }
    has_entry_plan = bool(
        row.entry_low is not None
        or row.entry_high is not None
        or (
            include_payload
            and
            isinstance(payload.get("source_meta"), dict)
            and isinstance(payload["source_meta"].get("plan"), dict)
            and (
                _safe_float(payload["source_meta"]["plan"].get("entry_low")) is not None
                or _safe_float(payload["source_meta"]["plan"].get("entry_high")) is not None
            )
        )
    )
    action, action_label, rank_score = _normalize_action_view(
        action=row.action or "watch",
        action_label=row.action_label or "",
        is_holding=bool(row.is_holding_snapshot),
        rank_score=float(row.rank_score or 0.0),
        has_entry_plan=has_entry_plan,
    )
    return {
        "id": row.id,
        "snapshot_date": row.snapshot_date,
        "stock_symbol": row.stock_symbol,
        "stock_market": row.stock_market,
        "stock_name": row.stock_name,
        "strategy_code": row.strategy_code,
        "strategy_name": row.strategy_name,
        "strategy_version": row.strategy_version,
        "risk_level": row.risk_level,
        "risk_level_label": _risk_label(row.risk_level or "medium"),
        "source_pool": row.source_pool or "watchlist",
        "source_pool_label": _source_label(row.source_pool or "watchlist"),
        "score": round(float(row.score or 0), 2),
        "rank_score": round(float(rank_score or 0), 2),
        "confidence": round(float(row.confidence or 0), 3) if row.confidence is not None else None,
        "status": row.status or "inactive",
        "action": action,
        "action_label": action_label,
        "signal": row.signal or "",
        "reason": row.reason or "",
        "evidence": row.evidence or [],
        "holding_days": int(row.holding_days or 3),
        "entry_low": row.entry_low,
        "entry_high": row.entry_high,
        "stop_loss": row.stop_loss,
        "target_price": row.target_price,
        "invalidation": row.invalidation or "",
        "plan_quality": int(row.plan_quality or 0),
        "source_agent": row.source_agent or "",
        "source_suggestion_id": row.source_suggestion_id,
        "source_candidate_id": row.source_candidate_id,
        "trace_id": row.trace_id or "",
        "is_holding_snapshot": bool(row.is_holding_snapshot),
        "context_quality_score": row.context_quality_score,
        "score_breakdown": score_breakdown,
        "market_regime": market_regime,
        "cross_feature": cross_feature,
        "news_metric": news_metric,
        "constrained": constrained,
        "constraint_reasons": [str(x) for x in constraint_reasons if str(x).strip()],
        "payload": payload if include_payload else {},
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


def refresh_strategy_signals(
    *,
    snapshot_date: str = "",
    rebuild_candidates: bool = False,
    max_inputs: int = 500,
    market_scan_limit: int = 80,
    max_kline_symbols: int = 72,
    limit_candidates: int = 2000,
) -> dict:
    ensure_strategy_catalog()
    if rebuild_candidates:
        refresh_entry_candidates(
            max_inputs=max_inputs,
            snapshot_date=snapshot_date or None,
            market_scan_limit=market_scan_limit,
            max_kline_symbols=max_kline_symbols,
        )

    db = SessionLocal()
    try:
        snapshot = (snapshot_date or "").strip()
        if not snapshot:
            latest = (
                db.query(EntryCandidate.snapshot_date)
                .order_by(EntryCandidate.snapshot_date.desc())
                .first()
            )
            snapshot = latest[0] if latest else ""
        if not snapshot:
            return {"snapshot_date": "", "count": 0, "items": []}

        candidates = (
            db.query(EntryCandidate)
            .filter(EntryCandidate.snapshot_date == snapshot)
            .order_by(EntryCandidate.score.desc(), EntryCandidate.updated_at.desc())
            .limit(max(20, int(limit_candidates)))
            .all()
        )
        if not candidates:
            return {"snapshot_date": snapshot, "count": 0, "items": []}

        profile_map = get_strategy_profile_map()
        regime_rows = _upsert_market_regime_snapshots(
            db=db,
            snapshot=snapshot,
            candidates=candidates,
        )
        cross_features = _build_cross_section_features(candidates)
        news_metrics = _load_news_metrics(
            db=db,
            candidates=candidates,
            lookback_hours=72,
            max_rows=5000,
        )
        existing_rows = (
            db.query(StrategySignalRun)
            .filter(StrategySignalRun.snapshot_date == snapshot)
            .all()
        )
        existing: dict[tuple[int, str], StrategySignalRun] = {}
        for row in existing_rows:
            cand_id = row.source_candidate_id
            code = row.strategy_code
            if cand_id is None:
                continue
            existing[(int(cand_id), str(code or ""))] = row

        weight_cache: dict[str, dict[str, float]] = {}
        factor_weight_cache: dict[str, dict[str, float]] = {}
        touched_keys: set[tuple[int, str]] = set()
        touched_rows: list[StrategySignalRun] = []

        for c in candidates:
            market = (c.stock_market or "CN").strip().upper() or "CN"
            weights = weight_cache.get(market)
            if weights is None:
                weights = get_effective_weight_map(market=market, regime="default")
                weight_cache[market] = weights
            factor_weights = factor_weight_cache.get(market)
            if factor_weights is None:
                factor_weights = get_factor_weights(market, db=db)
                factor_weight_cache[market] = factor_weights
            codes = _strategy_codes_for_candidate(c)
            for code in codes:
                profile = profile_map.get(code) or profile_map.get("watchlist_agent") or {}
                weight = float(weights.get(code, profile.get("default_weight", 1.0)))
                risk_level = (profile.get("risk_level") or "medium").strip() or "medium"
                strategy_name = profile.get("name") or code
                strategy_version = profile.get("version") or "v1"
                horizon_days = 3
                params = profile.get("params") or {}
                if isinstance(params, dict):
                    horizon_days = max(1, int(params.get("horizon_days", 3) or 3))

                regime_info = regime_rows.get(market) or {
                    "regime": "neutral",
                    "confidence": 0.0,
                }
                symbol_key = (c.stock_symbol or "").strip().upper()
                normalized_news_metric = _normalize_news_metric(news_metrics.get(symbol_key))
                score_breakdown = _compute_factor_breakdown(
                    row=c,
                    strategy_code=code,
                    weight=weight,
                    risk_level=risk_level,
                    regime_info=regime_info,
                    cross_feature=cross_features.get(int(c.id)) if c.id is not None else None,
                    news_metric=normalized_news_metric,
                    factor_weights=factor_weights,
                )
                rank_score = float(score_breakdown.get("weighted_score") or 0.0)
                confidence = c.confidence if c.confidence is not None else round(rank_score / 100.0, 3)
                cmeta = c.meta if isinstance(c.meta, dict) else {}
                source_meta = cmeta.get("source_meta") if isinstance(cmeta.get("source_meta"), dict) else {}
                context_quality_score = _safe_float(source_meta.get("context_quality_score"))
                compact_source_meta = _compact_source_meta(source_meta)
                action = (c.action or "watch").strip().lower() or "watch"
                action_label = (c.action_label or "观望").strip() or "观望"
                if bool(c.is_holding_snapshot):
                    if action == "buy":
                        action = "add"
                        action_label = "准备加仓"
                else:
                    if action == "add":
                        action = "buy"
                        action_label = "建仓"
                    elif action == "hold":
                        action_label = "观望"
                payload = {
                    "entry_candidate_id": c.id,
                    "entry_candidate_snapshot": c.snapshot_date,
                    "strategy_tags": c.strategy_tags or [],
                    "strategy_weight": weight,
                    "source_meta": compact_source_meta,
                    "score_breakdown": score_breakdown,
                    "market_regime": {
                        "regime": regime_info.get("regime") or "neutral",
                        "regime_label": regime_info.get("regime_label") or _regime_label(regime_info.get("regime") or "neutral"),
                        "confidence": regime_info.get("confidence") or 0.0,
                        "regime_score": regime_info.get("regime_score") or 0.0,
                    },
                    "cross_feature": cross_features.get(int(c.id)) if c.id is not None else {},
                    "news_metric": normalized_news_metric,
                }
                key = (int(c.id), str(code))
                row = existing.get(key)
                if not row:
                    row = StrategySignalRun(
                        snapshot_date=snapshot,
                        stock_symbol=c.stock_symbol,
                        stock_market=market,
                        stock_name=c.stock_name or c.stock_symbol,
                        strategy_code=code,
                        source_candidate_id=c.id,
                    )
                    db.add(row)
                    existing[key] = row

                row.strategy_name = strategy_name
                row.strategy_version = strategy_version
                row.risk_level = risk_level
                row.source_pool = c.candidate_source or "watchlist"
                row.score = float(c.score or 0)
                row.rank_score = float(rank_score)
                row.confidence = float(confidence or 0)
                row.status = c.status or "inactive"
                row.action = action
                row.action_label = action_label
                row.signal = c.signal or ""
                row.reason = c.reason or ""
                row.evidence = to_jsonable(c.evidence or [])
                row.holding_days = int(horizon_days)
                row.entry_low = c.entry_low
                row.entry_high = c.entry_high
                row.stop_loss = c.stop_loss
                row.target_price = c.target_price
                row.invalidation = c.invalidation or ""
                row.plan_quality = int(c.plan_quality or 0)
                row.source_agent = c.source_agent or ""
                row.source_suggestion_id = c.source_suggestion_id
                row.trace_id = c.source_trace_id or ""
                row.is_holding_snapshot = bool(c.is_holding_snapshot)
                row.context_quality_score = context_quality_score
                row.payload = to_jsonable(payload)
                row.updated_at = utc_now()
                touched_keys.add(key)
                touched_rows.append(row)

        constraint_stats = _apply_portfolio_constraints(rows=touched_rows)
        if constraint_stats.get("demoted", 0) > 0:
            logger.info(
                "[策略层] 组合约束生效: snapshot=%s demoted=%s details=%s",
                snapshot,
                constraint_stats.get("demoted", 0),
                constraint_stats.get("by_reason", {}),
            )

        # Remove stale strategy rows for same candidate snapshot when strategy mapping changed.
        stale_ids = [
            int(row.id)
            for key, row in existing.items()
            if row.id is not None and key not in touched_keys
        ]
        if stale_ids:
            db.query(StrategySignalRun).filter(
                StrategySignalRun.id.in_(stale_ids)
            ).delete(synchronize_session=False)

        db.commit()

        rows = (
            db.query(StrategySignalRun)
            .filter(StrategySignalRun.snapshot_date == snapshot)
            .order_by(StrategySignalRun.rank_score.desc(), StrategySignalRun.updated_at.desc())
            .all()
        )
        _sync_factor_and_risk_snapshots(
            db=db,
            snapshot=snapshot,
            signals=rows,
        )
        db.commit()
        factor_map: dict[int, StrategyFactorSnapshot] = {}
        run_ids = [int(x.id) for x in rows if x.id is not None]
        if run_ids:
            factors = (
                db.query(StrategyFactorSnapshot)
                .filter(
                    StrategyFactorSnapshot.snapshot_date == snapshot,
                    StrategyFactorSnapshot.signal_run_id.in_(run_ids),
                )
                .all()
            )
            factor_map = {int(f.signal_run_id): f for f in factors if f.signal_run_id is not None}
        items = [
            _format_signal(
                x,
                include_payload=False,
                factor_snapshot=factor_map.get(int(x.id)) if (x.id is not None) else None,
            )
            for x in rows[:3000]
        ]
        return {
            "snapshot_date": snapshot,
            "count": len(rows),
            "items": items,
            "constraints": constraint_stats,
        }
    finally:
        db.close()


def list_strategy_signals(
    *,
    market: str = "",
    status: str = "all",
    min_score: float = 0,
    limit: int = 50,
    snapshot_date: str = "",
    source_pool: str = "",
    holding: str = "",
    strategy_code: str = "",
    risk_level: str = "",
    include_payload: bool = False,
) -> dict:
    ensure_strategy_catalog()
    db = SessionLocal()
    try:
        snapshot = (snapshot_date or "").strip()
        if not snapshot:
            latest = (
                db.query(StrategySignalRun.snapshot_date)
                .order_by(StrategySignalRun.snapshot_date.desc())
                .first()
            )
            snapshot = latest[0] if latest else ""
        if not snapshot:
            return {"snapshot_date": "", "count": 0, "items": []}

        q = db.query(StrategySignalRun).filter(StrategySignalRun.snapshot_date == snapshot)
        mkt = (market or "").strip().upper()
        if mkt:
            q = q.filter(StrategySignalRun.stock_market == mkt)
        st = (status or "").strip().lower()
        if st and st != "all":
            q = q.filter(StrategySignalRun.status == st)
        src = (source_pool or "").strip().lower()
        if src and src != "all":
            if src == "market_scan":
                q = q.filter(StrategySignalRun.source_pool.in_(("market_scan", "mixed")))
            elif src == "watchlist":
                q = q.filter(StrategySignalRun.source_pool == "watchlist")
            else:
                q = q.filter(StrategySignalRun.source_pool == src)
        h = (holding or "").strip().lower()
        if h == "held":
            q = q.filter(StrategySignalRun.is_holding_snapshot.is_(True))
        elif h == "unheld":
            q = q.filter(StrategySignalRun.is_holding_snapshot.is_(False))
        scode = (strategy_code or "").strip()
        if scode:
            q = q.filter(StrategySignalRun.strategy_code == scode)
        r = (risk_level or "").strip().lower()
        if r and r != "all":
            q = q.filter(StrategySignalRun.risk_level == r)

        q = q.filter(StrategySignalRun.rank_score >= float(min_score or 0))
        rows = (
            q.order_by(
                case(
                    (StrategySignalRun.source_pool == "market_scan", 0),
                    (StrategySignalRun.source_pool == "mixed", 1),
                    else_=2,
                ),
                StrategySignalRun.rank_score.desc(),
                StrategySignalRun.updated_at.desc(),
            )
            .limit(max(1, int(limit)))
            .all()
        )
        factor_map: dict[int, StrategyFactorSnapshot] = {}
        if rows and not include_payload:
            ids = [int(x.id) for x in rows if x.id is not None]
            if ids:
                factors = (
                    db.query(StrategyFactorSnapshot)
                    .filter(
                        StrategyFactorSnapshot.snapshot_date == snapshot,
                        StrategyFactorSnapshot.signal_run_id.in_(ids),
                    )
                    .all()
                )
                factor_map = {int(f.signal_run_id): f for f in factors if f.signal_run_id is not None}
        items = [
            _format_signal(
                x,
                include_payload=include_payload,
                factor_snapshot=factor_map.get(int(x.id)) if (x.id is not None) else None,
            )
            for x in rows
        ]
        return {"snapshot_date": snapshot, "count": len(items), "items": items}
    finally:
        db.close()


def evaluate_strategy_outcomes(
    *,
    horizons: tuple[int, ...] = (1, 3, 5, 10),
    snapshot_days: int = 60,
    limit: int = 800,
) -> dict:
    stats = {
        "total_signals": 0,
        "eligible": 0,
        "evaluated": 0,
        "skipped_not_due": 0,
        "skipped_no_price": 0,
        "skipped_no_base_price": 0,
    }
    safe_horizons = sorted({max(1, int(h)) for h in horizons if int(h) > 0})
    if not safe_horizons:
        safe_horizons = [1, 3, 5]

    db = SessionLocal()
    try:
        cutoff = date.today() - timedelta(days=max(7, int(snapshot_days)))
        signals = (
            db.query(StrategySignalRun)
            .filter(
                StrategySignalRun.snapshot_date >= cutoff.strftime("%Y-%m-%d"),
                StrategySignalRun.status.in_(("active", "inactive")),
                StrategySignalRun.action.in_(("buy", "add", "hold", "watch")),
            )
            .order_by(StrategySignalRun.snapshot_date.desc(), StrategySignalRun.rank_score.desc())
            .limit(max(1, int(limit)))
            .all()
        )
        stats["total_signals"] = len(signals)
        if not signals:
            return stats

        existing_rows = (
            db.query(StrategyOutcome.signal_run_id, StrategyOutcome.horizon_days)
            .filter(StrategyOutcome.signal_run_id.in_([s.id for s in signals]))
            .all()
        )
        existing = {(int(x), int(y)) for x, y in existing_rows}

        today = date.today()
        kline_cache: dict[tuple[str, str], list] = {}
        pending = 0  # 分批提交计数,缩短写事务窗口

        for s in signals:
            snap_day = _parse_day(s.snapshot_date)
            if snap_day is None:
                continue
            key = (
                (s.stock_symbol or "").strip(),
                (s.stock_market or "CN").strip().upper(),
            )
            if key not in kline_cache:
                try:
                    lookback = max(120, (today - snap_day).days + 30)
                    kline_cache[key] = KlineCollector(_to_market(key[1])).get_klines(
                        key[0], days=min(lookback, 600)
                    )
                except Exception:
                    kline_cache[key] = []
            klines = kline_cache[key]

            for horizon in safe_horizons:
                if (s.id, horizon) in existing:
                    continue
                target_day = snap_day + timedelta(days=horizon)
                if target_day > today:
                    stats["skipped_not_due"] += 1
                    continue
                stats["eligible"] += 1
                outcome_price = _pick_close_on_or_before(klines, target_day)
                if outcome_price is None:
                    stats["skipped_no_price"] += 1
                    continue
                base_price = None
                if s.entry_low is not None and s.entry_high is not None:
                    base_price = (float(s.entry_low) + float(s.entry_high)) / 2
                elif s.entry_high is not None:
                    base_price = float(s.entry_high)
                elif s.entry_low is not None:
                    base_price = float(s.entry_low)
                if base_price is None:
                    payload = s.payload if isinstance(s.payload, dict) else {}
                    source_meta = payload.get("source_meta") if isinstance(payload.get("source_meta"), dict) else {}
                    quote = source_meta.get("quote") if isinstance(source_meta.get("quote"), dict) else {}
                    base_price = _safe_float(quote.get("current_price"))
                if base_price is None:
                    base_price = _pick_close_on_or_before(klines, snap_day)
                if base_price is None or base_price <= 0:
                    stats["skipped_no_base_price"] += 1
                    status = "no_base_price"
                    ret = None
                else:
                    ret = (outcome_price - base_price) / base_price * 100.0
                    if s.target_price is not None and outcome_price >= float(s.target_price):
                        status = "hit_target"
                    elif s.stop_loss is not None and outcome_price <= float(s.stop_loss):
                        status = "hit_stop"
                    else:
                        status = "evaluated"
                hit_target = (
                    bool(s.target_price is not None and outcome_price >= float(s.target_price))
                    if status != "no_base_price"
                    else None
                )
                hit_stop = (
                    bool(s.stop_loss is not None and outcome_price <= float(s.stop_loss))
                    if status != "no_base_price"
                    else None
                )
                db.add(
                    StrategyOutcome(
                        signal_run_id=s.id,
                        strategy_code=s.strategy_code,
                        snapshot_date=s.snapshot_date,
                        stock_symbol=s.stock_symbol,
                        stock_market=s.stock_market,
                        source_pool=s.source_pool or "watchlist",
                        horizon_days=horizon,
                        target_date=target_day.strftime("%Y-%m-%d"),
                        base_price=base_price,
                        outcome_price=outcome_price,
                        outcome_return_pct=ret,
                        hit_target=hit_target,
                        hit_stop=hit_stop,
                        outcome_status=status,
                        meta=to_jsonable(
                            {
                                "rank_score": float(s.rank_score or 0),
                                "action": s.action or "",
                                "action_label": s.action_label or "",
                            }
                        ),
                        evaluated_at=utc_now(),
                    )
                )
                stats["evaluated"] += 1
                existing.add((s.id, horizon))
                pending += 1

            # 分批提交:累计到阈值即落盘,缩短写事务,避免与 60s 调度器并发写长时间持锁
            if pending >= 50:
                db.commit()
                pending = 0

        db.commit()
        return stats
    except Exception as e:
        db.rollback()
        logger.warning(f"策略后验评估失败: {e}")
        return stats
    finally:
        db.close()


def _aggregate_recent_outcomes(*, db, days: int):
    since = utc_now() - timedelta(days=max(1, int(days)))
    rows = (
        db.query(
            StrategyOutcome.strategy_code,
            StrategyOutcome.stock_market,
            func.count(StrategyOutcome.id).label("total"),
            func.sum(case((StrategyOutcome.outcome_return_pct > 0, 1), else_=0)).label("wins"),
            func.avg(StrategyOutcome.outcome_return_pct).label("avg_ret"),
        )
        .filter(
            StrategyOutcome.created_at >= since,
            StrategyOutcome.outcome_status.in_(("evaluated", "hit_target", "hit_stop")),
        )
        .group_by(StrategyOutcome.strategy_code, StrategyOutcome.stock_market)
        .all()
    )
    by_pair: dict[tuple[str, str], dict] = {}
    by_strategy_all: dict[str, dict] = {}
    for code, market, total, wins, avg_ret in rows:
        c = (code or "").strip()
        m = (market or "ALL").strip().upper() or "ALL"
        t = int(total or 0)
        w = int(wins or 0)
        a = float(avg_ret or 0.0)
        by_pair[(c, m)] = {"sample_size": t, "wins": w, "avg_return_pct": a}
        x = by_strategy_all.setdefault(c, {"sample_size": 0, "wins": 0, "ret_sum": 0.0})
        x["sample_size"] += t
        x["wins"] += w
        x["ret_sum"] += a * t
    for c, x in by_strategy_all.items():
        x["avg_return_pct"] = (x["ret_sum"] / x["sample_size"]) if x["sample_size"] > 0 else 0.0
    return by_pair, by_strategy_all


def rebalance_strategy_weights(
    *,
    window_days: int = 45,
    min_samples: int = 8,
    alpha: float = 0.35,
    regime: str = "default",
) -> dict:
    ensure_strategy_catalog()
    reg = (regime or "default").strip() or "default"
    alpha = _clamp(float(alpha or 0.35), 0.05, 0.95)
    window_days = max(7, min(int(window_days or 45), 365))
    min_samples = max(3, min(int(min_samples or 8), 200))

    db = SessionLocal()
    try:
        catalogs = list_strategy_catalog(enabled_only=True)
        by_pair, by_all = _aggregate_recent_outcomes(db=db, days=window_days)

        changed = 0
        checked = 0
        skipped_low_sample = 0
        rows_changed: list[dict] = []

        targets: list[tuple[str, str, dict]] = []
        for c in catalogs:
            code = c["code"]
            default_weight = float(c.get("default_weight", 1.0))
            all_metrics = by_all.get(code, {"sample_size": 0, "wins": 0, "avg_return_pct": 0.0})
            targets.append((code, "ALL", {"default_weight": default_weight, **all_metrics}))
            for market in ("CN", "HK", "US"):
                metrics = by_pair.get((code, market), {"sample_size": 0, "wins": 0, "avg_return_pct": 0.0})
                targets.append((code, market, {"default_weight": default_weight, **metrics}))

        for code, market, metrics in targets:
            checked += 1
            sample_size = int(metrics.get("sample_size", 0))
            wins = int(metrics.get("wins", 0))
            avg_ret = float(metrics.get("avg_return_pct", 0.0))
            default_weight = float(metrics.get("default_weight", 1.0))

            row = (
                db.query(StrategyWeight)
                .filter(
                    StrategyWeight.strategy_code == code,
                    StrategyWeight.market == market,
                    StrategyWeight.regime == reg,
                )
                .first()
            )
            old_weight = float(row.weight if row else default_weight)
            if sample_size < min_samples:
                skipped_low_sample += 1
                continue

            win_rate = (wins / sample_size * 100.0) if sample_size > 0 else 0.0
            win_term = _clamp((win_rate - 50.0) / 50.0, -1.0, 1.0)
            ret_term = _clamp(avg_ret / 8.0, -1.0, 1.0)
            target = default_weight * (1.0 + 0.45 * win_term + 0.35 * ret_term)
            target = _clamp(target, 0.45, 1.90)
            new_weight = old_weight * (1.0 - alpha) + target * alpha
            new_weight = float(round(_clamp(new_weight, 0.45, 1.90), 4))

            if abs(new_weight - old_weight) < 0.01:
                continue

            reason = (
                f"auto_rebalance(win_rate={win_rate:.1f}%, avg_ret={avg_ret:.2f}%, "
                f"samples={sample_size}, alpha={alpha:.2f})"
            )
            if not row:
                row = StrategyWeight(
                    strategy_code=code,
                    market=market,
                    regime=reg,
                    weight=new_weight,
                    reason=reason,
                    meta={"window_days": window_days, "sample_size": sample_size},
                    effective_from=utc_now(),
                )
                db.add(row)
            else:
                row.weight = new_weight
                row.reason = reason
                row.meta = {"window_days": window_days, "sample_size": sample_size}
                row.effective_from = utc_now()
                row.updated_at = utc_now()

            db.add(
                StrategyWeightHistory(
                    strategy_code=code,
                    market=market,
                    regime=reg,
                    old_weight=float(old_weight),
                    new_weight=float(new_weight),
                    reason=reason,
                    window_days=window_days,
                    sample_size=sample_size,
                    meta={
                        "wins": wins,
                        "win_rate": round(win_rate, 3),
                        "avg_return_pct": round(avg_ret, 4),
                        "target": round(target, 4),
                    },
                )
            )
            changed += 1
            rows_changed.append(
                {
                    "strategy_code": code,
                    "market": market,
                    "old_weight": round(old_weight, 4),
                    "new_weight": round(new_weight, 4),
                    "sample_size": sample_size,
                }
            )

        db.commit()
        return {
            "window_days": window_days,
            "min_samples": min_samples,
            "alpha": alpha,
            "checked": checked,
            "changed": changed,
            "skipped_low_sample": skipped_low_sample,
            "changes": rows_changed,
        }
    except Exception as e:
        db.rollback()
        logger.warning(f"策略调权失败: {e}")
        return {
            "window_days": window_days,
            "min_samples": min_samples,
            "alpha": alpha,
            "checked": 0,
            "changed": 0,
            "skipped_low_sample": 0,
            "changes": [],
            "error": str(e),
        }
    finally:
        db.close()


def get_strategy_stats(*, days: int = 45) -> dict:
    ensure_strategy_catalog()
    days = max(1, min(int(days or 45), 365))
    since = utc_now() - timedelta(days=days)
    db = SessionLocal()
    try:
        latest_snapshot_row = (
            db.query(StrategySignalRun.snapshot_date)
            .order_by(StrategySignalRun.snapshot_date.desc())
            .first()
        )
        snapshot = latest_snapshot_row[0] if latest_snapshot_row else ""
        coverage = {
            "snapshot_date": snapshot,
            "total_signals": 0,
            "active_signals": 0,
            "watchlist_signals": 0,
            "market_scan_signals": 0,
            "mixed_signals": 0,
        }
        if snapshot:
            total_signals = (
                db.query(func.count(StrategySignalRun.id))
                .filter(StrategySignalRun.snapshot_date == snapshot)
                .scalar()
            ) or 0
            active_signals = (
                db.query(func.count(StrategySignalRun.id))
                .filter(
                    StrategySignalRun.snapshot_date == snapshot,
                    StrategySignalRun.status == "active",
                )
                .scalar()
            ) or 0
            market_scan = (
                db.query(func.count(StrategySignalRun.id))
                .filter(
                    StrategySignalRun.snapshot_date == snapshot,
                    StrategySignalRun.source_pool.in_(("market_scan", "mixed")),
                )
                .scalar()
            ) or 0
            mixed = (
                db.query(func.count(StrategySignalRun.id))
                .filter(
                    StrategySignalRun.snapshot_date == snapshot,
                    StrategySignalRun.source_pool == "mixed",
                )
                .scalar()
            ) or 0
            watchlist = max(0, int(total_signals) - int(market_scan))
            coverage = {
                "snapshot_date": snapshot,
                "total_signals": int(total_signals),
                "active_signals": int(active_signals),
                "watchlist_signals": int(watchlist),
                "market_scan_signals": int(market_scan),
                "mixed_signals": int(mixed),
                "market_scan_share_pct": round((market_scan / total_signals * 100.0), 2)
                if total_signals
                else 0.0,
            }

        outcome_rows = (
            db.query(
                StrategyOutcome.strategy_code,
                StrategyOutcome.stock_market,
                StrategyOutcome.horizon_days,
                func.count(StrategyOutcome.id).label("total"),
                func.sum(case((StrategyOutcome.outcome_return_pct > 0, 1), else_=0)).label("wins"),
                func.avg(StrategyOutcome.outcome_return_pct).label("avg_ret"),
            )
            .filter(
                StrategyOutcome.created_at >= since,
                StrategyOutcome.outcome_status.in_(("evaluated", "hit_target", "hit_stop")),
            )
            .group_by(
                StrategyOutcome.strategy_code,
                StrategyOutcome.stock_market,
                StrategyOutcome.horizon_days,
            )
            .all()
        )
        profiles = get_strategy_profile_map()
        weights = (
            db.query(StrategyWeight)
            .filter(StrategyWeight.regime == "default")
            .all()
        )
        weight_map = {
            (w.strategy_code, (w.market or "ALL").upper()): float(w.weight or 1.0)
            for w in weights
        }

        by_strategy: list[dict] = []
        for code, market, horizon, total, wins, avg_ret in outcome_rows:
            c = (code or "").strip()
            m = (market or "ALL").strip().upper()
            t = int(total or 0)
            w = int(wins or 0)
            win_rate = (w / t * 100.0) if t else 0.0
            prof = profiles.get(c) or {}
            default_weight = float(prof.get("default_weight", 1.0))
            current_weight = weight_map.get((c, m), weight_map.get((c, "ALL"), default_weight))
            by_strategy.append(
                {
                    "strategy_code": c,
                    "strategy_name": prof.get("name") or c,
                    "strategy_version": prof.get("version") or "v1",
                    "market": m,
                    "risk_level": prof.get("risk_level") or "medium",
                    "risk_level_label": _risk_label(prof.get("risk_level") or "medium"),
                    "horizon_days": int(horizon or 0),
                    "sample_size": t,
                    "wins": w,
                    "win_rate": round(win_rate, 2),
                    "avg_return_pct": round(float(avg_ret or 0.0), 4),
                    "default_weight": round(default_weight, 4),
                    "current_weight": round(float(current_weight), 4),
                }
            )
        by_strategy.sort(key=lambda x: (x["sample_size"], x["win_rate"], x["avg_return_pct"]), reverse=True)

        by_market_rows = (
            db.query(
                StrategyOutcome.stock_market,
                func.count(StrategyOutcome.id).label("total"),
                func.sum(case((StrategyOutcome.outcome_return_pct > 0, 1), else_=0)).label("wins"),
                func.avg(StrategyOutcome.outcome_return_pct).label("avg_ret"),
            )
            .filter(
                StrategyOutcome.created_at >= since,
                StrategyOutcome.outcome_status.in_(("evaluated", "hit_target", "hit_stop")),
            )
            .group_by(StrategyOutcome.stock_market)
            .all()
        )
        by_market = []
        for market, total, wins, avg_ret in by_market_rows:
            t = int(total or 0)
            w = int(wins or 0)
            by_market.append(
                {
                    "market": (market or "CN").strip().upper(),
                    "total": t,
                    "wins": w,
                    "win_rate": round((w / t * 100.0), 2) if t else 0.0,
                    "avg_return_pct": round(float(avg_ret or 0.0), 4),
                }
            )

        updates = (
            db.query(func.count(StrategyWeightHistory.id))
            .filter(StrategyWeightHistory.created_at >= since)
            .scalar()
        ) or 0

        top_signals = []
        regime_items: list[dict] = []
        risk_items: list[dict] = []
        factor_stats = {
            "avg_alpha_score": 0.0,
            "avg_catalyst_score": 0.0,
            "avg_quality_score": 0.0,
            "avg_risk_penalty": 0.0,
            "avg_crowd_penalty": 0.0,
            "sample_size": 0,
        }
        constrained_count = 0
        if snapshot:
            rows = (
                db.query(StrategySignalRun)
                .filter(StrategySignalRun.snapshot_date == snapshot)
                .order_by(StrategySignalRun.rank_score.desc(), StrategySignalRun.updated_at.desc())
                .limit(20)
                .all()
            )
            top_signals = [_format_signal(x) for x in rows]
            constrained_count = sum(
                1
                for x in rows
                if isinstance(x.payload, dict) and bool(x.payload.get("constrained"))
            )
            factor_rows = (
                db.query(
                    func.avg(StrategyFactorSnapshot.alpha_score).label("alpha"),
                    func.avg(StrategyFactorSnapshot.catalyst_score).label("catalyst"),
                    func.avg(StrategyFactorSnapshot.quality_score).label("quality"),
                    func.avg(StrategyFactorSnapshot.risk_penalty).label("risk"),
                    func.avg(StrategyFactorSnapshot.crowd_penalty).label("crowd"),
                    func.count(StrategyFactorSnapshot.id).label("cnt"),
                )
                .filter(StrategyFactorSnapshot.snapshot_date == snapshot)
                .first()
            )
            if factor_rows:
                factor_stats = {
                    "avg_alpha_score": round(float(factor_rows.alpha or 0.0), 4),
                    "avg_catalyst_score": round(float(factor_rows.catalyst or 0.0), 4),
                    "avg_quality_score": round(float(factor_rows.quality or 0.0), 4),
                    "avg_risk_penalty": round(float(factor_rows.risk or 0.0), 4),
                    "avg_crowd_penalty": round(float(factor_rows.crowd or 0.0), 4),
                    "sample_size": int(factor_rows.cnt or 0),
                }
            regimes = (
                db.query(MarketRegimeSnapshot)
                .filter(MarketRegimeSnapshot.snapshot_date == snapshot)
                .order_by(MarketRegimeSnapshot.market.asc())
                .all()
            )
            for r in regimes:
                regime_items.append(
                    {
                        "snapshot_date": r.snapshot_date,
                        "market": r.market,
                        "regime": r.regime,
                        "regime_label": _regime_label(r.regime or "neutral"),
                        "regime_score": round(float(r.regime_score or 0.0), 4),
                        "confidence": round(float(r.confidence or 0.0), 4),
                        "breadth_up_pct": r.breadth_up_pct,
                        "avg_change_pct": r.avg_change_pct,
                        "volatility_pct": r.volatility_pct,
                        "active_ratio": r.active_ratio,
                        "sample_size": int(r.sample_size or 0),
                        "meta": r.meta or {},
                    }
                )
            risks = (
                db.query(PortfolioRiskSnapshot)
                .filter(PortfolioRiskSnapshot.snapshot_date == snapshot)
                .order_by(PortfolioRiskSnapshot.market.asc())
                .all()
            )
            for r in risks:
                risk_items.append(
                    {
                        "snapshot_date": r.snapshot_date,
                        "market": r.market,
                        "total_signals": int(r.total_signals or 0),
                        "active_signals": int(r.active_signals or 0),
                        "held_signals": int(r.held_signals or 0),
                        "unheld_signals": int(r.unheld_signals or 0),
                        "high_risk_ratio": r.high_risk_ratio,
                        "concentration_top5": r.concentration_top5,
                        "avg_rank_score": r.avg_rank_score,
                        "risk_level": r.risk_level or "medium",
                        "meta": r.meta or {},
                    }
                )

        return {
            "window_days": days,
            "coverage": coverage,
            "constraints": {
                "constrained_top20": int(constrained_count),
            },
            "factor_stats": factor_stats,
            "regimes": regime_items,
            "portfolio_risk": risk_items,
            "by_strategy": by_strategy[:300],
            "by_market": by_market,
            "weight_updates": {
                "window_days": days,
                "changed": int(updates),
            },
            "top_signals": top_signals,
        }
    finally:
        db.close()


def list_market_regime_snapshots(
    *,
    snapshot_date: str = "",
    market: str = "",
    limit: int = 100,
) -> dict:
    db = SessionLocal()
    try:
        q = db.query(MarketRegimeSnapshot)
        snap = (snapshot_date or "").strip()
        if snap:
            q = q.filter(MarketRegimeSnapshot.snapshot_date == snap)
        mkt = (market or "").strip().upper()
        if mkt:
            q = q.filter(MarketRegimeSnapshot.market == mkt)
        rows = (
            q.order_by(
                MarketRegimeSnapshot.snapshot_date.desc(),
                MarketRegimeSnapshot.market.asc(),
            )
            .limit(max(1, min(int(limit), 1000)))
            .all()
        )
        items = []
        for r in rows:
            items.append(
                {
                    "id": r.id,
                    "snapshot_date": r.snapshot_date,
                    "market": r.market,
                    "regime": r.regime or "neutral",
                    "regime_label": _regime_label(r.regime or "neutral"),
                    "regime_score": round(float(r.regime_score or 0.0), 4),
                    "confidence": round(float(r.confidence or 0.0), 4),
                    "breadth_up_pct": r.breadth_up_pct,
                    "avg_change_pct": r.avg_change_pct,
                    "volatility_pct": r.volatility_pct,
                    "active_ratio": r.active_ratio,
                    "sample_size": int(r.sample_size or 0),
                    "meta": r.meta or {},
                    "created_at": _iso(r.created_at),
                    "updated_at": _iso(r.updated_at),
                }
            )
        return {"count": len(items), "items": items}
    finally:
        db.close()


def list_portfolio_risk_snapshots(
    *,
    snapshot_date: str = "",
    market: str = "",
    limit: int = 100,
) -> dict:
    db = SessionLocal()
    try:
        q = db.query(PortfolioRiskSnapshot)
        snap = (snapshot_date or "").strip()
        if snap:
            q = q.filter(PortfolioRiskSnapshot.snapshot_date == snap)
        mkt = (market or "").strip().upper()
        if mkt:
            q = q.filter(PortfolioRiskSnapshot.market == mkt)
        rows = (
            q.order_by(
                PortfolioRiskSnapshot.snapshot_date.desc(),
                PortfolioRiskSnapshot.market.asc(),
            )
            .limit(max(1, min(int(limit), 1000)))
            .all()
        )
        items = []
        for r in rows:
            items.append(
                {
                    "id": r.id,
                    "snapshot_date": r.snapshot_date,
                    "market": r.market,
                    "total_signals": int(r.total_signals or 0),
                    "active_signals": int(r.active_signals or 0),
                    "held_signals": int(r.held_signals or 0),
                    "unheld_signals": int(r.unheld_signals or 0),
                    "high_risk_ratio": r.high_risk_ratio,
                    "concentration_top5": r.concentration_top5,
                    "avg_rank_score": r.avg_rank_score,
                    "risk_level": r.risk_level or "medium",
                    "meta": r.meta or {},
                    "created_at": _iso(r.created_at),
                    "updated_at": _iso(r.updated_at),
                }
            )
        return {"count": len(items), "items": items}
    finally:
        db.close()


def get_strategy_factor_snapshot(signal_run_id: int) -> dict:
    db = SessionLocal()
    try:
        row = (
            db.query(StrategyFactorSnapshot)
            .filter(StrategyFactorSnapshot.signal_run_id == int(signal_run_id))
            .first()
        )
        if not row:
            return {}
        return {
            "id": row.id,
            "signal_run_id": int(row.signal_run_id),
            "snapshot_date": row.snapshot_date,
            "stock_symbol": row.stock_symbol,
            "stock_market": row.stock_market,
            "strategy_code": row.strategy_code,
            "alpha_score": float(row.alpha_score or 0.0),
            "catalyst_score": float(row.catalyst_score or 0.0),
            "quality_score": float(row.quality_score or 0.0),
            "risk_penalty": float(row.risk_penalty or 0.0),
            "crowd_penalty": float(row.crowd_penalty or 0.0),
            "source_bonus": float(row.source_bonus or 0.0),
            "regime_multiplier": float(row.regime_multiplier or 1.0),
            "final_score": float(row.final_score or 0.0),
            "factor_payload": row.factor_payload or {},
            "created_at": _iso(row.created_at),
            "updated_at": _iso(row.updated_at),
        }
    finally:
        db.close()


def list_strategy_weight_history(
    *,
    strategy_code: str = "",
    market: str = "",
    limit: int = 200,
) -> dict:
    db = SessionLocal()
    try:
        q = db.query(StrategyWeightHistory)
        code = (strategy_code or "").strip()
        if code:
            q = q.filter(StrategyWeightHistory.strategy_code == code)
        mkt = (market or "").strip().upper()
        if mkt:
            q = q.filter(StrategyWeightHistory.market == mkt)
        rows = (
            q.order_by(StrategyWeightHistory.created_at.desc())
            .limit(max(1, min(int(limit), 2000)))
            .all()
        )
        items = []
        for r in rows:
            items.append(
                {
                    "id": r.id,
                    "strategy_code": r.strategy_code,
                    "market": r.market,
                    "regime": r.regime,
                    "old_weight": float(r.old_weight or 1.0),
                    "new_weight": float(r.new_weight or 1.0),
                    "reason": r.reason or "",
                    "window_days": int(r.window_days or 0),
                    "sample_size": int(r.sample_size or 0),
                    "meta": r.meta or {},
                    "created_at": _iso(r.created_at),
                }
            )
        return {"count": len(items), "items": items}
    finally:
        db.close()
