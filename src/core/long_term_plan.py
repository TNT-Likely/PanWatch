"""长线投资计划：配置归一化与加仓档位评估。"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

PORTFOLIO_ROLES = frozenset({"core", "satellite", "watch"})
DEFAULT_ADD_LEVELS = [
    {"drawdown_pct": -5, "budget_pct": 20},
    {"drawdown_pct": -10, "budget_pct": 30},
    {"drawdown_pct": -15, "budget_pct": 50},
]

DEFAULT_INVESTMENT_PROFILE: dict[str, Any] = {
    "long_term_enabled": False,
    "portfolio_role": "watch",
    "target_weight_pct": None,
    "max_weight_pct": None,
    "add_plan": {
        "basis": "avg_cost",
        "levels": deepcopy(DEFAULT_ADD_LEVELS),
    },
    "reduce_plan": {
        "take_profit_pct": 15,
        "scope": "satellite_only",
    },
    "thesis": "",
    "thesis_invalidations": [],
}


def _coerce_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def normalize_investment_profile(raw: dict | None) -> dict[str, Any]:
    """合并默认值并校验长线计划字段。"""
    base = deepcopy(DEFAULT_INVESTMENT_PROFILE)
    if not isinstance(raw, dict):
        return base

    base["long_term_enabled"] = _coerce_bool(raw.get("long_term_enabled"))
    role = str(raw.get("portfolio_role") or "watch").strip().lower()
    base["portfolio_role"] = role if role in PORTFOLIO_ROLES else "watch"

    target = _coerce_float(raw.get("target_weight_pct"))
    max_w = _coerce_float(raw.get("max_weight_pct"))
    if target is not None:
        base["target_weight_pct"] = max(0.0, min(target, 100.0))
    if max_w is not None:
        base["max_weight_pct"] = max(0.0, min(max_w, 100.0))

    add_plan = raw.get("add_plan") if isinstance(raw.get("add_plan"), dict) else {}
    basis = str(add_plan.get("basis") or "avg_cost").strip().lower()
    base["add_plan"]["basis"] = basis if basis in {"avg_cost"} else "avg_cost"
    levels = add_plan.get("levels")
    normalized_levels: list[dict[str, float]] = []
    if isinstance(levels, list):
        for item in levels:
            if not isinstance(item, dict):
                continue
            dd = _coerce_float(item.get("drawdown_pct"))
            bp = _coerce_float(item.get("budget_pct"))
            if dd is None or bp is None:
                continue
            normalized_levels.append(
                {
                    "drawdown_pct": max(-90.0, min(dd, 0.0)),
                    "budget_pct": max(0.0, min(bp, 100.0)),
                }
            )
    if normalized_levels:
        normalized_levels.sort(key=lambda x: x["drawdown_pct"], reverse=True)
        base["add_plan"]["levels"] = normalized_levels

    reduce_plan = raw.get("reduce_plan") if isinstance(raw.get("reduce_plan"), dict) else {}
    tp = _coerce_float(reduce_plan.get("take_profit_pct"))
    if tp is not None:
        base["reduce_plan"]["take_profit_pct"] = max(0.0, tp)
    scope = str(reduce_plan.get("scope") or "satellite_only").strip().lower()
    base["reduce_plan"]["scope"] = (
        scope if scope in {"satellite_only", "all"} else "satellite_only"
    )

    base["thesis"] = str(raw.get("thesis") or "").strip()[:2000]
    inv = raw.get("thesis_invalidations")
    if isinstance(inv, list):
        base["thesis_invalidations"] = [
            str(x).strip()[:200] for x in inv if str(x).strip()
        ][:10]

    if base["long_term_enabled"] and base["max_weight_pct"] is None:
        # 启用长线但未设上限时，计划不可执行加仓
        pass

    return base


def portfolio_role_label(role: str) -> str:
    return {"core": "核心仓", "satellite": "卫星仓", "watch": "观察"}.get(role, "观察")


def format_profile_summary(profile: dict | None) -> str:
    """生成供 Agent 阅读的紧凑摘要。"""
    p = normalize_investment_profile(profile)
    if not p.get("long_term_enabled"):
        return "未启用长线计划"

    parts = [
        f"角色={portfolio_role_label(p.get('portfolio_role', 'watch'))}",
    ]
    if p.get("target_weight_pct") is not None:
        parts.append(f"目标仓位={p['target_weight_pct']:.1f}%")
    if p.get("max_weight_pct") is not None:
        parts.append(f"最大仓位={p['max_weight_pct']:.1f}%")
    if p.get("thesis"):
        parts.append(f"逻辑={p['thesis'][:120]}")
    levels = (p.get("add_plan") or {}).get("levels") or []
    if levels:
        lvl_text = " / ".join(
            f"{lv['drawdown_pct']:+.0f}%→{lv['budget_pct']:.0f}%"
            for lv in levels[:4]
        )
        parts.append(f"加仓档={lvl_text}")
    inv = p.get("thesis_invalidations") or []
    if inv:
        parts.append(f"失效条件={'; '.join(inv[:3])}")
    return "；".join(parts)


def evaluate_add_plan(
    profile: dict | None,
    *,
    current_price: float | None,
    avg_cost: float | None,
    position_value: float = 0.0,
    total_assets: float = 0.0,
    available_cash: float = 0.0,
    has_buy_today: bool = False,
    market: str = "CN",
) -> dict[str, Any]:
    """评估是否触发计划内加仓，并返回建议金额与阻断原因。"""
    p = normalize_investment_profile(profile)
    blockers: list[str] = []

    if not p.get("long_term_enabled"):
        blockers.append("未启用长线计划")
    if p.get("max_weight_pct") is None:
        blockers.append("未设置最大仓位，无法执行越跌越买")
    if not current_price or current_price <= 0:
        blockers.append("缺少有效现价")
    if not avg_cost or avg_cost <= 0:
        blockers.append("缺少有效成本价")

    weight_pct = 0.0
    if total_assets > 0 and position_value >= 0:
        weight_pct = position_value / total_assets * 100.0

    max_w = float(p.get("max_weight_pct") or 0)
    if max_w > 0 and weight_pct >= max_w - 1e-6:
        blockers.append("已达最大仓位上限")

    if has_buy_today:
        blockers.append("今日已有买入")

    drawdown_pct = 0.0
    if current_price and avg_cost and avg_cost > 0:
        drawdown_pct = (current_price - avg_cost) / avg_cost * 100.0

    levels = list((p.get("add_plan") or {}).get("levels") or [])
    triggered_level = None
    next_level = None
    for lv in levels:
        if drawdown_pct <= float(lv["drawdown_pct"]) + 1e-6:
            triggered_level = lv
        elif next_level is None:
            next_level = lv

    suggested_amount = 0.0
    suggested_qty = 0
    if (
        not blockers
        and triggered_level
        and total_assets > 0
        and max_w > 0
    ):
        max_value = total_assets * max_w / 100.0
        remaining_budget = max(0.0, max_value - position_value)
        suggested_amount = remaining_budget * float(triggered_level["budget_pct"]) / 100.0
        suggested_amount = min(suggested_amount, max(0.0, available_cash))
        if current_price and suggested_amount > 0:
            raw_qty = suggested_amount / current_price
            if market.upper() == "CN":
                suggested_qty = int(raw_qty // 100) * 100
            else:
                suggested_qty = int(raw_qty)
            if suggested_qty <= 0:
                blockers.append("建议金额不足以买入最小交易单位")

    eligible = bool(triggered_level and not blockers and suggested_amount > 0)

    next_trigger_price = None
    if next_level and avg_cost and avg_cost > 0:
        next_trigger_price = round(
            avg_cost * (1 + float(next_level["drawdown_pct"]) / 100.0),
            4,
        )

    return {
        "eligible": eligible,
        "profile": p,
        "current_drawdown_pct": round(drawdown_pct, 2),
        "weight_pct": round(weight_pct, 2),
        "triggered_level": triggered_level,
        "next_level": next_level,
        "next_trigger_price": next_trigger_price,
        "suggested_amount": round(suggested_amount, 2),
        "suggested_qty": suggested_qty,
        "blockers": blockers,
        "summary": format_profile_summary(p),
    }


def apply_long_term_discipline(
    suggestion: dict,
    *,
    profile: dict | None,
    add_eval: dict | None = None,
) -> dict:
    """对 AI 建议做长线铁律后处理。"""
    p = normalize_investment_profile(profile)
    if not p.get("long_term_enabled"):
        return suggestion

    action = suggestion.get("action")
    role = p.get("portfolio_role", "watch")
    updated = dict(suggestion)

    def _downgrade(new_action: str, new_label: str, suffix: str, alert: bool = False):
        updated["action"] = new_action
        updated["action_label"] = new_label
        updated["should_alert"] = alert
        base = (suggestion.get("reason") or "").strip()
        updated["reason"] = f"{base} {suffix}".strip()[:160] if base else suffix[:160]
        return updated

    if role == "core":
        if action in {"sell", "reduce"}:
            return _downgrade(
                "hold",
                "持有",
                "核心仓按长期逻辑持有，不因短线信号轻易减仓。",
                alert=False,
            )
        if action in {"buy", "add"}:
            eval_result = add_eval or {}
            if eval_result.get("blockers"):
                blocker = eval_result["blockers"][0]
                return _downgrade(
                    "watch",
                    "观望",
                    f"计划内暂不加仓：{blocker}。",
                    alert=False,
                )
            if not eval_result.get("eligible"):
                return _downgrade(
                    "hold",
                    "持有",
                    "尚未触发计划加仓档位，维持核心仓。",
                    alert=False,
                )

    if role == "satellite" and action in {"sell", "reduce"}:
        scope = (p.get("reduce_plan") or {}).get("scope", "satellite_only")
        if scope == "satellite_only":
            sig = (updated.get("signal") or "").strip()
            if sig and "卫星" not in sig:
                updated["signal"] = f"{sig}（仅卫星仓）"[:60]

    return updated
