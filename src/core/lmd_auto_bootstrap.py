"""自选股老马视角报告自动补全 — 无报告则后台排队生成。"""

from __future__ import annotations

import asyncio
import logging
import queue
import threading
import time
from types import SimpleNamespace
from typing import Any

from sqlalchemy.orm import Session

from src.web.database import SessionLocal
from src.web.models import AgentConfig, AnalysisHistory, Stock

logger = logging.getLogger(__name__)

LMD_AGENT = "lmd_outlook"

_in_flight: set[str] = set()
_in_flight_lock = threading.Lock()
_task_queue: queue.Queue[tuple[Any, bool] | None] = queue.Queue()
_worker_lock = threading.Lock()
_worker_thread: threading.Thread | None = None


def _auto_bootstrap_enabled(db: Session) -> bool:
    agent = db.query(AgentConfig).filter(AgentConfig.name == LMD_AGENT).first()
    if not agent:
        return True
    cfg = agent.config if isinstance(agent.config, dict) else {}
    auto = cfg.get("auto_bootstrap") or {}
    if isinstance(auto, dict) and "enabled" in auto:
        return bool(auto["enabled"])
    return True


def _suppress_notify_default(db: Session) -> bool:
    agent = db.query(AgentConfig).filter(AgentConfig.name == LMD_AGENT).first()
    if not agent:
        return True
    cfg = agent.config if isinstance(agent.config, dict) else {}
    auto = cfg.get("auto_bootstrap") or {}
    if isinstance(auto, dict) and "suppress_notify" in auto:
        return bool(auto["suppress_notify"])
    return True


def has_lmd_report(db: Session, stock_symbol: str) -> bool:
    """该标的是否已有任意一条老马视角分析记录。"""
    sym = (stock_symbol or "").strip()
    if not sym:
        return False
    return (
        db.query(AnalysisHistory.id)
        .filter(
            AnalysisHistory.agent_name == LMD_AGENT,
            AnalysisHistory.stock_symbol == sym,
        )
        .first()
        is not None
    )


def _stock_payload(stock: Any) -> SimpleNamespace:
    return SimpleNamespace(
        id=int(getattr(stock, "id", 0) or 0),
        symbol=str(getattr(stock, "symbol", "") or "").strip(),
        name=str(getattr(stock, "name", "") or "").strip(),
        market=str(getattr(stock, "market", "CN") or "CN").strip().upper() or "CN",
        security_type=str(getattr(stock, "security_type", None) or "stock"),
    )


def is_lmd_in_flight(symbol: str) -> bool:
    """该标的是否正在生成老马视角报告（含自动补全与手动触发）。"""
    sym = (symbol or "").strip()
    if not sym:
        return False
    with _in_flight_lock:
        return sym in _in_flight


def try_acquire_lmd_generation(symbol: str) -> bool:
    """尝试占用生成槽位；已在生成中则返回 False。"""
    sym = (symbol or "").strip()
    if not sym:
        return False
    with _in_flight_lock:
        if sym in _in_flight:
            return False
        _in_flight.add(sym)
        return True


def release_lmd_generation(symbol: str) -> None:
    """释放生成槽位（任务结束或失败时调用）。"""
    sym = (symbol or "").strip()
    if not sym:
        return
    with _in_flight_lock:
        _in_flight.discard(sym)


def _try_mark_in_flight(symbol: str) -> bool:
    return try_acquire_lmd_generation(symbol)


def _clear_in_flight(symbol: str) -> None:
    release_lmd_generation(symbol)


def _ensure_worker() -> None:
    global _worker_thread
    with _worker_lock:
        if _worker_thread and _worker_thread.is_alive():
            return
        _worker_thread = threading.Thread(
            target=_worker_loop,
            name="lmd-outlook-bootstrap",
            daemon=True,
        )
        _worker_thread.start()


def _worker_loop() -> None:
    while True:
        item = _task_queue.get()
        try:
            if item is None:
                return
            stock, suppress_notify = item
            symbol = getattr(stock, "symbol", "") or ""
            try:
                asyncio.run(_run_lmd_trigger(stock, suppress_notify=suppress_notify))
                logger.info("[lmd_bootstrap] 老马视角报告已生成 - %s", symbol)
            except Exception:
                logger.exception("[lmd_bootstrap] 老马视角报告生成失败 - %s", symbol)
            finally:
                _clear_in_flight(symbol)
        finally:
            _task_queue.task_done()


async def _run_lmd_trigger(stock: Any, *, suppress_notify: bool) -> None:
    from server import trigger_agent_for_stock

    symbol = getattr(stock, "symbol", "") or ""
    trace_id = f"auto-lmd-{symbol}-{int(time.time() * 1000)}"
    await trigger_agent_for_stock(
        LMD_AGENT,
        stock,
        stock_agent_id=None,
        bypass_throttle=True,
        bypass_market_hours=True,
        suppress_notify=suppress_notify,
        trace_id=trace_id,
    )


def ensure_lmd_report(stock: Any, *, suppress_notify: bool | None = None) -> dict:
    """若该自选股尚无老马视角报告，则加入后台生成队列。

    Returns:
        {has_report, queued, deduplicated, message}
    """
    payload = _stock_payload(stock)
    if not payload.symbol:
        return {
            "has_report": False,
            "queued": False,
            "deduplicated": False,
            "message": "无效股票代码",
        }

    db = SessionLocal()
    try:
        if not _auto_bootstrap_enabled(db):
            return {
                "has_report": has_lmd_report(db, payload.symbol),
                "queued": False,
                "deduplicated": False,
                "message": "老马视角自动补全已关闭",
            }
        if has_lmd_report(db, payload.symbol):
            return {
                "has_report": True,
                "queued": False,
                "deduplicated": False,
                "message": "已有老马视角报告",
            }
        notify = (
            _suppress_notify_default(db)
            if suppress_notify is None
            else bool(suppress_notify)
        )
    finally:
        db.close()

    if not _try_mark_in_flight(payload.symbol):
        return {
            "has_report": False,
            "queued": False,
            "deduplicated": True,
            "message": "老马视角报告生成中",
        }

    _ensure_worker()
    _task_queue.put((payload, notify))
    logger.info("[lmd_bootstrap] 已排队老马视角报告 - %s", payload.symbol)
    return {
        "has_report": False,
        "queued": True,
        "deduplicated": False,
        "message": "已提交老马视角报告生成",
    }


def bootstrap_all_missing_stocks() -> int:
    """扫描全部自选股，为缺失老马报告的标的排队生成。返回新排队数量。"""
    db = SessionLocal()
    try:
        if not _auto_bootstrap_enabled(db):
            logger.info("[lmd_bootstrap] 自动补全已关闭，跳过启动扫描")
            return 0
        stocks = db.query(Stock).order_by(
            Stock.is_featured.desc(),
            Stock.sort_order.asc(),
            Stock.id.asc(),
        ).all()
    finally:
        db.close()

    queued = 0
    for stock in stocks:
        result = ensure_lmd_report(stock)
        if result.get("queued"):
            queued += 1
    if queued:
        logger.info("[lmd_bootstrap] 启动扫描：已为 %s 只自选股排队老马视角报告", queued)
    return queued
