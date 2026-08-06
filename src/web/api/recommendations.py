"""推荐相关 API（入场候选榜）。"""

from datetime import datetime, timezone
import logging
import threading

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from src.core.entry_candidates import (
    evaluate_entry_candidate_outcomes,
    get_entry_candidate_stats,
    list_entry_candidates,
    refresh_entry_candidates,
    save_entry_candidate_feedback,
)
from src.core.strategy_catalog import list_strategy_catalog
from src.core.strategy_engine import (
    evaluate_strategy_outcomes,
    get_strategy_factor_snapshot,
    get_strategy_stats,
    list_market_regime_snapshots,
    list_portfolio_risk_snapshots,
    list_strategy_signals,
    list_strategy_weight_history,
    rebalance_strategy_weights,
    refresh_strategy_signals,
)
from src.core.factor_eval import evaluate_factor_ic
from src.core.signal_explain import enrich_signal
from src.web.database import SessionLocal
from src.web.models import StrategySignalRun

router = APIRouter()
logger = logging.getLogger(__name__)

_refresh_state_lock = threading.Lock()
_refresh_state = {
    "running": False,
    "started_at": "",
    "finished_at": "",
    "last_error": "",
    "last_snapshot_date": "",
}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _latest_strategy_snapshot() -> str:
    db = SessionLocal()
    try:
        row = (
            db.query(StrategySignalRun.snapshot_date)
            .order_by(StrategySignalRun.snapshot_date.desc())
            .first()
        )
        return row[0] if row else ""
    finally:
        db.close()


def _set_refresh_state(**kwargs):
    with _refresh_state_lock:
        _refresh_state.update(kwargs)


def _get_refresh_state() -> dict:
    with _refresh_state_lock:
        return dict(_refresh_state)


def _refresh_worker(
    *,
    snapshot_date: str,
    rebuild_candidates: bool,
    max_inputs: int,
    market_scan_limit: int,
    max_kline_symbols: int,
    limit_candidates: int,
):
    _set_refresh_state(
        running=True,
        started_at=_now_iso(),
        finished_at="",
        last_error="",
    )
    try:
        result = refresh_strategy_signals(
            snapshot_date=snapshot_date,
            rebuild_candidates=rebuild_candidates,
            max_inputs=max_inputs,
            market_scan_limit=market_scan_limit,
            max_kline_symbols=max_kline_symbols,
            limit_candidates=limit_candidates,
        )
        _set_refresh_state(
            running=False,
            finished_at=_now_iso(),
            last_error="",
            last_snapshot_date=(result.get("snapshot_date") or ""),
        )
    except Exception as e:
        logger.exception("后台刷新策略信号失败: %s", e)
        _set_refresh_state(
            running=False,
            finished_at=_now_iso(),
            last_error=str(e),
        )


def _start_refresh_job(**kwargs) -> tuple[bool, dict]:
    with _refresh_state_lock:
        if _refresh_state.get("running"):
            return False, dict(_refresh_state)
        _refresh_state.update(
            running=True,
            started_at=_now_iso(),
            finished_at="",
            last_error="",
        )
    thread = threading.Thread(
        target=_refresh_worker,
        kwargs=kwargs,
        daemon=True,
        name="strategy-signals-refresh",
    )
    thread.start()
    return True, _get_refresh_state()


class CandidateFeedbackIn(BaseModel):
    snapshot_date: str = ""
    stock_symbol: str
    stock_market: str = "CN"
    useful: bool = True
    candidate_source: str = "watchlist"
    strategy_tags: list[str] = Field(default_factory=list)
    reason: str = ""


@router.get("/entry-candidates")
def get_entry_candidates(
    market: str = Query("", description="市场代码: CN/HK/US"),
    status: str = Query("active", description="状态: active/inactive/all"),
    min_score: float = Query(0, ge=0, le=100),
    limit: int = Query(20, ge=1, le=500),
    refresh: bool = Query(False, description="是否先刷新候选再返回"),
    snapshot_date: str = Query("", description="快照日期 YYYY-MM-DD，默认最新"),
    source: str = Query("", description="来源: market_scan/watchlist/mixed/all"),
    holding: str = Query("", description="持仓过滤: held/unheld/all"),
    strategy: str = Query("", description="策略标签过滤"),
    trend: str = Query("", description="技术形态: 多头排列/均线交织/空头排列"),
    macd: str = Query("", description="MACD: 金叉/死叉"),
    rsi: str = Query("", description="RSI: 超买/偏强/中性"),
    kdj: str = Query("", description="KDJ: 金叉/死叉"),
    volume_ratio_min: float = Query(0, ge=0, description="量比下限"),
    change_pct_min: float | None = Query(None, description="涨跌幅下限(%)"),
    change_pct_max: float | None = Query(None, description="涨跌幅上限(%)"),
):
    if refresh:
        refresh_entry_candidates()
    return list_entry_candidates(
        market=market,
        status=status,
        min_score=min_score,
        limit=limit,
        snapshot_date=snapshot_date,
        source=source,
        holding=holding,
        strategy=strategy,
        trend=trend,
        macd=macd,
        rsi=rsi,
        kdj=kdj,
        volume_ratio_min=volume_ratio_min,
        change_pct_min=change_pct_min,
        change_pct_max=change_pct_max,
    )


