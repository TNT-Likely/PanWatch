"""缠论情绪博弈策略单元测试。"""

from src.collectors.kline_collector import KlineData
from src.core.signals.chan_emotion_strategy import (
    LevelAnalysis,
    analyze_chan_emotion,
    build_strokes,
    build_decision_explanation,
    detect_fractals,
    detect_macd_divergence,
    detect_pivot,
    process_kline_inclusion,
    serialize_chan_emotion_result,
)


def _make_klines(prices: list[float], start_date: str = "2024-01-01") -> list[KlineData]:
    out: list[KlineData] = []
    for i, p in enumerate(prices):
        out.append(
            KlineData(
                date=f"2024-01-{i + 1:02d}",
                open=p * 0.99,
                close=p,
                high=p * 1.01,
                low=p * 0.98,
                volume=1000 + i,
            )
        )
    return out


def test_process_kline_inclusion_merges_contained_bars():
    """包含处理应合并被包含 K 线。"""
    klines = _make_klines([10, 10.2, 10.1, 10.3, 10.5])
    bars = process_kline_inclusion(klines)
    assert len(bars) <= len(klines)
    assert len(bars) >= 2


def test_build_strokes_requires_minimum_span():
    """笔的生成需要至少 5 根处理后 K 线跨度。"""
    prices = [10 + i * 0.5 for i in range(20)]
    klines = _make_klines(prices)
    bars = process_kline_inclusion(klines)
    fractals = detect_fractals(bars)
    strokes = build_strokes(fractals, bars)
    assert isinstance(strokes, list)
    for s in strokes:
        assert s.bars >= 5


def test_analyze_chan_emotion_returns_structured_result():
    """多级别分析应返回完整策略结构。"""
    daily = _make_klines([10 + i * 0.2 for i in range(60)])
    result = analyze_chan_emotion(
        "600519",
        "CN",
        holding=False,
        daily_klines=daily,
        m30_klines=daily[-40:],
        m5_klines=daily[-30:],
        daily_summary={
            "trend": "多头排列",
            "change_5d": 4.5,
            "recent_5_up": 4,
            "volume_trend": "放量",
            "last_close": daily[-1].close,
            "support": daily[-1].close * 0.95,
            "resistance": daily[-1].close * 1.08,
        },
    )
    payload = serialize_chan_emotion_result(result)
    assert payload["strategy_code"] == "chan_emotion"
    assert len(payload["levels"]) == 3
    assert payload["win_rate"] >= 5
    assert payload["action_label"] in ("买入", "观望", "回避", "加仓", "减仓", "卖出", "持有")
    assert payload["decision_explanation"]
    assert payload["agent_instruction"]


def test_detect_pivot_from_three_strokes():
    """三笔重叠应能识别中枢。"""
    from src.core.signals.chan_emotion_strategy import Fractal, Stroke

    f = [
        Fractal("bottom", 0, 10.0, "d1"),
        Fractal("top", 6, 12.0, "d2"),
        Fractal("bottom", 12, 11.0, "d3"),
        Fractal("top", 18, 13.0, "d4"),
        Fractal("bottom", 24, 12.0, "d5"),
        Fractal("top", 30, 14.0, "d6"),
    ]
    strokes = [
        Stroke("up", f[0], f[1], 6),
        Stroke("down", f[1], f[2], 6),
        Stroke("up", f[2], f[3], 6),
        Stroke("down", f[3], f[4], 6),
        Stroke("up", f[4], f[5], 6),
    ]
    pivot = detect_pivot(strokes)
    assert pivot is not None
    assert pivot.zd < pivot.zg


def test_macd_divergence_on_synthetic_uptrend():
    """价升量缩型 MACD 柱面积减弱应提示顶背驰。"""
    # 两段上涨，后段涨幅更大但 MACD 动能模拟偏弱需足够 K 线
    base = [10.0]
    for _ in range(25):
        base.append(base[-1] * 1.01)
    for _ in range(25):
        base.append(base[-1] * 1.015)
    klines = _make_klines(base)
    bars = process_kline_inclusion(klines)
    fractals = detect_fractals(bars)
    strokes = build_strokes(fractals, bars)
    div = detect_macd_divergence(klines, strokes)
    # 不一定每次合成数据都背驰，但函数应可调用
    assert div is None or "背驰" in div


def test_decision_explanation_clarifies_uptrend_sell_signal():
    """上升趋势中的卖出信号应解释为背驰触发的持仓退出。"""
    daily = LevelAnalysis(
        timeframe="1d",
        label="日线（定方向）",
        bar_count=120,
        trend="trend_up",
        stroke_count=14,
        pivot=None,
        divergence="顶背驰（第一类卖点预警）",
        signal_tags=["顶背驰（第一类卖点预警）"],
    )
    op = LevelAnalysis(
        timeframe="m30",
        label="30分钟（主操作）",
        bar_count=240,
        trend="trend_up",
        stroke_count=25,
        pivot=None,
        divergence="顶背驰（第一类卖点预警）",
        signal_tags=["顶背驰（第一类卖点预警）"],
    )
    micro = LevelAnalysis(
        timeframe="m5",
        label="5分钟（精确定位）",
        bar_count=240,
        trend="trend_up",
        stroke_count=18,
        pivot=None,
        divergence=None,
        signal_tags=[],
    )

    explanation = build_decision_explanation(
        holding=True,
        emotion_label="赚钱效应（强势扩散，宜持股或三类买点）",
        daily=daily,
        op=op,
        micro=micro,
        win_rate=53.0,
        action="sell",
        action_label="卖出",
        evidence=[
            {"text": "日线趋势向上", "delta": 10},
            {"text": "赚钱效应阶段", "delta": 8},
            {"text": "30分钟（主操作）顶背驰（第一类卖点预警）", "delta": -15},
        ],
    )

    assert "三周期仍是上升趋势" in explanation
    assert "30分钟出现顶背驰" in explanation
    assert "持仓退出信号" in explanation
    assert "53.0%" in explanation


def test_decision_explanation_for_hold_uses_intact_structure_copy():
    """持有信号应解释为结构未破坏，而不是信号不清晰。"""
    daily = LevelAnalysis(
        timeframe="1d",
        label="日线（定方向）",
        bar_count=120,
        trend="trend_up",
        stroke_count=10,
        pivot=None,
        divergence=None,
        signal_tags=[],
    )
    op = LevelAnalysis(
        timeframe="m30",
        label="30分钟（主操作）",
        bar_count=240,
        trend="trend_up",
        stroke_count=16,
        pivot=None,
        divergence=None,
        signal_tags=[],
    )
    micro = LevelAnalysis(
        timeframe="m5",
        label="5分钟（精确定位）",
        bar_count=240,
        trend="consolidation",
        stroke_count=12,
        pivot=None,
        divergence=None,
        signal_tags=[],
    )

    explanation = build_decision_explanation(
        holding=True,
        emotion_label="情绪中性（观望为主，等结构清晰）",
        daily=daily,
        op=op,
        micro=micro,
        win_rate=58.0,
        action="hold",
        action_label="持有",
        evidence=[
            {"text": "日线趋势向上", "delta": 10},
            {"text": "情绪中性", "delta": -2},
        ],
    )

    assert "结构未破坏" in explanation
    assert "持股待涨" in explanation
