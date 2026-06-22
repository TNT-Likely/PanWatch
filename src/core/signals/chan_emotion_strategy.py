"""缠论几何 + 养家心法情绪博弈 — 多级别联立看盘策略。

主操作级别 30 分钟，5 分钟精确定位，日线定方向。
供 Agent 执行与前端股票详情展示。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

from src.collectors.kline_collector import KlineCollector, KlineData, _calculate_macd
from src.models.market import MarketCode

TimeframeKey = Literal["1d", "m30", "m5"]
EmotionPhase = Literal["profit_effect", "loss_effect", "neutral"]
TrendType = Literal["trend_up", "trend_down", "consolidation", "unknown"]
ActionKey = Literal["buy", "add", "watch", "avoid", "reduce", "sell", "hold"]


@dataclass
class ProcessedBar:
    index: int
    date: str
    open: float
    close: float
    high: float
    low: float


@dataclass
class Fractal:
    kind: Literal["top", "bottom"]
    index: int
    price: float
    date: str


@dataclass
class Stroke:
    direction: Literal["up", "down"]
    start: Fractal
    end: Fractal
    bars: int


@dataclass
class Pivot:
    zd: float  # 中枢下沿
    zg: float  # 中枢上沿
    start_index: int
    end_index: int


@dataclass
class LevelAnalysis:
    timeframe: TimeframeKey
    label: str
    bar_count: int
    trend: TrendType
    stroke_count: int
    pivot: Pivot | None
    divergence: str | None
    signal_tags: list[str] = field(default_factory=list)


@dataclass
class ChanEmotionResult:
    symbol: str
    market: str
    asof: str
    last_close: float | None
    emotion_phase: EmotionPhase
    emotion_label: str
    levels: list[LevelAnalysis]
    win_rate: float
    position_pct: int
    position_label: str
    action: ActionKey
    action_label: str
    signal: str
    reason: str
    stop_loss: float | None
    target_price: float | None
    invalidation: str
    agent_instruction: str
    human_notes: list[str]
    evidence: list[dict[str, Any]]


def _bar_dict(k: KlineData) -> ProcessedBar:
    return ProcessedBar(
        index=0,
        date=k.date,
        open=k.open,
        close=k.close,
        high=k.high,
        low=k.low,
    )


def process_kline_inclusion(klines: list[KlineData]) -> list[ProcessedBar]:
    """K 线包含处理：合并被包含 K 线，消除随机波动。"""
    if not klines:
        return []
    bars: list[ProcessedBar] = []
    for i, k in enumerate(klines):
        bars.append(
            ProcessedBar(
                index=i,
                date=k.date,
                open=k.open,
                close=k.close,
                high=k.high,
                low=k.low,
            )
        )
    if len(bars) < 2:
        return bars

    merged: list[ProcessedBar] = [bars[0]]
    direction: int = 0  # 1 up, -1 down

    for bar in bars[1:]:
        prev = merged[-1]
        contained = bar.high <= prev.high and bar.low >= prev.low
        prev_contained = prev.high <= bar.high and prev.low >= bar.low

        if contained or prev_contained:
            if direction >= 0:
                new_high = max(prev.high, bar.high)
                new_low = max(prev.low, bar.low)
            else:
                new_high = min(prev.high, bar.high)
                new_low = min(prev.low, bar.low)
            merged[-1] = ProcessedBar(
                index=bar.index,
                date=bar.date,
                open=prev.open,
                close=bar.close,
                high=new_high,
                low=new_low,
            )
            if bar.close > prev.close:
                direction = 1
            elif bar.close < prev.close:
                direction = -1
            continue

        if bar.high > prev.high and bar.low > prev.low:
            direction = 1
        elif bar.high < prev.high and bar.low < prev.low:
            direction = -1
        merged.append(bar)

    return merged


def detect_fractals(bars: list[ProcessedBar]) -> list[Fractal]:
    """识别顶底分型。"""
    out: list[Fractal] = []
    if len(bars) < 3:
        return out
    for i in range(1, len(bars) - 1):
        left, mid, right = bars[i - 1], bars[i], bars[i + 1]
        if mid.high > left.high and mid.high > right.high and mid.low > left.low and mid.low > right.low:
            out.append(Fractal(kind="top", index=mid.index, price=mid.high, date=mid.date))
        elif mid.low < left.low and mid.low < right.low and mid.high < left.high and mid.high < right.high:
            out.append(Fractal(kind="bottom", index=mid.index, price=mid.low, date=mid.date))
    return out


def build_strokes(fractals: list[Fractal], bars: list[ProcessedBar]) -> list[Stroke]:
    """由交替分型生成笔（至少 5 根处理后 K 线）。"""
    if len(fractals) < 2:
        return []
    strokes: list[Stroke] = []
    last = fractals[0]
    for f in fractals[1:]:
        if f.kind == last.kind:
            # 同向分型取极值
            if f.kind == "top" and f.price >= last.price:
                last = f
            elif f.kind == "bottom" and f.price <= last.price:
                last = f
            continue
        span = abs(f.index - last.index) + 1
        if span < 5:
            last = f
            continue
        direction: Literal["up", "down"] = "up" if f.kind == "top" else "down"
        strokes.append(Stroke(direction=direction, start=last, end=f, bars=span))
        last = f
    return strokes


def detect_pivot(strokes: list[Stroke]) -> Pivot | None:
    """三笔重叠区间定义为中枢。"""
    if len(strokes) < 3:
        return None
    recent = strokes[-3:]
    lows = []
    highs = []
    for s in recent:
        lo = min(s.start.price, s.end.price)
        hi = max(s.start.price, s.end.price)
        lows.append(lo)
        highs.append(hi)
    zd = max(lows)
    zg = min(highs)
    if zg <= zd:
        return None
    return Pivot(
        zd=zd,
        zg=zg,
        start_index=recent[0].start.index,
        end_index=recent[-1].end.index,
    )


def classify_trend(strokes: list[Stroke], pivot: Pivot | None, close: float | None) -> TrendType:
    if not strokes:
        return "unknown"
    if pivot and close is not None:
        if close > pivot.zg * 1.02:
            return "trend_up"
        if close < pivot.zd * 0.98:
            return "trend_down"
        return "consolidation"
    last_two = strokes[-2:] if len(strokes) >= 2 else strokes
    if len(last_two) == 2 and last_two[0].direction == "up" and last_two[1].direction == "up":
        return "trend_up"
    if len(last_two) == 2 and last_two[0].direction == "down" and last_two[1].direction == "down":
        return "trend_down"
    return "consolidation"


def _macd_area(hist: list[float], start_idx: int, end_idx: int, positive: bool) -> float:
    if start_idx > end_idx:
        start_idx, end_idx = end_idx, start_idx
    total = 0.0
    for i in range(start_idx, min(end_idx + 1, len(hist))):
        v = hist[i]
        if positive and v > 0:
            total += v
        elif not positive and v < 0:
            total += abs(v)
    return total


def detect_macd_divergence(klines: list[KlineData], strokes: list[Stroke]) -> str | None:
    """趋势背驰：价创新高/新低但 MACD 柱面积或高度减弱。"""
    if len(klines) < 30 or len(strokes) < 2:
        return None
    closes = [k.close for k in klines]
    macd = _calculate_macd(closes)
    if not macd:
        return None
    dif, dea, hist = macd
    # align hist length to klines
    offset = len(klines) - len(hist)

    up_strokes = [s for s in strokes if s.direction == "up"]
    down_strokes = [s for s in strokes if s.direction == "down"]

    if len(up_strokes) >= 2:
        a, b = up_strokes[-2], up_strokes[-1]
        if b.end.price > a.end.price:
            ha = _macd_area(hist, a.start.index - offset, a.end.index - offset, True)
            hb = _macd_area(hist, b.start.index - offset, b.end.index - offset, True)
            if hb < ha * 0.85:
                return "顶背驰（第一类卖点预警）"

    if len(down_strokes) >= 2:
        a, b = down_strokes[-2], down_strokes[-1]
        if b.end.price < a.end.price:
            ha = _macd_area(hist, a.start.index - offset, a.end.index - offset, False)
            hb = _macd_area(hist, b.start.index - offset, b.end.index - offset, False)
            if hb < ha * 0.85:
                return "底背驰（第一类买点预警）"

    return None


def detect_third_buy(pivot: Pivot | None, klines: list[KlineData], strokes: list[Stroke]) -> bool:
    """第三类买点：离开中枢后回抽不回到中枢内部。"""
    if not pivot or not klines or len(strokes) < 2:
        return False
    last = strokes[-1]
    if last.direction != "up":
        return False
    recent_low = min(k.low for k in klines[-5:])
    if recent_low > pivot.zg:
        return True
    return False


def detect_third_sell(pivot: Pivot | None, klines: list[KlineData], strokes: list[Stroke]) -> bool:
    if not pivot or not klines or len(strokes) < 2:
        return False
    last = strokes[-1]
    if last.direction != "down":
        return False
    recent_high = max(k.high for k in klines[-5:])
    if recent_high < pivot.zd:
        return True
    return False


def assess_emotion(
    daily_summary: dict[str, Any] | None,
    klines: list[KlineData],
) -> tuple[EmotionPhase, str]:
    """个股层面近似市场情绪：赚钱效应 / 亏钱效应。"""
    score = 0
    summary = daily_summary or {}
    trend = str(summary.get("trend") or "")
    change_5d = summary.get("change_5d")
    recent_up = summary.get("recent_5_up")
    volume_trend = str(summary.get("volume_trend") or "")

    if "多头" in trend:
        score += 2
    elif "空头" in trend:
        score -= 2
    if isinstance(change_5d, (int, float)):
        if change_5d > 3:
            score += 2
        elif change_5d > 0:
            score += 1
        elif change_5d < -3:
            score -= 2
        elif change_5d < 0:
            score -= 1
    if isinstance(recent_up, int):
        if recent_up >= 4:
            score += 1
        elif recent_up <= 1:
            score -= 1
    if "放量" in volume_trend:
        score += 1
    elif "缩量" in volume_trend:
        score -= 1

    if len(klines) >= 5:
        last5 = klines[-5:]
        up_cnt = sum(1 for i in range(1, len(last5)) if last5[i].close > last5[i - 1].close)
        if up_cnt >= 4:
            score += 1
        elif up_cnt <= 1:
            score -= 1

    if score >= 3:
        return "profit_effect", "赚钱效应（强势扩散，宜持股或三类买点）"
    if score <= -2:
        return "loss_effect", "亏钱效应（弱势蔓延，宜空仓或等大级别底背驰）"
    return "neutral", "情绪中性（观望为主，等结构清晰）"


def analyze_level(
    klines: list[KlineData],
    timeframe: TimeframeKey,
    label: str,
) -> LevelAnalysis:
    bars = process_kline_inclusion(klines)
    fractals = detect_fractals(bars)
    strokes = build_strokes(fractals, bars)
    pivot = detect_pivot(strokes)
    close = klines[-1].close if klines else None
    trend = classify_trend(strokes, pivot, close)
    divergence = detect_macd_divergence(klines, strokes)
    tags: list[str] = []
    if divergence:
        tags.append(divergence)
    if detect_third_buy(pivot, klines, strokes):
        tags.append("第三类买点")
    if detect_third_sell(pivot, klines, strokes):
        tags.append("第三类卖点")
    if pivot:
        tags.append(f"中枢 ZD={pivot.zd:.2f} ZG={pivot.zg:.2f}")

    return LevelAnalysis(
        timeframe=timeframe,
        label=label,
        bar_count=len(klines),
        trend=trend,
        stroke_count=len(strokes),
        pivot=pivot,
        divergence=divergence,
        signal_tags=tags,
    )


def _position_from_win_rate(win_rate: float, upside_pct: float) -> tuple[int, str]:
    if win_rate < 60:
        return 0, "观望（空仓或不动）"
    if win_rate < 70:
        return 25, "小仓位出击（约 25%）"
    if win_rate < 90:
        return 50, "中等仓位（约 50%）"
    if upside_pct > 30:
        return 90, "重仓/满仓（赢面>90% 且空间>30%）"
    return 70, "偏大仓位（约 70%）"


def _action_from_context(
    holding: bool,
    emotion: EmotionPhase,
    daily: LevelAnalysis,
    op: LevelAnalysis,
    micro: LevelAnalysis,
    win_rate: float,
) -> tuple[ActionKey, str, str, str]:
    op_tags = set(op.signal_tags)
    daily_tags = set(daily.signal_tags)

    sell_signal = any("卖点" in t or "顶背驰" in t for t in op_tags | daily_tags)
    buy_signal = any("买点" in t for t in op_tags) or any("底背驰" in t for t in op_tags | daily_tags)

    if holding:
        if sell_signal and win_rate < 55:
            return "sell", "卖出", "趋势背驰或第三类卖点", "卖点常在疯狂上涨中形成，机械化清仓"
        if sell_signal:
            return "reduce", "减仓", "操作级别出现背驰/卖点结构", "先降风险，保留底仓观察"
        if buy_signal and emotion != "loss_effect" and win_rate >= 60:
            return "add", "加仓", "三类买点或底背驰且情绪未恶化", "小仓加码，设好止损"
        if win_rate >= 55:
            return "hold", "持有", "结构未破坏", "持股待涨，不破止损"
        return "watch", "观望", "赢面不足", "不追加，等更清晰信号"

    if emotion == "loss_effect" and not any("底背驰" in t for t in daily_tags):
        return "avoid", "回避", "亏钱效应下无大级别底背驰", "宁愿错过，不买错"
    if buy_signal and emotion == "profit_effect" and win_rate >= 60:
        return "buy", "买入", "赚钱效应 + 操作级别买点", "可按计划建仓"
    if buy_signal and win_rate >= 65:
        return "buy", "买入", "多级别出现买点结构", "小仓试探，严格止损"
    if sell_signal or emotion == "loss_effect":
        return "avoid", "回避", "弱势或卖点结构", "空仓等待"
    return "watch", "观望", "结构未共振", "等 30 分钟级别三类买点或底背驰"


def build_agent_instruction(
    emotion: EmotionPhase,
    daily: LevelAnalysis,
    op: LevelAnalysis,
    micro: LevelAnalysis,
    action: ActionKey,
    position_pct: int,
) -> str:
    lines = [
        f"市场环境={emotion}",
        f"日线={daily.trend} 笔{daily.stroke_count}",
        f"30分={op.trend} 信号={','.join(op.signal_tags) or '无'}",
        f"5分={micro.trend} 信号={','.join(micro.signal_tags) or '无'}",
    ]
    if emotion == "profit_effect" and any("第三类买点" in t for t in op.signal_tags):
        lines.append("IF 30min三类买点 AND 5min无顶背驰 → 买入约50%仓位")
    elif any("顶背驰" in t for t in op.signal_tags):
        lines.append("IF 30min顶背驰 AND 5min顶分型确认 → 清仓卖出")
    elif emotion == "loss_effect":
        lines.append("弱势周期：仅日线底背驰时超跌反弹，其余空仓")
    else:
        lines.append(f"当前指令：{action} 仓位约{position_pct}%")
    return "；".join(lines)


def analyze_chan_emotion(
    symbol: str,
    market: str,
    *,
    holding: bool = False,
    daily_klines: list[KlineData] | None = None,
    m30_klines: list[KlineData] | None = None,
    m5_klines: list[KlineData] | None = None,
    daily_summary: dict[str, Any] | None = None,
) -> ChanEmotionResult:
    """主入口：多级别联立分析。"""
    mkt = MarketCode(market) if market in ("CN", "HK", "US") else MarketCode.CN
    collector = KlineCollector(mkt)

    if daily_klines is None:
        daily_klines = collector.get_klines(symbol, days=120)
    if m30_klines is None:
        m30_klines = collector.get_intraday_klines(symbol, interval="m30", count=240)
    if m5_klines is None:
        m5_klines = collector.get_intraday_klines(symbol, interval="m5", count=240)
    if daily_summary is None and daily_klines:
        daily_summary = collector.get_kline_summary(symbol)

    daily = analyze_level(daily_klines or [], "1d", "日线（定方向）")
    op = analyze_level(m30_klines or daily_klines or [], "m30", "30分钟（主操作）")
    micro = analyze_level(m5_klines or m30_klines or [], "m5", "5分钟（精确定位）")

    emotion, emotion_label = assess_emotion(daily_summary, daily_klines or [])
    last_close = (daily_klines[-1].close if daily_klines else None) or (
        daily_summary.get("last_close") if daily_summary else None
    )

    win_rate = 50.0
    evidence: list[dict[str, Any]] = []

    if daily.trend == "trend_up":
        win_rate += 10
        evidence.append({"text": "日线趋势向上", "delta": 10})
    elif daily.trend == "trend_down":
        win_rate -= 12
        evidence.append({"text": "日线趋势向下", "delta": -12})

    if emotion == "profit_effect":
        win_rate += 8
        evidence.append({"text": "赚钱效应阶段", "delta": 8})
    elif emotion == "loss_effect":
        win_rate -= 10
        evidence.append({"text": "亏钱效应阶段", "delta": -10})

    for lvl, weight in ((op, 15), (micro, 5)):
        for tag in lvl.signal_tags:
            if "买点" in tag:
                win_rate += weight
                evidence.append({"text": f"{lvl.label}{tag}", "delta": weight})
            if "卖点" in tag or "顶背驰" in tag:
                win_rate -= weight
                evidence.append({"text": f"{lvl.label}{tag}", "delta": -weight})

    if op.pivot and last_close:
        upside = (op.pivot.zg - last_close) / last_close * 100 if last_close > 0 else 0
        room_to_zg = max(0, upside)
    else:
        room_to_zg = 15.0
        if daily_summary and daily_summary.get("resistance") and last_close:
            res = float(daily_summary["resistance"])
            if res > last_close:
                room_to_zg = (res - last_close) / last_close * 100

    win_rate = max(5.0, min(98.0, win_rate))
    position_pct, position_label = _position_from_win_rate(win_rate, room_to_zg)

    action, action_label, signal_core, reason_core = _action_from_context(
        holding, emotion, daily, op, micro, win_rate
    )

    stop_loss = None
    invalidation = ""
    if op.pivot:
        stop_loss = round(op.pivot.zd * 0.98, 3)
        invalidation = f"跌破中枢下沿 ZD≈{op.pivot.zd:.2f}（二类买点失效）"
    elif daily_summary and daily_summary.get("support"):
        stop_loss = round(float(daily_summary["support"]) * 0.97, 3)
        invalidation = f"跌破关键支撑 {daily_summary['support']}"

    target_price = None
    if op.pivot and last_close:
        target_price = round(op.pivot.zg * 1.05, 3)
    elif daily_summary and daily_summary.get("resistance"):
        target_price = round(float(daily_summary["resistance"]), 3)

    signal = signal_core
    if op.signal_tags:
        signal = f"{signal_core} · {op.signal_tags[0]}"

    reason_parts = [reason_core, emotion_label]
    if daily.divergence:
        reason_parts.append(daily.divergence)
    reason = "；".join(reason_parts)

    human_notes = [
        "Agent 负责包含处理、笔、线段与背驰识别；您负责情绪确认与题材筛选。",
        "关注政策与板块轮动，技术面无法感知宏观突变。",
        "买卖点出现后机械化执行，克服贪婪与恐惧。",
    ]

    asof = daily_klines[-1].date if daily_klines else ""

    return ChanEmotionResult(
        symbol=symbol,
        market=mkt.value,
        asof=asof,
        last_close=last_close,
        emotion_phase=emotion,
        emotion_label=emotion_label,
        levels=[daily, op, micro],
        win_rate=round(win_rate, 1),
        position_pct=position_pct if action in ("buy", "add") else 0,
        position_label=position_label if action in ("buy", "add") else "不建仓",
        action=action,
        action_label=action_label,
        signal=signal,
        reason=reason,
        stop_loss=stop_loss,
        target_price=target_price,
        invalidation=invalidation,
        agent_instruction=build_agent_instruction(
            emotion, daily, op, micro, action, position_pct
        ),
        human_notes=human_notes,
        evidence=evidence,
    )


def serialize_chan_emotion_result(result: ChanEmotionResult) -> dict[str, Any]:
    """序列化为 API JSON。"""
    levels = []
    for lvl in result.levels:
        pivot = None
        if lvl.pivot:
            pivot = {"zd": lvl.pivot.zd, "zg": lvl.pivot.zg}
        levels.append(
            {
                "timeframe": lvl.timeframe,
                "label": lvl.label,
                "bar_count": lvl.bar_count,
                "trend": lvl.trend,
                "stroke_count": lvl.stroke_count,
                "pivot": pivot,
                "divergence": lvl.divergence,
                "signal_tags": lvl.signal_tags,
            }
        )
    return {
        "symbol": result.symbol,
        "market": result.market,
        "asof": result.asof,
        "last_close": result.last_close,
        "emotion_phase": result.emotion_phase,
        "emotion_label": result.emotion_label,
        "levels": levels,
        "win_rate": result.win_rate,
        "position_pct": result.position_pct,
        "position_label": result.position_label,
        "action": result.action,
        "action_label": result.action_label,
        "signal": result.signal,
        "reason": result.reason,
        "stop_loss": result.stop_loss,
        "target_price": result.target_price,
        "invalidation": result.invalidation,
        "agent_instruction": result.agent_instruction,
        "human_notes": result.human_notes,
        "evidence": result.evidence,
        "strategy_code": "chan_emotion",
        "strategy_name": "缠论情绪博弈",
    }
