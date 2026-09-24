"""价格提醒引擎 事务顺序单测:通知必须在命中落库(commit)之后发生。

回归背景:原实现 flush(拿写锁)→通知(网络 30s/渠道)→commit,把 SQLite 单写锁
占住数十秒,饿死应用自身写入(2026-09-24 实测事故根因)。
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

import src.modules.market.price_alert_engine as eng_mod
from src.modules.market.price_alert_engine import (
    PriceAlertEngine,
    RuleEvalResult,
    _minute_bucket,
)
from src.platform.persistence.models import Base, PriceAlertHit, PriceAlertRule, Stock


@pytest.fixture
def session_factory(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path}/alert.db")

    @event.listens_for(engine, "connect")
    def _pragma(dbapi_conn, rec):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.close()

    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)


def _seed_rule(session_factory) -> int:
    s = session_factory()
    try:
        stock = Stock(symbol="600276", name="恒瑞医药", market="CN")
        s.add(stock)
        s.flush()
        rule = PriceAlertRule(
            stock_id=stock.id,
            name="测试规则",
            enabled=True,
            condition_group={"op": "and", "items": [{"type": "price", "op": "<=", "value": 100}]},
            repeat_mode="repeat",
        )
        s.add(rule)
        s.commit()
        return rule.id
    finally:
        s.close()


def _scan(session_factory, rule_id: int, notify_impl) -> dict:
    """跑一次 scan_once;notify_impl() 为通知桩,返回 (ok, err) 或抛异常。

    注意:必须 patch 模块级 ``eng_mod.SessionLocal``(scan_once 引用的是模块全局,
    而非实例属性)——否则测试会连到生产库 panwatch.db!
    """
    engine = PriceAlertEngine()

    async def fake_quotes(stocks):
        return {("CN", s.symbol): {"current_price": 50.0} for s in stocks}

    async def fake_eval(rule, quote):
        return RuleEvalResult(matched=True, hits=[], snapshot={"current_price": 50.0})

    async def fake_notify(db, rule, snapshot):
        return await notify_impl()

    engine._fetch_quotes_map = fake_quotes  # type: ignore[assignment]
    engine.eval_rule = fake_eval  # type: ignore[assignment]
    engine._send_notify = fake_notify  # type: ignore[assignment]
    eng_mod.SessionLocal = session_factory  # type: ignore[assignment]
    return asyncio.run(engine.scan_once(only_rule_id=rule_id, bypass_market_hours=True))


def test_hit_committed_before_notify(session_factory):
    """通知被调用时,命中行必须已 commit(第二连接可见)——写锁不在通知期间。"""
    rule_id = _seed_rule(session_factory)
    seen = {}

    async def notify():
        raw = session_factory()
        try:
            seen["row"] = raw.query(PriceAlertHit).filter_by(rule_id=rule_id).first()
        finally:
            raw.close()
        return True, ""

    result = _scan(session_factory, rule_id, notify)
    assert result["triggered"] == 1
    assert seen.get("row") is not None, "通知时命中行尚未 commit(仍在写锁内)"


def test_notify_exception_keeps_hit_and_records_error(session_factory):
    rule_id = _seed_rule(session_factory)

    async def boom():
        raise RuntimeError("渠道超时")

    result = _scan(session_factory, rule_id, boom)
    assert result["triggered"] == 1  # 扫描未中断
    s = session_factory()
    try:
        hit = s.query(PriceAlertHit).filter_by(rule_id=rule_id).one()
        assert hit.notify_success is False
        assert "渠道超时" in (hit.notify_error or "")
    finally:
        s.close()


def test_duplicate_minute_bucket_skipped_without_notify(session_factory):
    rule_id = _seed_rule(session_factory)
    now = datetime.now(timezone.utc)
    s = session_factory()
    try:
        stock = s.query(Stock).first()
        s.add(PriceAlertHit(
            rule_id=rule_id, stock_id=stock.id, trigger_time=now,
            trigger_bucket=_minute_bucket(now), trigger_snapshot={},
        ))
        s.commit()
    finally:
        s.close()

    notified = {"n": 0}

    async def count_notify():
        notified["n"] += 1
        return True, ""

    result = _scan(session_factory, rule_id, count_notify)
    assert result["items"][0]["status"] == "duplicated"
    assert notified["n"] == 0  # 去重发生在通知之前,不重复打扰
