"""_single_quarter_profit_yoy 单季差分单测(纯函数,离线)。"""

from __future__ import annotations

import sys

from src.modules.automation.premarket_pipeline.stages import _single_quarter_profit_yoy


def test_h1_latest_diffs_against_prev_year_same_quarter():
    profit = {
        "20250331": 132978099.51, "20250630": 290350350.87,
        "20250930": 405736428.26, "20251231": 557866868.69,
        "20260331": 146820961.69, "20260630": 261638901.95,
    }
    yoy = _single_quarter_profit_yoy(profit, [])
    # 2026Q2 单季 = H1-Q1 = 114,817,940.26;2025Q2 单季 = 157,372,251.36
    assert yoy is not None and abs(yoy - (114817940.26 - 157372251.36) / 157372251.36) < 1e-9
    assert yoy < 0  # 单季下滑 → 核验侧判 FAIL


def test_q1_latest_uses_cumulative_directly():
    profit = {
        "20250331": 100.0, "20250630": 200.0, "20250930": 300.0, "20251231": 400.0,
        "20260331": 150.0,
    }
    yoy = _single_quarter_profit_yoy(profit, [])
    assert yoy is not None and abs(yoy - (150.0 - 100.0) / 100.0) < 1e-9


def test_negative_prev_year_base_uses_abs_denominator():
    profit = {
        "20250331": -26939053.18, "20250630": -38162293.77,
        "20250930": -91644996.27, "20251231": -130044037.83,
        "20260331": -5629607.33, "20260630": 22698467.32,
    }
    yoy = _single_quarter_profit_yoy(profit, [])
    now = 22698467.32 - (-5629607.33)
    prev = -38162293.77 - (-26939053.18)
    assert yoy is not None and abs(yoy - (now - prev) / abs(prev)) < 1e-9
    assert yoy > 0  # 负基数转正 → 同比为正


def test_missing_prev_year_same_quarter_returns_none():
    profit = {
        "20260331": 100.0, "20260630": 250.0,  # 无 2025 年同季
        "20250930": 300.0, "20251231": 400.0, "20250630": 200.0,
    }
    # 重排后最新期 20260630,同季 2025 历史存在(20250630) → 可得;改造成真缺同季
    profit2 = {
        "20260331": 100.0, "20260630": 250.0,
        "20250331": 90.0, "20250630": 200.0,
    }
    assert _single_quarter_profit_yoy(profit2, []) is None  # 仅 4 期 < 5


def test_insufficient_periods_returns_none():
    assert _single_quarter_profit_yoy({"20260331": 1.0, "20260630": 2.0}, []) is None
    assert _single_quarter_profit_yoy({}, []) is None


def test_q1_latest_without_prev_year_returns_none():
    profit = {"20260331": 150.0, "20251231": 400.0, "20250930": 300.0}
    assert _single_quarter_profit_yoy(profit, []) is None  # <5 项
