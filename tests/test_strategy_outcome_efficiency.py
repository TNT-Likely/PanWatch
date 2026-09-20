from datetime import date


def test_strategy_outcome_horizons_skip_completed_and_future_rows():
    """策略结果已存在或尚未到期时，不应为该标的加载 K 线。"""
    from src.modules.strategy.strategy_engine import _pending_due_horizons

    pending, skipped_not_due = _pending_due_horizons(
        signal_id=7,
        snapshot_day=date(2026, 9, 18),
        today=date(2026, 9, 20),
        horizons=(1, 3, 5, 10),
        existing={(7, 1)},
    )

    assert pending == []
    assert skipped_not_due == 3
