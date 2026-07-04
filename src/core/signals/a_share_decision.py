from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any

from src.models.market import MarketCode, StockData


class DecisionLabel(str, Enum):
    WATCH = "观察"
    BUY_CANDIDATE = "买入候选"
    HOLD = "继续持有"
    ADD_WATCH = "加仓观察"
    REDUCE_WARNING = "减仓警告"
    STOP_LOSS = "止损触发"
    NO_CHASE = "禁止追高"


@dataclass(frozen=True)
class PositionInput:
    has_position: bool = False
    avg_cost: float | None = None
    quantity: float | None = None
    stop_loss: float | None = None
    target_price: float | None = None
    max_position_ratio: float | None = None
    current_position_ratio: float | None = None
    trading_style: str = "swing"


@dataclass(frozen=True)
class DecisionInput:
    symbol: str
    name: str = ""
    market: MarketCode = MarketCode.CN
    quote: StockData | dict[str, Any] | None = None
    technical: dict[str, Any] = field(default_factory=dict)
    position: PositionInput | dict[str, Any] | None = None
    news_flags: list[str] = field(default_factory=list)
    sector_strength: float | None = None
    already_no_chase_today: bool = False


@dataclass(frozen=True)
class DecisionResult:
    symbol: str
    name: str
    market: str
    label: DecisionLabel
    score: int
    reasons: list[str]
    risks: list[str]
    confirm_conditions: list[str]
    invalidation_conditions: list[str]
    risk_level: str
    generated_at: str

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["label"] = self.label.value
        return data