@router.post("/entry-candidates/refresh")
def refresh_candidates(
    max_inputs: int = Query(300, ge=10, le=1000),
    market_scan_limit: int = Query(60, ge=20, le=300),
):
    cand = refresh_entry_candidates(
        max_inputs=max_inputs,
        market_scan_limit=market_scan_limit,
    )
    # 同步刷新策略信号层，保持前端机会页一致。
    refresh_strategy_signals(
        snapshot_date=cand.get("snapshot_date", ""),
        rebuild_candidates=False,
    )
    return cand


@router.post("/entry-candidates/feedback")
def submit_candidate_feedback(payload: CandidateFeedbackIn):
    ok = save_entry_candidate_feedback(
        snapshot_date=payload.snapshot_date,
        stock_symbol=payload.stock_symbol,
        stock_market=payload.stock_market,
        useful=payload.useful,
        candidate_source=payload.candidate_source,
        strategy_tags=payload.strategy_tags,
        reason=payload.reason,
    )
    return {"ok": ok}


@router.get("/entry-candidates/stats")
def candidate_stats(days: int = Query(30, ge=1, le=365)):
    return get_entry_candidate_stats(days=days)


@router.post("/entry-candidates/outcomes/evaluate")
def evaluate_candidate_outcomes(
    limit: int = Query(400, ge=20, le=2000),
    snapshot_days: int = Query(45, ge=7, le=365),
):
    return evaluate_entry_candidate_outcomes(
        horizons=(1, 3, 5, 10),
        snapshot_days=snapshot_days,
        limit=limit,
    )


@router.get("/strategy-catalog")
def get_strategy_catalog(enabled_only: bool = Query(True, description="仅返回启用策略")):
    return {"items": list_strategy_catalog(enabled_only=enabled_only)}


@router.get("/strategy-signals")
def get_strategy_signal_list(
    market: str = Query("", description="市场代码: CN/HK/US"),
    status: str = Query("all", description="状态: active/inactive/all"),
    min_score: float = Query(0, ge=0, le=100),
    limit: int = Query(50, ge=1, le=500),
    snapshot_date: str = Query("", description="快照日期 YYYY-MM-DD，默认最新"),
    source_pool: str = Query("", description="来源池: market_scan/watchlist/mixed/all"),
    holding: str = Query("", description="持仓过滤: held/unheld/all"),
    strategy_code: str = Query("", description="策略代码"),
    risk_level: str = Query("", description="风险等级: low/medium/high/all"),
    include_payload: bool = Query(False, description="是否返回完整 payload（默认否，提升性能）"),
    trend: str = Query("", description="技术形态: 多头排列/均线交织/空头排列"),
    macd: str = Query("", description="MACD: 金叉/死叉"),
    rsi: str = Query("", description="RSI: 超买/偏强/中性"),
    kdj: str = Query("", description="KDJ: 金叉/死叉"),
    volume_ratio_min: float = Query(0, ge=0, description="量比下限"),
    change_pct_min: float | None = Query(None, description="涨跌幅下限(%)"),
    change_pct_max: float | None = Query(None, description="涨跌幅上限(%)"),
    regime: str = Query("", description="市场情绪: bullish/bearish/neutral/all"),
    strategy_tag: str = Query("", description="策略标签: macd_golden/momentum/trend_follow 等"),
):
    result = list_strategy_signals(
        market=market,
        status=status,
        min_score=min_score,
        limit=limit,
        snapshot_date=snapshot_date,
        source_pool=source_pool,
        holding=holding,
        strategy_code=strategy_code,
        risk_level=risk_level,
        include_payload=include_payload,
        trend=trend,
        macd=macd,
        rsi=rsi,
        kdj=kdj,
        volume_ratio_min=volume_ratio_min,
        change_pct_min=change_pct_min,
        change_pct_max=change_pct_max,
        regime=regime,
        strategy_tag=strategy_tag,
    )
    # Phase 3: 注入 1-10 可解释评分 + 正负因子拆解
    for _it in result.get("items", []):
        enrich_signal(_it)
    return result


