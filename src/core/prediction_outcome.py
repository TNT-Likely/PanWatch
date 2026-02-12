from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

from src.collectors.kline_collector import KlineCollector
from src.core.context_store import (
    list_pending_prediction_outcomes,
    mark_agent_prediction_outcome,
)
from src.models.market import MarketCode

logger = logging.getLogger(__name__)


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


def _to_market(value: str | None) -> MarketCode:
    try:
        return MarketCode((value or "CN").strip().upper())
    except Exception:
        return MarketCode.CN


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


def evaluate_pending_prediction_outcomes(
    *,
    max_horizon_days: int = 10,
    limit: int = 300,
) -> dict:
    pending = list_pending_prediction_outcomes(
        max_horizon_days=max_horizon_days,
        limit=limit,
    )
    stats = {
        "total_pending": len(pending),
        "eligible": 0,
        "evaluated": 0,
        "skipped_not_due": 0,
        "skipped_invalid_date": 0,
        "skipped_no_price": 0,
    }
    if not pending:
        return stats

    today = date.today()
    kline_cache: dict[tuple[str, str], list] = {}

    for rec in pending:
        pred_day = _parse_day(rec.prediction_date)
        if pred_day is None:
            stats["skipped_invalid_date"] += 1
            continue

        horizon = max(1, int(rec.horizon_days or 1))
        target_day = pred_day + timedelta(days=horizon)
        if target_day > today:
            stats["skipped_not_due"] += 1
            continue

        stats["eligible"] += 1
        market = _to_market(rec.stock_market)
        cache_key = (rec.stock_symbol, market.value)
        if cache_key not in kline_cache:
            lookback_days = max(120, (today - pred_day).days + 30)
            try:
                kline_cache[cache_key] = KlineCollector(market).get_klines(
                    rec.stock_symbol,
                    days=min(lookback_days, 600),
                )
            except Exception as e:
                logger.warning(
                    "评估建议获取K线失败: %s %s - %s",
                    rec.stock_symbol,
                    market.value,
                    e,
                )
                kline_cache[cache_key] = []

        klines = kline_cache[cache_key]
        outcome_price = _pick_close_on_or_before(klines, target_day)
        if outcome_price is None:
            stats["skipped_no_price"] += 1
            continue

        base_price = None
        if rec.trigger_price is not None and rec.trigger_price > 0:
            try:
                base_price = float(rec.trigger_price)
            except Exception:
                base_price = None
        if base_price is None:
            base_price = _pick_close_on_or_before(klines, pred_day)

        if base_price is None or base_price <= 0:
            ok = mark_agent_prediction_outcome(
                record_id=rec.id,
                outcome_price=outcome_price,
                outcome_return_pct=None,
                status="no_base_price",
            )
        else:
            outcome_ret = (outcome_price - base_price) / base_price * 100
            ok = mark_agent_prediction_outcome(
                record_id=rec.id,
                outcome_price=outcome_price,
                outcome_return_pct=outcome_ret,
                status="evaluated",
            )
        if ok:
            stats["evaluated"] += 1

    return stats
