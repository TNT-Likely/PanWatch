"""行业数据 API:行业预测查询(GET /sectors/predictions)。

行业快照/动量等读接口按需在此扩展;业务规则在 sector_data_service。
"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from src.platform.persistence.database import get_db
from src.platform.persistence.models import SectorPrediction, SectorSnapshot

logger = logging.getLogger(__name__)
router = APIRouter()


def _to_response(row: SectorPrediction) -> dict:
    return {
        "id": row.id,
        "snapshot_date": row.snapshot_date,
        "board_code": row.board_code,
        "board_name": row.board_name or "",
        "market": row.market or "CN",
        "direction": row.direction or "",
        "confidence": row.confidence,
        "stage": row.stage or "",
        "momentum_score": row.momentum_score,
        "rationale": row.rationale or "",
        "catalysts": row.catalysts or [],
        "meta": row.meta or {},
        "source_agent": row.source_agent or "",
        "created_at": row.created_at.isoformat() if row.created_at else "",
        "updated_at": row.updated_at.isoformat() if row.updated_at else "",
    }


@router.get("/predictions")
def list_sector_predictions(date: str | None = None, db: Session = Depends(get_db)):
    """按日期列出行业预测;date 缺省取库内最新快照日。

    响应带 requested_date(显式或解析出的最新日)与 predictions 列表。
    """
    if date:
        target = (date or "").strip()
        if len(target) != 10:
            raise HTTPException(400, "date 格式错误(需要 YYYY-MM-DD)")
    else:
        latest = (
            db.query(SectorPrediction.snapshot_date)
            .order_by(SectorPrediction.snapshot_date.desc())
            .first()
        )
        target = latest[0] if latest else ""
        if not target:
            return {"requested_date": "", "predictions": []}
    rows = (
        db.query(SectorPrediction)
        .filter(SectorPrediction.snapshot_date == target)
        .order_by(
            SectorPrediction.momentum_score.desc().nullslast(),
            SectorPrediction.board_code.asc(),
        )
        .all()
    )
    return {"requested_date": target, "predictions": [_to_response(r) for r in rows]}


@router.get("/snapshots")
def list_sector_snapshots(date: str | None = None, db: Session = Depends(get_db)):
    """按日期列出行业快照;date 缺省取库内最新快照日(供盘前决策与诊断)。"""
    if date:
        target = (date or "").strip()
        if len(target) != 10:
            raise HTTPException(400, "date 格式错误(需要 YYYY-MM-DD)")
    else:
        latest = (
            db.query(SectorSnapshot.snapshot_date)
            .order_by(SectorSnapshot.snapshot_date.desc())
            .first()
        )
        target = latest[0] if latest else ""
        if not target:
            return {"requested_date": "", "snapshots": []}
    rows = (
        db.query(SectorSnapshot)
        .filter(SectorSnapshot.snapshot_date == target)
        .order_by(SectorSnapshot.rank.asc().nullslast())
        .all()
    )
    return {
        "requested_date": target,
        "snapshots": [
            {
                "id": r.id,
                "snapshot_date": r.snapshot_date,
                "board_code": r.board_code,
                "board_name": r.board_name or "",
                "change_pct": r.change_pct,
                "turnover": r.turnover,
                "limit_up_count": r.limit_up_count,
                "limit_up_caliber": r.limit_up_caliber or "",
                "main_net_inflow": r.main_net_inflow,
                "small_net_inflow": r.small_net_inflow,
                "rank": r.rank,
                "meta": r.meta or {},
            }
            for r in rows
        ],
    }
