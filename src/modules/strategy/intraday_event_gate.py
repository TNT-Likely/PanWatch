"""Event-driven gate for intraday monitor.

Goal: avoid calling AI on every tick; only analyze when meaningful events happen.

We persist a small per-symbol state under DATA_DIR.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from src.core.json_store import read_json, write_json_atomic


def _data_dir() -> str:
    return os.environ.get("DATA_DIR", "./data")


def _state_path() -> str:
    return os.path.join(_data_dir(), "state", "intraday_monitor_state.json")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _safe_float(v: Any) -> float | None:
    try:
        if v is None:
            return None
        return float(v)
    except Exception:
        return None


# ATR 自适应异动默认倍数:涨跌幅 >= k×ATR% 视为相对个股自身波动的异动。
DEFAULT_ATR_K = 1.5


def adaptive_price_threshold(
    atr_pct: float | None,
    fixed_threshold: float,
    k: float = DEFAULT_ATR_K,
) -> float:
    """返回自适应价格异动阈值 = max(固定阈值, k×ATR%)。

    ATR% 缺失/非正(None/0/负/异常)时退回固定阈值,保证不丢失原有行为。
    固定阈值始终作为下限(floor),避免极低波动个股阈值过松。
    """
    fixed = _safe_float(fixed_threshold) or 0.0
    ap = _safe_float(atr_pct)
    if ap is None or ap <= 0:
        return fixed
    return max(fixed, (_safe_float(k) or DEFAULT_ATR_K) * ap)


def is_abnormal_move(
    change_pct: float | None,
    atr_pct: float | None,
    k: float = DEFAULT_ATR_K,
    fixed_threshold: float = 0.0,
) -> bool:
    """判断今日涨跌幅相对个股自身波动率是否异常。

    规则:|change_pct| >= max(固定阈值, k×ATR%) 即异动。
    - atr_pct 为 None/0 时回退到 fixed_threshold(保留原有固定阈值行为)。
    - 任一入参异常一律按"非异动"返回 False(fail-soft,不阻断 agent)。
    """
    cp = _safe_float(change_pct)
    if cp is None:
        return False
    threshold = adaptive_price_threshold(atr_pct, fixed_threshold, k)
    if threshold <= 0:
        return False
    return abs(cp) >= threshold


@dataclass(frozen=True)
class EventDecision:
    should_analyze: bool
    reasons: list[str]


def _tech_sig(kline_summary: dict | None) -> dict[str, Any]:
    ks = kline_summary or {}
    return {
        "trend": ks.get("trend"),
        "macd_status": ks.get("macd_status"),
        "rsi_status": ks.get("rsi_status"),
        "kdj_status": ks.get("kdj_status"),
        "boll_status": ks.get("boll_status"),
        "kline_pattern": ks.get("kline_pattern"),
    }


def check_and_update(
    *,
    symbol: str,
    change_pct: float | None,
    volume_ratio: float | None,
    kline_summary: dict | None,
    price_threshold: float,
    volume_threshold: float,
) -> EventDecision:
    """Return whether we should analyze now, and persist latest state."""

    path = _state_path()
    state: dict[str, Any] = read_json(path, default={})
    rec: dict[str, Any] = state.get(symbol) if isinstance(state, dict) else None
    if not isinstance(rec, dict):
        rec = {}

    reasons: list[str] = []

    # 1) Price move / volume spike thresholds
    cp = _safe_float(change_pct)
    if cp is not None and abs(cp) >= float(price_threshold or 0):
        reasons.append("price_threshold")

    vr = _safe_float(volume_ratio)
    if (
        vr is not None
        and float(volume_threshold or 0) > 0
        and vr >= float(volume_threshold)
    ):
        reasons.append("volume_threshold")

    # 2) Technical state changed
    new_sig = _tech_sig(kline_summary)
    old_sig = rec.get("tech_sig") if isinstance(rec.get("tech_sig"), dict) else None
    if old_sig is not None and old_sig != new_sig:
        reasons.append("tech_state_changed")

    # Persist latest observation
    rec["last_seen_at"] = _now_iso()
    rec["change_pct"] = cp
    rec["volume_ratio"] = vr
    rec["tech_sig"] = new_sig
    state[symbol] = rec
    write_json_atomic(path, state)

    return EventDecision(should_analyze=bool(reasons), reasons=reasons)
