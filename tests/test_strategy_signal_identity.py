"""机会页卡片身份漂移回归测试。

根因：entry_candidates 每日先删后插 + 无 AUTOINCREMENT 导致 rowid 复用，
同一候选 id 跨代指向不同股票；策略信号以 (candidate_id, strategy_code)
为身份键且更新不刷新 symbol/name → 新股票价格写进旧股票的信号行。
"""

from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.platform.persistence.database import Base

SNAPSHOT = "2026-09-28"


def _scan_input(symbol: str, name: str, price: float) -> dict:
    return {
        "symbol": symbol,
        "market": "CN",
        "stock_name": name,
        "candidate_source": "market_scan",
        "source_agent": "market_scan",
        "quote_seed": {"current_price": price},
        "action": "buy",
        "action_label": "建仓",
        "signal": "",
        "reason": "",
        "meta": {},
    }


@pytest.fixture
def db(monkeypatch):
    """独立内存库 + 全局 SessionLocal 指向它。"""
    import src.platform.persistence.models  # noqa: F401
    import src.platform.persistence.database as database

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(database, "SessionLocal", factory)
    import src.modules.strategy.strategy_engine as engine_mod
    import src.modules.strategy.strategy_catalog as catalog_mod
    import src.modules.strategy.entry_candidates as candidates_mod

    monkeypatch.setattr(engine_mod, "SessionLocal", factory)
    monkeypatch.setattr(catalog_mod, "SessionLocal", factory)
    monkeypatch.setattr(candidates_mod, "SessionLocal", factory)

    # 离线化:市场池/建议池/持仓/K线全部换成罐头数据
    monkeypatch.setattr(candidates_mod, "_load_market_scan_inputs", lambda **kw: {})
    monkeypatch.setattr(candidates_mod, "_load_latest_suggestions", lambda **kw: [])
    monkeypatch.setattr(candidates_mod, "_load_holding_keys", lambda: set())

    class _StubKlineCollector:
        def __init__(self, *a, **kw):
            pass

        def fetch_daily(self, *a, **kw):
            return []

        def kline(self, *a, **kw):
            return []

        def fetch(self, *a, **kw):
            return []

    monkeypatch.setattr(candidates_mod, "KlineCollector", _StubKlineCollector)
    return factory


@contextmanager
def _session(factory):
    s = factory()
    try:
        yield s
    finally:
        s.close()


def _run_candidate_refresh(factory, monkeypatch, inputs: dict):
    import src.modules.strategy.entry_candidates as candidates_mod

    monkeypatch.setattr(candidates_mod, "_load_market_scan_inputs", lambda **kw: dict(inputs))
    monkeypatch.setattr(
        candidates_mod,
        "md_stock_data",
        lambda symbols, market: [
            SimpleNamespace(
                symbol=s,
                name=inputs[f"CN:{s}"]["stock_name"],
                current_price=inputs[f"CN:{s}"]["quote_seed"]["current_price"],
                change_pct=None,
                turnover=None,
            )
            for s in symbols
            if f"CN:{s}" in inputs
        ],
    )
    return candidates_mod.refresh_entry_candidates(snapshot_date=SNAPSHOT, market_scan_limit=20)


def _signal_rows(factory) -> dict:
    from src.platform.persistence.models import StrategySignalRun

    with _session(factory) as s:
        return {
            r.stock_symbol: r
            for r in s.query(StrategySignalRun).filter(StrategySignalRun.snapshot_date == SNAPSHOT).all()
        }


def test_candidate_upsert_keeps_id_and_updates_fields(db, monkeypatch):
    """同日重复刷新：同股 id 不变、字段更新；消失的候选被删除。"""
    _run_candidate_refresh(db, monkeypatch, {"CN:688786": _scan_input("688786", "悦安新材", 28.59)})
    with _session(db) as s:
        from src.platform.persistence.models import EntryCandidate

        first = (
            s.query(EntryCandidate)
            .filter(EntryCandidate.snapshot_date == SNAPSHOT, EntryCandidate.stock_symbol == "688786")
            .one()
        )
        first_id = first.id
        assert 27.0 < float(first.entry_low) < 30.0

    # 第二次刷新:同股价格更新
    _run_candidate_refresh(db, monkeypatch, {"CN:688786": _scan_input("688786", "悦安新材", 29.0)})
    with _session(db) as s:
        from src.platform.persistence.models import EntryCandidate

        rows = (
            s.query(EntryCandidate)
            .filter(EntryCandidate.snapshot_date == SNAPSHOT, EntryCandidate.stock_symbol == "688786")
            .all()
        )
        assert len(rows) == 1, "同股同日应只保留一行"
        assert rows[0].id == first_id, "id 必须稳定(先删后插会漂移 id)"
        assert 28.0 < float(rows[0].entry_low) < 30.0, "价格字段应被更新"

    # 第三次刷新:悦安消失、中微进入 → 悦安行删除、中微行新建
    _run_candidate_refresh(db, monkeypatch, {"CN:688012": _scan_input("688012", "中微公司", 324.92)})
    with _session(db) as s:
        from src.platform.persistence.models import EntryCandidate

        symbols = {
            r.stock_symbol for r in s.query(EntryCandidate).filter(EntryCandidate.snapshot_date == SNAPSHOT).all()
        }
        assert symbols == {"688012"}, "消失的候选应被删除"


def test_signal_identity_not_contaminated_when_candidate_id_reused(db, monkeypatch):
    """候选 id 被复用指向另一只股票时，信号行身份不得被交叉污染。"""
    _run_candidate_refresh(db, monkeypatch, {"CN:688786": _scan_input("688786", "悦安新材", 28.59)})
    import src.modules.strategy.strategy_engine as engine_mod

    engine_mod.refresh_strategy_signals(snapshot_date=SNAPSHOT, rebuild_candidates=False)
    rows = _signal_rows(db)
    assert "688786" in rows
    assert rows["688786"].stock_name == "悦安新材"

    # 模拟 rowid 复用:删除当日候选后强制以相同 id 插入另一只股票
    with _session(db) as s:
        from src.platform.persistence.models import EntryCandidate

        cand = (
            s.query(EntryCandidate)
            .filter(EntryCandidate.snapshot_date == SNAPSHOT, EntryCandidate.stock_symbol == "688786")
            .one()
        )
        reused_id = cand.id
        s.query(EntryCandidate).filter(EntryCandidate.snapshot_date == SNAPSHOT).delete(synchronize_session=False)
        s.expunge_all()  # 清 identity map,允许以相同 id 重插(模拟 rowid 复用)
        s.add(
            EntryCandidate(
                id=reused_id,
                stock_symbol="688012",
                stock_market="CN",
                stock_name="中微公司",
                snapshot_date=SNAPSHOT,
                status="active",
                score=90.0,
                candidate_source="market_scan",
                source_agent="market_scan",
                entry_low=321.6708,
                entry_high=328.1692,
                stop_loss=316.6775,
                target_price=359.865,
                plan_quality=100,
            )
        )
        s.commit()

    engine_mod.refresh_strategy_signals(snapshot_date=SNAPSHOT, rebuild_candidates=False)
    rows = _signal_rows(db)

    assert "688012" in rows, "中微公司应以自己的身份新建信号行"
    assert rows["688012"].stock_name == "中微公司"
    assert abs(float(rows["688012"].entry_low) - 321.6708) < 0.01

    if "688786" in rows:  # 若保留,身份与价格必须仍是悦安自己的
        r = rows["688786"]
        assert r.stock_name == "悦安新材"
        assert not (abs(float(r.entry_low) - 321.6708) < 0.01), "悦安行不得出现中微公司的价格"