def evaluate_a_share_decision(data: DecisionInput | dict[str, Any]) -> DecisionResult:
    """Evaluate an A-share watchlist/position into a human-confirmed decision label.

    This is intentionally deterministic. LLMs may explain the output later, but the
    label and guardrails must come from structured quote, technical and position data.
    """

    inp = _coerce_input(data)
    quote = _quote_dict(inp.quote)
    tech = inp.technical if isinstance(inp.technical, dict) else {}
    pos = _coerce_position(inp.position)

    reasons: list[str] = []
    risks: list[str] = []
    confirm: list[str] = []
    invalid: list[str] = []

    price = _first_float(quote, "current_price", "price", "last_price") or _f(
        tech.get("last_close")
    )
    change_pct = _first_float(quote, "change_pct", "pct_chg")
    high = _first_float(quote, "high_price", "high")
    low = _first_float(quote, "low_price", "low")
    turnover = _first_float(quote, "turnover", "amount")
    volume_ratio = _first_float(tech, "volume_ratio")

    ma5 = _f(tech.get("ma5"))
    ma10 = _f(tech.get("ma10"))
    ma20 = _f(tech.get("ma20"))
    support = _first_float(tech, "support", "support_m", "support_s")
    resistance = _first_float(tech, "resistance", "resistance_m", "resistance_s")
    rsi6 = _f(tech.get("rsi6"))
    kdj_j = _f(tech.get("kdj_j"))
    trend = str(tech.get("trend") or "")
    macd_cross = str(tech.get("macd_cross") or tech.get("macd_status") or "")
    boll_status = str(tech.get("boll_status") or "")
    volume_trend = str(tech.get("volume_trend") or "")

    score = 50

    if inp.market != MarketCode.CN:
        risks.append("当前规则按 A 股交易特征设计，非 A 股仅作弱参考")
        score -= 8

    if _is_special_treatment(inp.symbol, inp.name):
        return _result(
            inp,
            DecisionLabel.WATCH,
            15,
            ["ST/退市风险标的不进入买入候选"],
            ["特殊风险标的"],
            ["仅人工复核，不触发买入"],
            ["摘帽且重新满足趋势和风控条件"],
            "高",
        )

    if pos.stop_loss is not None and price is not None and price <= pos.stop_loss:
        reasons.append(f"当前价 {price:.2f} 触及止损价 {pos.stop_loss:.2f}")
        invalid.append("重新站回止损位并修复原买入逻辑")
        return _result(
            inp,
            DecisionLabel.STOP_LOSS,
            95,
            reasons,
            risks or ["已触发硬止损条件"],
            ["人工确认是否按计划止损或减仓"],
            invalid,
            "高",
        )

    if price is not None and support is not None and _is_breakdown(price, support):
        reasons.append(f"当前价跌破关键支撑 {support:.2f}")
        score -= 20
        risks.append("关键支撑被破坏")
        invalid.append("收盘重新站回关键支撑位")
        if pos.has_position:
            return _result(
                inp,
                DecisionLabel.STOP_LOSS if pos.stop_loss else DecisionLabel.REDUCE_WARNING,
                88 if pos.stop_loss else 78,
                reasons,
                risks,
                ["人工确认是否减仓或止损"],
                invalid,
                "高",
            )

    no_chase_reasons = _no_chase_reasons(
        change_pct=change_pct,
        price=price,
        high=high,
        low=low,
        support=support,
        resistance=resistance,
        rsi6=rsi6,
        kdj_j=kdj_j,
        boll_status=boll_status,
        volume_ratio=volume_ratio,
        already_no_chase_today=inp.already_no_chase_today,
    )
    if no_chase_reasons:
        if pos.has_position:
            return _result(
                inp,
                DecisionLabel.REDUCE_WARNING,
                82,
                no_chase_reasons,
                risks + ["持仓接近压力位或动量过热，不适合追高加仓"],
                ["人工确认是否减仓或停止加仓"],
                ["回踩支撑后重新企稳，且动量过热解除"],
                "高",
            )
        return _result(
            inp,
            DecisionLabel.NO_CHASE,
            84,
            no_chase_reasons,
            risks + ["追价风险收益比不足"],
            ["等待回踩确认或放弃本次信号"],
            ["价格回到支撑附近且风险收益比重新达标"],
            "高",
        )

    if trend == "多头排列":
        score += 12
        reasons.append("均线多头排列")
    elif trend == "空头排列":
        score -= 16
        risks.append("均线空头排列")
    elif trend == "均线交织":
        score -= 3
        risks.append("均线交织，趋势确认度不足")

    if ma5 is not None and ma10 is not None and ma20 is not None:
        if ma5 > ma10 and ma20 > 0 and (ma10 >= ma20 or abs(ma10 - ma20) / ma20 <= 0.015):
            score += 8
            reasons.append("MA5 强于 MA10，MA20 未明显转弱")
        if price is not None and price >= ma20:
            score += 6
            reasons.append("价格位于 MA20 上方")
        elif price is not None:
            score -= 10
            risks.append("价格低于 MA20")

    if "金叉" in macd_cross:
        score += 7
        reasons.append("MACD 金叉或偏多")
    elif "死叉" in macd_cross:
        score -= 9
        risks.append("MACD 死叉或偏空")

    if rsi6 is not None:
        if 35 <= rsi6 <= 68:
            score += 5
            reasons.append(f"RSI {rsi6:.1f} 处于可接受区间")
        elif rsi6 > 72:
            score -= 8
            risks.append(f"RSI {rsi6:.1f} 偏热")
        elif rsi6 < 28:
            score -= 4
            risks.append(f"RSI {rsi6:.1f} 偏弱，需等待修复")

    if volume_ratio is not None:
        if 1.2 <= volume_ratio <= 2.8:
            score += 7
            reasons.append(f"量比 {volume_ratio:.1f} 温和放大")
        elif volume_ratio > 3.5:
            score -= 5
            risks.append(f"量比 {volume_ratio:.1f} 过高，需防冲高回落")
        elif volume_ratio < 0.7:
            score -= 5
            risks.append("量能不足")
    elif "放量" in volume_trend:
        score += 3
        reasons.append("量能放大")

    rr = _risk_reward(price, support or pos.stop_loss, resistance or pos.target_price)
    if rr is not None:
        if rr >= 1.8:
            score += 8
            reasons.append(f"风险收益比约 {rr:.1f}:1")
        elif rr < 1.2:
            score -= 12
            risks.append(f"风险收益比约 {rr:.1f}:1，空间不足")

    if inp.sector_strength is not None:
        if inp.sector_strength >= 60:
            score += 5
            reasons.append("板块强度同步偏强")
        elif inp.sector_strength <= 40:
            score -= 7
            risks.append("板块强度偏弱")

    for flag in inp.news_flags:
        if flag:
            score -= 10
            risks.append(f"消息风险：{flag}")

    if pos.has_position:
        label = _position_label(score, price, pos, resistance, rsi6, volume_ratio, risks, reasons)
    else:
        label = _entry_label(score, pos, rr, risks)

    if label == DecisionLabel.BUY_CANDIDATE:
        confirm.extend(
            [
                "回踩不破关键支撑或 MA20",
                "放量突破压力位后不快速回落",
                "板块强度继续维持",
            ]
        )
        invalid.extend(
            [
                "跌破 MA20 或关键支撑",
                "放量长上影且无法收回",
                "板块转弱或出现重大负面消息",
            ]
        )
    elif label == DecisionLabel.HOLD:
        confirm.append("继续观察是否保持在成本价和止损位上方")
        invalid.append("跌破止损位、MA20 或持仓保护线")
    elif label == DecisionLabel.ADD_WATCH:
        confirm.extend(["只在回踩支撑不破时人工确认", "不得突破后直接追高加仓"])
        invalid.append("放量滞涨或跌回 MA10/MA20 下方")
    elif label == DecisionLabel.REDUCE_WARNING:
        confirm.append("人工确认是否按仓位纪律减仓")
        invalid.append("重新站回压力位并恢复量价配合")
    else:
        confirm.append("继续观察，不触发交易动作")
        invalid.append("等待趋势、量能和风险收益比同时改善")

    return _result(
        inp,
        label,
        _clamp_int(score, 0, 100),
        _dedupe(reasons) or ["结构化条件不足，保持观察"],
        _dedupe(risks),
        _dedupe(confirm),
        _dedupe(invalid),
        _risk_level(label, score, risks),
    )