@router.get("/strategy-regimes")
def get_strategy_regimes(
    snapshot_date: str = Query("", description="快照日期 YYYY-MM-DD"),
    market: str = Query("", description="市场过滤: CN/HK/US"),
    limit: int = Query(100, ge=1, le=1000),
):
    return list_market_regime_snapshots(
        snapshot_date=snapshot_date,
        market=market,
        limit=limit,
    )


@router.get("/strategy-risk-snapshots")
def get_strategy_risk_snapshots(
    snapshot_date: str = Query("", description="快照日期 YYYY-MM-DD"),
    market: str = Query("", description="市场过滤: CN/HK/US"),
    limit: int = Query(100, ge=1, le=1000),
):
    return list_portfolio_risk_snapshots(
        snapshot_date=snapshot_date,
        market=market,
        limit=limit,
    )


@router.get("/strategy-factors/{signal_run_id}")
def get_strategy_factor(signal_run_id: int):
    return get_strategy_factor_snapshot(signal_run_id)


@router.post("/strategy-signals/refresh")
def refresh_strategy_signal_list(
    rebuild_candidates: bool = Query(True, description="是否先重算候选池"),
    snapshot_date: str = Query("", description="指定快照日期，不传则用最新"),
    max_inputs: int = Query(500, ge=20, le=2000),
    market_scan_limit: int = Query(80, ge=20, le=500),
    max_kline_symbols: int = Query(72, ge=0, le=300),
    limit_candidates: int = Query(2000, ge=50, le=10000),
    wait: bool = Query(False, description="是否同步等待刷新完成（默认后台执行）"),
):
    if wait:
        return refresh_strategy_signals(
            snapshot_date=snapshot_date,
            rebuild_candidates=rebuild_candidates,
            max_inputs=max_inputs,
            market_scan_limit=market_scan_limit,
            max_kline_symbols=max_kline_symbols,
            limit_candidates=limit_candidates,
        )

    started, state = _start_refresh_job(
        snapshot_date=snapshot_date,
        rebuild_candidates=rebuild_candidates,
        max_inputs=max_inputs,
        market_scan_limit=market_scan_limit,
        max_kline_symbols=max_kline_symbols,
        limit_candidates=limit_candidates,
    )
    latest_snapshot = _latest_strategy_snapshot()
    return {
        "queued": True,
        "running": True,
        "accepted": bool(started),
        "message": "已提交后台执行" if started else "刷新任务已在执行中",
        "snapshot_date": latest_snapshot or state.get("last_snapshot_date") or "",
        "count": 0,
        "items": [],
    }


@router.get("/strategy-signals/refresh-status")
def strategy_signal_refresh_status():
    state = _get_refresh_state()
    latest_snapshot = _latest_strategy_snapshot()
    return {
        "running": bool(state.get("running")),
        "started_at": state.get("started_at") or "",
        "finished_at": state.get("finished_at") or "",
        "last_error": state.get("last_error") or "",
        "last_snapshot_date": latest_snapshot or state.get("last_snapshot_date") or "",
    }


@router.post("/strategy-signals/outcomes/evaluate")
def evaluate_strategy_signal_outcomes(
    limit: int = Query(800, ge=20, le=5000),
    snapshot_days: int = Query(60, ge=7, le=365),
):
    return evaluate_strategy_outcomes(
        horizons=(1, 3, 5, 10),
        snapshot_days=snapshot_days,
        limit=limit,
    )


@router.post("/strategy-weights/rebalance")
def rebalance_strategy_weights_api(
    window_days: int = Query(45, ge=7, le=365),
    min_samples: int = Query(8, ge=3, le=500),
    alpha: float = Query(0.35, ge=0.05, le=0.95),
):
    return rebalance_strategy_weights(
        window_days=window_days,
        min_samples=min_samples,
        alpha=alpha,
        regime="default",
    )


@router.get("/strategy-stats")
def strategy_stats(days: int = Query(45, ge=1, le=365)):
    return get_strategy_stats(days=days)


@router.get("/strategy-factor-ic")
def strategy_factor_ic(
    days: int = Query(90, ge=7, le=365, description="回看快照天数"),
    horizon: int = Query(5, ge=1, le=60, description="持有期(交易日)"),
):
    """各因子的 IC/IR 有效性评估(StrategyFactorSnapshot × StrategyOutcome)。"""
    return evaluate_factor_ic(days=days, horizon=horizon)


@router.get("/strategy-weight-history")
def strategy_weight_history(
    strategy_code: str = Query("", description="策略代码过滤"),
    market: str = Query("", description="市场过滤"),
    limit: int = Query(200, ge=1, le=2000),
):
    return list_strategy_weight_history(
        strategy_code=strategy_code,
        market=market,
        limit=limit,
    )
