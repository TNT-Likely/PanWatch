"""持仓数量精度规则：允许小于 1 的数量，最多保留 8 位小数。

持仓数量可能来自碎股、基金份额或按份额计价的品种，因此不能按整数处理。
这里集中定义校验与展示规则，HTTP 接口、配置包导入导出和文本输出共用同一套口径。
"""

from __future__ import annotations

import math
from decimal import Decimal, InvalidOperation

from pydantic_core import PydanticCustomError

MAX_QUANTITY_DECIMALS = 8
"""持仓数量最多支持的小数位数。"""


def quantity_decimals(value: float) -> int:
    """返回数量的小数位数（指数写法按实际值计算，例如 1e-08 记 8 位）。"""
    try:
        exponent = Decimal(str(value)).as_tuple().exponent
    except (InvalidOperation, ValueError):
        return MAX_QUANTITY_DECIMALS + 1
    return max(0, -int(exponent))


def _finite_quantity(value: float) -> float:
    """把数量转成有限浮点数，非法输入抛 ValueError。"""
    try:
        quantity = float(value)
    except (TypeError, ValueError) as exc:
        raise PydanticCustomError("quantity_not_a_number", "持仓数量必须是数字") from exc
    if not math.isfinite(quantity):
        raise PydanticCustomError("quantity_not_finite", "持仓数量必须是有限数字")
    return quantity


def validate_quantity_precision(value: float) -> float:
    """只校验数量精度：最多 8 位小数。

    配置包等“搬运已有数据”的场景只做精度校验，避免历史模板因为数量为 0
    或负数而整体无法导入。
    """
    quantity = _finite_quantity(value)
    if quantity_decimals(quantity) > MAX_QUANTITY_DECIMALS:
        raise PydanticCustomError(
            "quantity_too_precise",
            f"持仓数量最多支持 {MAX_QUANTITY_DECIMALS} 位小数",
        )
    return quantity


def validate_position_quantity(value: float) -> float:
    """校验持仓数量：必须是有限正数，且小数位数不超过 8 位。

    失败时抛 PydanticCustomError，调用方（Pydantic 校验器）会转成 422，
    错误信息直接是可读文案，不会带 “Value error, ” 前缀。
    """
    quantity = _finite_quantity(value)
    if quantity <= 0:
        raise PydanticCustomError("quantity_not_positive", "持仓数量必须大于 0")
    return validate_quantity_precision(quantity)


def normalize_quantity(value: object) -> float | int:
    """整数量保持整数返回，避免升级为浮点列后 JSON 出现 100.0。"""
    try:
        quantity = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0
    if not math.isfinite(quantity):
        return 0
    if quantity.is_integer():
        return int(quantity)
    return quantity


def format_quantity(value: object) -> str:
    """数量转成可读文本：整数不带小数，小数去掉多余的 0。"""
    try:
        quantity = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return "0"
    if not math.isfinite(quantity):
        return "0"
    if quantity.is_integer():
        return str(int(quantity))
    text = f"{quantity:.{MAX_QUANTITY_DECIMALS}f}"
    text = text.rstrip("0").rstrip(".")
    return text or "0"
