"""行业数据 API:行业预测查询(GET /sectors/predictions)与预测命中率校准
(GET /sectors/predictions/hit-rate)。

行业快照/动量等读接口按需在此扩展;业务规则在 sector_data_service 与
sector_prediction_hitrate(命中判定纯逻辑,与影子周报脚本共用)。
"""

import logging
from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from src.modules.market.sector_prediction_hitrate import (
    DEFAULT_TOP_N,
    UP_DIRECTIONS,
    aggregate_three_price_status,
    evaluate_sector_hit_rate,
)
from src.platform.persistence.database import get_db
from src.platform.persistence.models import (
    AgentPredictionOutcome,
    SectorPrediction,
    SectorSnapshot,
)

logger = logging.getLogger(__name__)
router = APIRouter()

#: hit-rate 缺省回看窗口(天):to 缺省取库内最新预测日,from 缺省再往前推这么多天
HIT_RATE_DEFAULT_WINDOW_DAYS = 30


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


def _parse_date_or_400(value: str | None, name: str) -> date | None:
    """YYYY-MM-DD 校验,非法抛 400(与既有 date 参数口径一致)。"""
    text = (value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise HTTPException(400, f"{name} 格式错误(需要 YYYY-MM-DD)") from None


@router.get("/predictions/hit-rate")
def sector_predictions_hit_rate(
    from_: Annotated[str | None, Query(alias="from")] = None,
    to: str | None = None,
    top_n: Annotated[int, Query(ge=1, le=10)] = DEFAULT_TOP_N,
    db: Session = Depends(get_db),
) -> dict:
    """预测命中率校准:逐 snapshot_date 取看多预测 top_n,对照同日快照涨幅 top_n。

    - 判定规则在 sector_prediction_hitrate(API 与影子周报脚本共用);
    - 三价触及率按 agent_prediction_outcomes(premarket_pipeline,horizon 1/3/5/10)
      聚合 outcome_status 分布;
    - from/to 缺省:to=库内最新预测日(无则今天),from=to 前 30 天。
    """
    # 延迟导入避免 market 域在模块加载期依赖 automation 域配置
    from src.modules.automation import premarket_config as pcfg

    end_day = _parse_date_or_400(to, "to")
    start_day = _parse_date_or_400(from_, "from")
    if end_day is None:
        latest = (
            db.query(SectorPrediction.snapshot_date)
            .order_by(SectorPrediction.snapshot_date.desc())
            .first()
        )
        end_day = (
            date.fromisoformat(latest[0])
            if latest and latest[0]
            else date.today()
        )
    if start_day is None:
        start_day = end_day - timedelta(days=HIT_RATE_DEFAULT_WINDOW_DAYS)
    if start_day > end_day:
        raise HTTPException(400, "from 不能晚于 to")

    start, end = start_day.isoformat(), end_day.isoformat()
    preds = (
        db.query(SectorPrediction)
        .filter(
            SectorPrediction.snapshot_date >= start,
            SectorPrediction.snapshot_date <= end,
        )
        .all()
    )
    snaps = (
        db.query(SectorSnapshot)
        .filter(
            SectorSnapshot.snapshot_date >= start,
            SectorSnapshot.snapshot_date <= end,
        )
        .all()
    )
    outcomes = (
        db.query(AgentPredictionOutcome)
        .filter(
            AgentPredictionOutcome.agent_name == pcfg.AGENT_NAME,
            AgentPredictionOutcome.prediction_date >= start,
            AgentPredictionOutcome.prediction_date <= end,
        )
        .all()
    )

    result = evaluate_sector_hit_rate(preds, snaps, top_n=top_n)
    result["window"] = {"from": start, "to": end}
    result["top_n"] = top_n
    result["direction_set"] = sorted(UP_DIRECTIONS)
    result["three_price"] = {
        "agent_name": pcfg.AGENT_NAME,
        **aggregate_three_price_status(outcomes, pcfg.PREDICTION_HORIZONS),
    }
    return result
