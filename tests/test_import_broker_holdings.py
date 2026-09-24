"""券商持仓导入脚本 纯逻辑单测(离线,不连库)。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.import_broker_holdings import (  # noqa: E402
    Holding,
    load_holdings,
    plan_import,
)


def _write(tmp_path, rows):
    p = tmp_path / "holdings.json"
    p.write_text(json.dumps({"holdings": rows}), encoding="utf-8")
    return str(p)


def test_load_and_normalize(tmp_path):
    p = _write(tmp_path, [
        {"code": "600276", "name": "恒瑞医药", "quantity": 200, "cost_price": 47.2231,
         "market_value": 8956.0},
        {"code": "002050", "name": "三花智控", "quantity": 100, "cost_price": 36.57},
    ])
    hs = load_holdings(p)
    assert len(hs) == 2
    assert hs[0].current_price == 44.78  # 市值/数量推导现价
    assert hs[1].current_price is None


def test_invalid_rows_dropped(tmp_path):
    p = _write(tmp_path, [
        {"code": "600276", "name": "正常", "quantity": 200, "cost_price": 47.22},
        {"code": "BK0475", "name": "板块代码非A股", "quantity": 100, "cost_price": 1.0},
        {"code": "600276", "name": "数量非法", "quantity": "200股", "cost_price": 47.22},
        {"code": "600276", "name": "成本为零", "quantity": 100, "cost_price": 0},
        {"code": "600276", "name": "负数量", "quantity": -100, "cost_price": 47.22},
    ])
    hs = load_holdings(p)
    assert len(hs) == 1 and hs[0].name == "正常"


def test_plan_skips_existing_position_and_paper():
    hs = [
        Holding("600276", "恒瑞医药", 200, 47.22),
        Holding("300124", "汇川技术", 200, 57.24),
        Holding("601168", "西部矿业", 200, 38.52),
    ]
    plan = plan_import(hs, existing_position_symbols={"600276"}, existing_paper_open_symbols={"300124"})
    assert [h.code for h in plan.to_create] == ["601168"]
    assert plan.skipped_positions == ["恒瑞医药(600276)"]
    assert plan.skipped_paper == ["汇川技术(300124)"]


def test_plan_all_new():
    hs = [Holding("600276", "恒瑞医药", 200, 47.22)]
    plan = plan_import(hs, set(), set())
    assert len(plan.to_create) == 1 and not plan.skipped_positions and not plan.skipped_paper
