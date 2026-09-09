"""因子权重 API(M5):只读列表 + 手动覆盖(pin / 设权重 / 开关自动标定)。

响应由 ResponseWrapperMiddleware 统一包成 {code,data,message},路由直接返回原始数据。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from src.modules.strategy.factor_weights import get_all_factor_weights, set_factor_weight
from src.platform.persistence.database import get_db

router = APIRouter()


class FactorWeightUpdate(BaseModel):
    """手动覆盖入参,均可选(只传要改的字段)。"""

    weight: float | None = None
    is_pinned: bool | None = None
    auto_calibrate: bool | None = None


@router.get("/weights")
def list_weights(db: Session = Depends(get_db)):
    """列出所有市场 × 因子的权重 + 最近 IC/IR 观测。"""
    return {"items": get_all_factor_weights(db=db)}


@router.post("/weights/{factor_code}/{market}")
def update_weight(
    factor_code: str, market: str, payload: FactorWeightUpdate,
    db: Session = Depends(get_db),
):
    """手动覆盖某因子权重 / pin / 开关自动标定(权重变化写 manual 审计)。"""
    try:
        return set_factor_weight(
            factor_code, market,
            weight=payload.weight, is_pinned=payload.is_pinned,
            auto_calibrate=payload.auto_calibrate, db=db,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
