"""首页 Phase A:今日提醒命中聚合 + 组合待办(空态用)。"""

from __future__ import annotations

from datetime import datetime, timezone

from src.web import models as M
from src.web.api import accounts as accounts_api
from src.web.api import price_alerts as alerts_api


def _seed(s):
    acc = M.Account(name="测试", available_funds=1000, enabled=True)
    s.add(acc)
    s.flush()
    mt = M.Stock(symbol="600519", name="贵州茅台", market="CN")
    pa = M.Stock(symbol="000001", name="平安银行", market="CN")
    s.add_all([mt, pa])
    s.flush()
    s.add(M.Position(account_id=acc.id, stock_id=mt.id, cost_price=1700, quantity=100))
    s.add(M.Position(account_id=acc.id, stock_id=pa.id, cost_price=10, quantity=1000))
    rule = M.PriceAlertRule(stock_id=mt.id, name="茅台破位", enabled=True)  # 仅茅台有提醒
    s.add(rule)
    s.flush()
    s.commit()
    return acc, mt, pa, rule


def test_todos_flags_holding_without_alert(db):
    """持仓但未设提醒的标的应进待办;已设提醒的不进。"""
    _, mt, pa, _ = _seed(db)
    res = accounts_api.portfolio_todos(db=db)
    msgs = [t["message"] for t in res["todos"]]
    assert any("平安银行" in m for m in msgs), msgs
    assert not any("贵州茅台" in m for m in msgs), msgs


def test_today_hits_aggregate_with_stock_name(db):
    """今日命中跨规则聚合,带标的名/代码。"""
    _, mt, pa, rule = _seed(db)
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    db.add(
        M.PriceAlertHit(
            rule_id=rule.id,
            stock_id=mt.id,
            trigger_time=now,
            trigger_bucket="bkt1",
            trigger_snapshot={"current_price": 1650},
        )
    )
    db.commit()
    res = alerts_api.list_today_hits(db=db)
    assert len(res) == 1
    assert res[0]["symbol"] == "600519"
    assert res[0]["name"] == "贵州茅台"
    assert res[0]["rule_name"] == "茅台破位"


def test_today_hits_excludes_old(db):
    """昨天及更早的命中不计入今日。"""
    _, mt, pa, rule = _seed(db)
    old = datetime(2020, 1, 1)
    db.add(
        M.PriceAlertHit(
            rule_id=rule.id, stock_id=mt.id, trigger_time=old, trigger_bucket="old", trigger_snapshot={}
        )
    )
    db.commit()
    assert alerts_api.list_today_hits(db=db) == []
