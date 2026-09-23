"""多源数据交叉校验与数据血统(provenance)工具。

盘前决策引擎的地基约定(全仓通用约束):
- 外部接口失败必须 fail-soft(返回空 + 标注来源不可用),绝不编造数据;
- 多源可得时交叉校验,偏差超过容差标注 disputed;
- 落库的每个关键字段都带血统(source/as_of/caliber/degrade_level),事后可审计。

这里只放纯函数:不做 IO、不依赖 DB/网络,便于单测与各采集服务复用。
"""

from __future__ import annotations

import math
from typing import Any

#: 默认相对偏差容差:两源相对偏差 ≤ 5% 视为一致
DEFAULT_TOLERANCE = 0.05

# 零值保护:两源同号大值偏差用相对值,接近 0 时用绝对兜底,避免除零抖动
_EPS = 1e-9

# 降级级别(数字越大越差,与 provenance.degrade_level 对齐)
DEGRADE_CONSISTENT = 0  # 双源一致(或仅血统记录,无偏差概念)
DEGRADE_SINGLE_SOURCE = 1  # 单源可得,无法交叉验证
DEGRADE_DISPUTED = 2  # 多源偏差超容差,取值存疑
DEGRADE_MISSING = 3  # 所有源均不可得

STATUS_OK = "ok"
STATUS_DISPUTED = "disputed"
STATUS_MISSING = "missing"


def make_provenance(
    *,
    value: Any = None,
    source: str = "",
    as_of: str = "",
    caliber: str = "",
    degrade_level: int = DEGRADE_CONSISTENT,
) -> dict[str, Any]:
    """组装单字段数据血统。

    - value: 该字段最终采用值(可为 None 表示不可得)
    - source: 来源标识,如 "ak.stock_sector_fund_flow_rank" / "discovery.hot_boards"
    - as_of: 取数时点 ISO 字符串(源侧数据时点另有 caliber 说明)
    - caliber: 口径标注,如 "official"(涨停池含ST) / "self_counted"(自算不含ST) /
      "em_main"(东财主力净额) / "ths_total"(同花顺净额)
    - degrade_level: 降级级别,取值见本模块 DEGRADE_* 常量
    """
    return {
        "value": value,
        "source": str(source or ""),
        "as_of": str(as_of or ""),
        "caliber": str(caliber or ""),
        "degrade_level": int(degrade_level),
    }


def to_float(value: Any) -> float | None:
    """宽容转 float:None/空串/非数/NaN 一律 None,不抛异常。"""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        f = float(value)
    else:
        try:
            f = float(str(value).strip().replace(",", ""))
        except (TypeError, ValueError):
            return None
    if math.isnan(f) or math.isinf(f):
        return None
    return f


def _relative_deviation(a: float, b: float) -> float:
    """两值相对偏差:|a-b| / max(|a|,|b|)。两者都近 0 时视为 0 偏差。"""
    scale = max(abs(a), abs(b))
    if scale <= _EPS:
        return 0.0
    return abs(a - b) / scale


def cross_validate(
    values: list[dict],
    tol: float = DEFAULT_TOLERANCE,
) -> dict[str, Any]:
    """对一个字段的多个来源取值做交叉校验。

    Args:
        values: 每项至少含 ``value``(数值或 None)与 ``source``(来源标识),
            其余键(as_of/caliber 等)原样保留进结果。
        tol: 相对偏差容差(0.05 = 5%)。

    Returns:
        dict 含:
        - status: "ok" | "disputed" | "missing"
            - ok: ≥2 个有效值且最大相对偏差 ≤ tol;
            - disputed: ≥2 个有效值但偏差 > tol;
            - missing: 有效值 < 2(全部缺失,或仅单源无法交叉——调用方
              可自行采用主源值并标注 DEGRADE_SINGLE_SOURCE)。
        - sources: 各源明细列表 [{value, source, ...}](保留 None 项,便于审计哪些源缺了)。
        - deviation: 有效值间最大相对偏差(不可判定时为 None)。
        - reason: 机器可读结论("consistent"/"sources_disagree"/
          "insufficient_sources"/"no_valid_values")。
        - degrade_level: 与 status 对应的降级级别常量。
    """
    tol = max(0.0, float(tol))
    detail = [dict(item) for item in (values or [])]
    for item in detail:
        item["value"] = to_float(item.get("value"))

    valid = [item for item in detail if item["value"] is not None]

    if len(valid) < 2:
        if valid:
            # 仅单源:交叉校验不可得,记 missing;降级级别按单源(1)标注,
            # 调用方可用 pick_primary 采信主源值。
            degrade = DEGRADE_SINGLE_SOURCE
        else:
            degrade = DEGRADE_MISSING
        return {
            "status": STATUS_MISSING,
            "sources": detail,
            "deviation": None,
            "reason": "no_valid_values" if not valid else "insufficient_sources",
            "degrade_level": degrade,
        }

    numbers = [float(item["value"]) for item in valid]  # type: ignore[arg-type]
    deviation = max(
        _relative_deviation(numbers[i], numbers[j])
        for i in range(len(numbers))
        for j in range(i + 1, len(numbers))
    )
    if deviation <= tol:
        return {
            "status": STATUS_OK,
            "sources": detail,
            "deviation": round(deviation, 6),
            "reason": "consistent",
            "degrade_level": DEGRADE_CONSISTENT,
        }
    return {
        "status": STATUS_DISPUTED,
        "sources": detail,
        "deviation": round(deviation, 6),
        "reason": "sources_disagree",
        "degrade_level": DEGRADE_DISPUTED,
    }


def pick_primary(
    values: list[dict],
    verdict: dict[str, Any] | None = None,
) -> tuple[float | None, int]:
    """按交叉校验结论选取采用值与降级级别。

    约定:values 列表按来源优先级排序(主源在前)。
    - ok(双源一致)→ 取主源值,degrade=0;
    - disputed(偏差超容差)→ 仍取主源值但标注 degrade=2,由上层决定是否采信;
    - missing 且有单源值 → 取该值,degrade=1;
    - 全缺 → None,degrade=3。

    Returns:
        (adopted_value, degrade_level)
    """
    ordered = [dict(item) for item in (values or [])]
    for item in ordered:
        item["value"] = to_float(item.get("value"))
    status = (verdict or {}).get("status")

    if status == STATUS_OK:
        primary = next((item["value"] for item in ordered if item["value"] is not None), None)
        return primary, DEGRADE_CONSISTENT
    if status == STATUS_DISPUTED:
        primary = next((item["value"] for item in ordered if item["value"] is not None), None)
        return primary, DEGRADE_DISPUTED

    available = [item for item in ordered if item["value"] is not None]
    if available:
        return available[0]["value"], DEGRADE_SINGLE_SOURCE
    return None, DEGRADE_MISSING
