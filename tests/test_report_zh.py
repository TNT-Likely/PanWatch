"""盘前报告中文化与排版单测:零英文残留(白名单豁免)、聚合、去重、动作一致性。"""

from __future__ import annotations

import re

from src.modules.automation.premarket_pipeline.labels import zh_period
from src.modules.automation.premarket_pipeline.report import render_report
from src.modules.automation.premarket_pipeline.types import (
    MacroCards,
    PickCandidate,
    SectorForecast,
    SectorForecastItem,
)

# 中文语境不允许出现的英文残留(黑名单);金融标准缩写豁免
BLACKLIST = re.compile(
    r"bearish|bullish|neutral|tighten|relax|degrade|FAIL|N/A|review:|\bllm\b|algorithm"
    r"|\bmedium\b|\bhigh\b|\blow\b",
    re.IGNORECASE,
)


def _cards() -> MacroCards:
    return MacroCards(cards=[{
        "title": "市场环境卡",
        "direction": "bearish",
        "confidence": 6,
        "rationale": "隔夜美股三大指数齐跌,纳指-1.13%;技术面降级口径(degrade=2)佐证缺位,10.4%,按LOW档评估(medium)。",
        "position_policy": {
            "review": "外围走弱，防守为主",
            "diff": [{"item": "仓位上限", "direction": "tighten", "from": "60%", "to": "50%"}],
        },
    }], status="ok")


def _forecast() -> SectorForecast:
    return SectorForecast(items=[
        SectorForecastItem(board_code="881144", board_name="医疗器械", direction="neutral",
                           confidence=0.30, momentum_score=5.7932, source="llm+algorithm"),
        SectorForecastItem(board_code="881173", board_name="小家电", direction="bullish",
                           confidence=0.30, momentum_score=-3.436, source="algorithm"),
    ], llm_ok=True)


def _candidates() -> list[PickCandidate]:
    def cand(symbol, name, verified, rr=3.75, price=58.21):
        return PickCandidate(
            symbol=symbol, stock_name=name, board_code="881144", board_name="医疗器械",
            current_price=price, entry_low=44.7, entry_high=55.271, stop_loss=45.987,
            target_price=63.9, grade="C", leader_score=0.64, rr=rr, v11_pass=True,
            valuation={"source": "technical_ma20_low60", "degrade_level": 2},
            verified=verified, llm_ok=False,
            verification={"passed": verified} if verified else {"passed": False},
        )
    return [cand("301580", "爱迪特", True), cand("688626", "翔宇医疗", False)]


def _declarations() -> list[dict]:
    as_of = "2026-09-24T10:09:17+08:00"
    rows = [
        {"item": "事件日历", "source": "db:event_calendar_items", "as_of": as_of,
         "caliber": "近7日+未来5交易日窗口", "degrade_level": 0},
        {"item": "宏观指标", "source": "db:macro_indicator_values", "as_of": as_of,
         "caliber": "库内最新期", "degrade_level": 3, "note": "通道不可用"},
    ]
    for sym in ("301580", "688626", "300718"):
        rows.append({"item": f"估值入场区 {sym}", "source": "technical_ma20_low60",
                     "as_of": as_of, "caliber": "technical_ma20_low60",
                     "degrade_level": 2, "note": "PE 百分位不可得,降级技术面支撑"})
    return rows


def _render() -> str:
    return render_report(
        snapshot_date="2026-09-24", cards=_cards(), forecast=_forecast(),
        candidates=_candidates(), declarations=_declarations(),
        events_future=[{"event_date": "2026-09-30", "level": "high", "name": "中国PMI",
                        "scope": "中国", "direction": "neutral"}],
        global_rows=[
            {"name": "道琼斯", "price": 51511.59, "change_pct": -0.68, "source_tag": "tencent_global"},
            {"name": "恒生指数", "price": 24697.7, "change_pct": -0.55, "source_tag": "tencent_global"},
        ],
        us_rows=[{"name": "道琼斯", "current": 51511.59, "change_pct": -0.68}],
        stage_errors=[],
    )


def test_no_english_enum_leakage():
    text = _render()
    hits = BLACKLIST.findall(text)
    assert not hits, f"英文残留: {hits}"


def test_direction_and_policy_translated():
    text = _render()
    assert "方向：看空" in text and "方向：中性" in text
    assert "简评：外围走弱" in text
    assert "收紧：60%→50%" in text


def test_sector_table_translated_and_formatted():
    text = _render()
    assert "模型+算法" in text and "纯算法" in text
    assert "+5.79%" in text and "-3.44%" in text  # 动量百分比两位小数
    assert "3/10" in text  # 置信度 x/10 刻度


def test_pick_table_two_decimals_and_action_consistency():
    text = _render()
    assert "44.70～55.27" in text  # 两位小数 + 波浪连接
    assert "45.99" in text and "63.90" in text
    assert "已出信号·关注买入区" in text  # 核验通过者的动作
    # 逐股明细只展开核验通过者
    assert text.count("### 爱迪特") == 1 and text.count("### 翔宇医疗") == 0


def test_valuation_caliber_translated():
    text = _render()
    assert "技术面支撑（20日线/60日低点）" in text
    assert "近似" in text and "degrade" not in text.lower()


def test_declarations_aggregated_and_gaps_merged():
    text = _render()
    assert "估值入场区（3 只）" in text
    assert text.count("PE 百分位不可得") + text.count("市盈率百分位不可得") <= 2  # 表+缺口各一处
    assert "09-24 10:09" in text  # 数据时点压缩
    assert "正常" in text and "缺失" in text  # 降级中文


def test_global_indexes_deduped_by_name():
    text = _render()
    assert text.count("| 道琼斯 |") == 1


def test_level_and_period_translated():
    assert "【高】" in _render()
    assert zh_period("2026Q2") == "2026中报" and zh_period("2026Q1") == "2026一季报"
    assert zh_period("2026-06-30") == "2026-06-30"  # 非季度格式回退


def test_free_text_sanitized():
    text = _render()
    assert "（近似）" in text          # degrade=2 → （近似）
    assert "低档" in text             # LOW → 低
    assert "（中）" in text           # (medium) → （中）
    assert "齐跌，纳指" in text       # 半角逗号 → 全角
    assert "-1.13%；技术面" in text   # 半角分号跟随百分号 → 全角
