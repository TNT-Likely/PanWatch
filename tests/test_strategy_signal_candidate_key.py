"""策略信号按业务键匹配既有行(issue #94)。

refresh_strategy_signals 原先按 source_candidate_id(候选行的易变代理 id)
匹配既有 StrategySignalRun:候选按快照重建时 id 会被回收到另一只股票上,
复用的信号行保留创建时的 stock_symbol/stock_name,却被写入另一只股票的
entry/stop/target 价格,导致 /api/recommendations/strategy-signals 返回
的数量级错位价格。本用例模拟 id 回收场景:重建后每只股票的信号行必须
携带自己的新价格,且 source_candidate_id 跟随当前候选行。
"""

from __future__ import annotations

SNAPSHOT = "2026-11-04"


def _candidate(cid, symbol, name, low, high, stop, target):
    from src.platform.persistence.models import EntryCandidate

    return EntryCandidate(
        id=cid, stock_symbol=symbol, stock_market="CN", stock_name=name,
        snapshot_date=SNAPSHOT, status="active", score=80.0, action="buy",
        action_label="建仓", candidate_source="watchlist", strategy_tags=[],
        entry_low=low, entry_high=high, stop_loss=stop, target_price=target,
    )


def _seed(rows):
    from src.platform.persistence.database import SessionLocal
    from src.platform.persistence.models import EntryCandidate

    db = SessionLocal()
    try:
        db.query(EntryCandidate).filter(
            EntryCandidate.snapshot_date == SNAPSHOT
        ).delete(synchronize_session=False)
        db.commit()
        for row in rows:
            db.add(row)
        db.commit()
    finally:
        db.close()


def _signals_by_symbol():
    from src.platform.persistence.database import SessionLocal
    from src.platform.persistence.models import StrategySignalRun

    db = SessionLocal()
    try:
        rows = (
            db.query(StrategySignalRun)
            .filter(StrategySignalRun.snapshot_date == SNAPSHOT)
            .all()
        )
        return {r.stock_symbol: r for r in rows}
    finally:
        db.close()


def _cleanup():
    from src.platform.persistence.database import SessionLocal
    from src.platform.persistence.models import EntryCandidate, StrategySignalRun

    db = SessionLocal()
    try:
        for model in (StrategySignalRun, EntryCandidate):
            db.query(model).filter(model.snapshot_date == SNAPSHOT).delete(
                synchronize_session=False
            )
        db.commit()
    finally:
        db.close()


def test_signal_rows_follow_business_key_when_candidate_ids_recycle():
    """候选重建后 id 互换归属,信号行仍按 (股票, 市场, 策略) 对齐价格。"""
    from src.modules.strategy.strategy_engine import refresh_strategy_signals

    _cleanup()
    try:
        # 首建:A(600000) 价格 ~10,id=101;B(000001) 价格 ~100,id=102。
        _seed([
            _candidate(101, "600000", "StockA", 9.5, 10.5, 9.0, 12.0),
            _candidate(102, "000001", "StockB", 95.0, 105.0, 90.0, 120.0),
        ])
        refresh_strategy_signals(snapshot_date=SNAPSHOT)
        first = _signals_by_symbol()
        assert first["600000"].entry_low == 9.5
        assert first["000001"].entry_low == 95.0

        # 重建:同快照内 id 回收互换 —— 101 变 B、102 变 A,价格各更新。
        _seed([
            _candidate(101, "000001", "StockB", 114.0, 126.0, 108.0, 144.0),
            _candidate(102, "600000", "StockA", 11.4, 12.6, 10.8, 14.4),
        ])
        refresh_strategy_signals(snapshot_date=SNAPSHOT)

        rows = _signals_by_symbol()
        assert len(rows) == 2
        a = rows["600000"]
        assert (a.entry_low, a.entry_high, a.stop_loss, a.target_price) == (
            11.4, 12.6, 10.8, 14.4,
        )
        assert a.source_candidate_id == 102
        b = rows["000001"]
        assert (b.entry_low, b.entry_high, b.stop_loss, b.target_price) == (
            114.0, 126.0, 108.0, 144.0,
        )
        assert b.source_candidate_id == 101
    finally:
        _cleanup()