def _entry_label(
    score: int, pos: PositionInput, rr: float | None, risks: list[str]
) -> DecisionLabel:
    if pos.stop_loss is None:
        risks.append("未设置止损位，不允许进入买入候选")
        return DecisionLabel.WATCH
    if rr is None or rr < 1.8:
        return DecisionLabel.WATCH
    if score >= 72:
        return DecisionLabel.BUY_CANDIDATE
    return DecisionLabel.WATCH


def _position_label(
    score: int,
    price: float | None,
    pos: PositionInput,
    resistance: float | None,
    rsi6: float | None,
    volume_ratio: float | None,
    risks: list[str],
    reasons: list[str],
) -> DecisionLabel:
    pnl_pct = None
    if price is not None and pos.avg_cost:
        pnl_pct = (price - pos.avg_cost) / pos.avg_cost * 100
        if pnl_pct >= 8:
            reasons.append(f"持仓浮盈约 {pnl_pct:.1f}%")
        elif pnl_pct <= -4:
            risks.append(f"持仓浮亏约 {pnl_pct:.1f}%")

    if resistance and price and price >= resistance * 0.985 and (rsi6 or 0) >= 68:
        risks.append("接近压力位且动量偏热")
        return DecisionLabel.REDUCE_WARNING
    if volume_ratio is not None and volume_ratio >= 3.5 and (rsi6 or 0) >= 70:
        risks.append("放量过热，需防冲高回落")
        return DecisionLabel.REDUCE_WARNING
    if score >= 76 and pnl_pct is not None and pnl_pct > 0:
        return DecisionLabel.ADD_WATCH
    if score >= 55:
        return DecisionLabel.HOLD
    return DecisionLabel.REDUCE_WARNING


def _no_chase_reasons(**kwargs: Any) -> list[str]:
    reasons: list[str] = []
    if kwargs["already_no_chase_today"]:
        reasons.append("当日已触发禁止追高")
    change_pct = kwargs["change_pct"]
    price = kwargs["price"]
    high = kwargs["high"]
    low = kwargs["low"]
    support = kwargs["support"]
    resistance = kwargs["resistance"]
    rsi6 = kwargs["rsi6"]
    kdj_j = kwargs["kdj_j"]
    boll_status = kwargs["boll_status"]
    volume_ratio = kwargs["volume_ratio"]

    if change_pct is not None and change_pct >= 7:
        reasons.append(f"当日涨幅 {change_pct:.1f}% 过高")
    if price is not None and support is not None and support > 0:
        distance = (price - support) / support * 100
        if distance >= 9:
            reasons.append(f"距离支撑位约 {distance:.1f}%")
    if price is not None and resistance is not None and resistance > 0:
        room = (resistance - price) / price * 100
        if 0 <= room <= 3:
            reasons.append("上方压力位距离过近")
    if rsi6 is not None and rsi6 >= 78:
        reasons.append(f"RSI {rsi6:.1f} 严重偏热")
    if kdj_j is not None and kdj_j >= 105:
        reasons.append(f"KDJ J值 {kdj_j:.1f} 偏热")
    if "突破上轨" in boll_status and volume_ratio is not None and volume_ratio > 3:
        reasons.append("布林上轨外放量，追高回撤风险高")
    if high is not None and low is not None and price is not None and high > low:
        close_position = (price - low) / (high - low)
        if close_position < 0.45 and volume_ratio is not None and volume_ratio > 2.5:
            reasons.append("放量冲高后收盘位置偏低")
    return reasons


