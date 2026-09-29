#!/usr/bin/env python3
"""一次性修复策略信号身份漂移(机会页卡片失真)。

根因: entry_candidates 曾用"先删后插"刷新且表无 AUTOINCREMENT → SQLite 复用
rowid,同一候选 id 跨代指向不同股票;策略信号旧行按 (candidate_id, strategy_code)
复用且不刷新 symbol/name → A 股票的价格被写进 B 股票的信号行。

检测: 信号行的 stock_symbol/stock_market 与其 source_candidate_id 指向的候选行
(同 snapshot_date)不一致 → 身份失真。价格归属无法追溯,直接删除(宁可缺失不可失真)。
无候选可 JOIN 的历史行无法判定,保守保留。

用法:
    python scripts/repair_signal_identity.py            # 只读检测,打印报告
    python scripts/repair_signal_identity.py --apply    # 执行删除(幂等,可重复跑)
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import and_, or_  # noqa: E402

from src.platform.persistence.database import SessionLocal  # noqa: E402
from src.platform.persistence.models import (  # noqa: E402
    EntryCandidate,
    StrategyFactorSnapshot,
    StrategyOutcome,
    StrategySignalRun,
)

SAMPLE_LIMIT = 20


def find_corrupted(db) -> list:
    return (
        db.query(
            StrategySignalRun.id,
            StrategySignalRun.snapshot_date,
            StrategySignalRun.stock_symbol,
            StrategySignalRun.stock_name,
            EntryCandidate.stock_symbol.label("candidate_symbol"),
        )
        .join(
            EntryCandidate,
            and_(
                EntryCandidate.id == StrategySignalRun.source_candidate_id,
                EntryCandidate.snapshot_date == StrategySignalRun.snapshot_date,
            ),
        )
        .filter(
            or_(
                EntryCandidate.stock_symbol != StrategySignalRun.stock_symbol,
                EntryCandidate.stock_market != StrategySignalRun.stock_market,
            )
        )
        .order_by(StrategySignalRun.snapshot_date.desc(), StrategySignalRun.id.desc())
        .all()
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="修复策略信号身份漂移(默认只读检测)")
    parser.add_argument("--apply", action="store_true", help="执行删除(默认只读)")
    args = parser.parse_args()

    db = SessionLocal()
    try:
        rows = find_corrupted(db)
        print(f"检测到身份失真的策略信号行: {len(rows)} 条")
        for r in rows[:SAMPLE_LIMIT]:
            print(
                f"  signal id={r.id} snapshot={r.snapshot_date} "
                f"symbol={r.stock_symbol} name={r.stock_name} -> 候选行实际是 {r.candidate_symbol}"
            )
        if len(rows) > SAMPLE_LIMIT:
            print(f"  ... 其余 {len(rows) - SAMPLE_LIMIT} 条略")
        if not rows:
            print("无需修复。")
            return 0
        if not args.apply:
            print("只读模式,未做任何修改;加 --apply 执行删除。")
            return 0

        ids = [int(r.id) for r in rows]
        # StrategyOutcome 的 stock_symbol 继承自失真信号行,同属垃圾数据;
        # SQLite 默认不启用外键 CASCADE,显式删除三个从表保证无孤儿。
        deleted_outcomes = (
            db.query(StrategyOutcome)
            .filter(StrategyOutcome.signal_run_id.in_(ids))
            .delete(synchronize_session=False)
        )
        deleted_factors = (
            db.query(StrategyFactorSnapshot)
            .filter(StrategyFactorSnapshot.signal_run_id.in_(ids))
            .delete(synchronize_session=False)
        )
        deleted_signals = (
            db.query(StrategySignalRun)
            .filter(StrategySignalRun.id.in_(ids))
            .delete(synchronize_session=False)
        )
        db.commit()
        print(
            f"已删除失真信号行 {deleted_signals} 条、后验结果 {deleted_outcomes} 条、"
            f"因子快照 {deleted_factors} 条。"
        )
        print("建议触发一次策略信号刷新重建当日快照。")
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
