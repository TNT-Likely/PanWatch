"""盘前报告 术语中文映射(单一事实源:report.py/render_notify 共用)。

约定:LLM JSON 的枚举值(direction/tighten 等)保持英文以稳定 schema,
翻译一律发生在渲染层;未知值回退原样输出(绝不编造翻译)。
"""

from __future__ import annotations

#: 方向枚举 → 中文
DIRECTION = {"bullish": "看多", "bearish": "看空", "neutral": "中性"}

#: 仓位政策调整方向 → 中文
POLICY_DIRECTION = {"tighten": "收紧", "relax": "放松", "hold": "维持"}

#: 三卡 position_policy 字段名 → 中文列名
FIELD_LABEL = {"review": "简评", "action": "操作建议"}

#: 数据来源/口径 → 中文(未知值回退原样)
SOURCE_ZH = {
    "db:event_calendar_items": "库内·事件日历",
    "db:macro_indicator_values": "库内·宏观指标",
    "marketdata.global_markets": "全球指数通道",
    "marketdata.index_quotes(tencent)": "隔夜美股（腾讯）",
    "sector_snapshot_db": "板块快照库（同花顺降级）",
    "llm_sector_fix": "模型修正",
    "llm_macro_cards": "模型三卡",
    "technical_ma20_low60": "技术面支撑（20日线/60日低点）",
    "stock_a_indicator_lg": "乐咕估值历史",
}

#: 板块预测来源枚举 → 中文
SECTOR_SOURCE = {"llm+algorithm": "模型+算法", "algorithm": "纯算法", "llm": "模型"}

#: 降级级别 → 中文
DEGRADE = {0: "正常", 1: "备源", 2: "近似", 3: "缺失"}

#: 事件等级 → 中文
LEVEL = {"high": "高", "medium": "中", "low": "低"}

#: 报告期 → 中文
PERIOD_SUFFIX = {"Q1": "一季报", "Q2": "中报", "Q3": "三季报", "Q4": "年报"}


def zh_direction(value) -> str:
    return DIRECTION.get(str(value or "").strip().lower(), "中性")


def zh_policy_direction(value) -> str:
    return POLICY_DIRECTION.get(str(value or "").strip().lower(), str(value or ""))


def zh_source(value) -> str:
    key = str(value or "").strip()
    return SOURCE_ZH.get(key, key)


def zh_sector_source(value) -> str:
    return SECTOR_SOURCE.get(str(value or "").strip(), str(value or ""))


def zh_degrade(value) -> str:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return str(value or "")
    return DEGRADE.get(n, str(value))


def zh_level(value) -> str:
    return LEVEL.get(str(value or "").strip().lower(), str(value or ""))


def zh_period(value) -> str:
    """``2026Q2`` → ``2026中报``;非季度格式回退原值。"""
    text = str(value or "").strip()
    if len(text) == 6 and text[4:] in PERIOD_SUFFIX:
        return f"{text[:4]}{PERIOD_SUFFIX[text[4:]]}"
    return text


def zh_caliber(value) -> str:
    key = str(value or "").strip()
    return SOURCE_ZH.get(key, key.replace("T-1", "前一交易日"))