def _risk_reward(
    price: float | None, downside_ref: float | None, upside_ref: float | None
) -> float | None:
    if price is None or downside_ref is None or upside_ref is None:
        return None
    downside = price - downside_ref
    upside = upside_ref - price
    if downside <= 0 or upside <= 0:
        return None
    return round(upside / downside, 2)


def _coerce_input(data: DecisionInput | dict[str, Any]) -> DecisionInput:
    if isinstance(data, DecisionInput):
        return data
    raw = dict(data or {})
    market = raw.get("market") or MarketCode.CN
    if not isinstance(market, MarketCode):
        market = MarketCode(str(market))
    return DecisionInput(
        symbol=str(raw.get("symbol") or ""),
        name=str(raw.get("name") or ""),
        market=market,
        quote=raw.get("quote"),
        technical=raw.get("technical") or {},
        position=raw.get("position"),
        news_flags=list(raw.get("news_flags") or []),
        sector_strength=_f(raw.get("sector_strength")),
        already_no_chase_today=bool(raw.get("already_no_chase_today")),
    )


def _coerce_position(data: PositionInput | dict[str, Any] | None) -> PositionInput:
    if isinstance(data, PositionInput):
        return data
    raw = data if isinstance(data, dict) else {}
    return PositionInput(
        has_position=bool(raw.get("has_position")),
        avg_cost=_f(raw.get("avg_cost")),
        quantity=_f(raw.get("quantity")),
        stop_loss=_first_float(raw, "stop_loss", "stop_loss_price"),
        target_price=_f(raw.get("target_price")),
        max_position_ratio=_f(raw.get("max_position_ratio")),
        current_position_ratio=_f(raw.get("current_position_ratio")),
        trading_style=str(raw.get("trading_style") or "swing"),
    )


def _quote_dict(quote: StockData | dict[str, Any] | None) -> dict[str, Any]:
    if isinstance(quote, StockData):
        return asdict(quote)
    return quote if isinstance(quote, dict) else {}


def _result(
    inp: DecisionInput,
    label: DecisionLabel,
    score: int,
    reasons: list[str],
    risks: list[str],
    confirm: list[str],
    invalid: list[str],
    risk_level: str,
) -> DecisionResult:
    return DecisionResult(
        symbol=inp.symbol,
        name=inp.name,
        market=inp.market.value,
        label=label,
        score=_clamp_int(score, 0, 100),
        reasons=_dedupe(reasons),
        risks=_dedupe(risks),
        confirm_conditions=_dedupe(confirm),
        invalidation_conditions=_dedupe(invalid),
        risk_level=risk_level,
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
    )


def _risk_level(label: DecisionLabel, score: int, risks: list[str]) -> str:
    if label in {DecisionLabel.STOP_LOSS, DecisionLabel.NO_CHASE, DecisionLabel.REDUCE_WARNING}:
        return "高"
    if risks or score < 65:
        return "中"
    return "低"


def _is_special_treatment(symbol: str, name: str) -> bool:
    text = f"{symbol} {name}".upper()
    return "ST" in text or "退" in text


def _is_breakdown(price: float, support: float) -> bool:
    return support > 0 and price < support * 0.985


def _first_float(data: dict[str, Any], *keys: str) -> float | None:
    for key in keys:
        value = _f(data.get(key))
        if value is not None:
            return value
    return None


def _f(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def _clamp_int(value: float | int, low: int, high: int) -> int:
    return max(low, min(high, int(round(value))))


def _dedupe(items: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for item in items:
        text = str(item or "").strip()
        if text and text not in seen:
            out.append(text)
            seen.add(text)
    return out
