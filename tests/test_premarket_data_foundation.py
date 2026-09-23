"""盘前数据地基单测:六表迁移(幂等重跑)+ 交叉校验三分支 + 血统工具。"""

from __future__ import annotations

import pytest
from sqlalchemy import create_engine, inspect, text


# ────────────────────────── 迁移 ──────────────────────────

NEW_TABLES = (
    "event_calendar_items",
    "sector_predictions",
    "sector_snapshots",
    "macro_indicator_values",
    "fundamentals_cache",
    "valuation_series",
)


def test_m127_creates_six_tables(tmp_path):
    """v127 迁移在新库上创建全部六张表与关键索引。"""
    from src.platform.persistence.migrations import _m127_premarket_data_foundation

    engine = create_engine(f"sqlite:///{tmp_path / 'foundation.db'}")
    with engine.begin() as conn:
        _m127_premarket_data_foundation(conn)
        names = {
            row[0]
            for row in conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            )
        }
        indexes = {
            row[0]
            for row in conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='index'")
            )
        }
    engine.dispose()

    assert set(NEW_TABLES) <= names
    assert {
        "ix_event_calendar_date",
        "ix_sector_prediction_date",
        "ix_sector_snapshot_date",
        "ix_macro_indicator_indicator",
        "ix_valuation_series_symbol_date",
    } <= indexes

    # 行内关键列抽查:唯一约束/口径字段在
    engine = create_engine(f"sqlite:///{tmp_path / 'foundation.db'}")
    with engine.begin() as conn:
        snap_cols = {
            row[1] for row in conn.execute(text("PRAGMA table_info(sector_snapshots)"))
        }
        macro_cols = {
            row[1]
            for row in conn.execute(text("PRAGMA table_info(macro_indicator_values)"))
        }
    engine.dispose()
    assert {
        "limit_up_count",
        "limit_up_caliber",
        "main_net_inflow",
        "small_net_inflow",
        "rank",
        "meta",
    } <= snap_cols
    assert {"indicator", "period", "value", "publish_date", "source", "as_of"} <= macro_cols


def test_m127_idempotent_rerun(tmp_path):
    """v127 迁移连续重跑两次:不抛错、表集合不变、数据保留。"""
    from src.platform.persistence.migrations import _m127_premarket_data_foundation

    engine = create_engine(f"sqlite:///{tmp_path / 'idem.db'}")
    with engine.begin() as conn:
        _m127_premarket_data_foundation(conn)
        conn.execute(
            text(
                "INSERT INTO macro_indicator_values(indicator, period, value, source) "
                "VALUES('CPI同比', '2026年08月', 2.1, 'ut')"
            )
        )
        _m127_premarket_data_foundation(conn)  # 幂等重跑
        names = {
            row[0]
            for row in conn.execute(
                text("SELECT name FROM sqlite_master WHERE type='table'")
            )
        }
        kept = conn.execute(
            text(
                "SELECT value FROM macro_indicator_values "
                "WHERE indicator='CPI同比' AND period='2026年08月'"
            )
        ).scalar()
    engine.dispose()

    assert set(NEW_TABLES) <= names
    assert kept == 2.1


def test_run_versioned_migrations_full_chain_twice(tmp_path):
    """全量版本化迁移连续跑两遍:第二遍应全部跳过且无异常。

    与 init_db 的真实顺序一致:先 create_all 注册 ORM 表,再跑版本化迁移
    (m108 等历史迁移的数据回填依赖 create_all 建出的宿主表)。
    """
    import src.platform.persistence.models  # noqa: F401

    from src.platform.persistence.database import Base
    from src.platform.persistence.migrations import run_versioned_migrations

    engine = create_engine(f"sqlite:///{tmp_path / 'chain.db'}")
    Base.metadata.create_all(engine)
    run_versioned_migrations(engine)
    run_versioned_migrations(engine)
    with engine.begin() as conn:
        applied = conn.execute(
            text(
                "SELECT version FROM schema_migrations "
                "WHERE success=1 ORDER BY version"
            )
        ).fetchall()
        versions = [int(r[0]) for r in applied]
    engine.dispose()

    assert versions[-1] == 127
    assert len(versions) == len(set(versions))  # 无重复版本


def test_models_metadata_matches_tables():
    """ORM 元数据注册了六张新表,且与迁移表名一一对应。"""
    import src.platform.persistence.models as models

    from src.platform.persistence.database import Base

    for t in NEW_TABLES:
        assert t in Base.metadata.tables, f"ORM 缺少表: {t}"
    # 关键模型类存在
    for cls in (
        models.EventCalendarItem,
        models.SectorPrediction,
        models.SectorSnapshot,
        models.MacroIndicatorValue,
        models.FundamentalsCache,
        models.ValuationSeries,
    ):
        assert cls.__tablename__ in NEW_TABLES


# ────────────────────────── 交叉校验 ──────────────────────────


def test_cross_validate_ok_branch():
    """双源一致(相对偏差 ≤ 容差)→ ok。"""
    from src.modules.market.data_cross_validation import cross_validate

    verdict = cross_validate(
        [
            {"value": 10.0, "source": "a", "caliber": "em_main"},
            {"value": 10.4, "source": "b", "caliber": "ths_total"},
        ]
    )
    assert verdict["status"] == "ok"
    assert verdict["reason"] == "consistent"
    assert verdict["deviation"] <= 0.05
    assert verdict["degrade_level"] == 0
    assert len(verdict["sources"]) == 2
    assert verdict["sources"][0]["source"] == "a"


def test_cross_validate_disputed_branch():
    """双源偏差超容差 → disputed,且各源值与来源完整保留。"""
    from src.modules.market.data_cross_validation import (
        DEGRADE_DISPUTED,
        cross_validate,
    )

    verdict = cross_validate(
        [
            {"value": 100.0, "source": "a"},
            {"value": 150.0, "source": "b"},
        ],
        tol=0.05,
    )
    assert verdict["status"] == "disputed"
    assert verdict["reason"] == "sources_disagree"
    assert verdict["deviation"] > 0.05
    assert verdict["degrade_level"] == DEGRADE_DISPUTED
    assert {s["source"]: s["value"] for s in verdict["sources"]} == {"a": 100.0, "b": 150.0}


def test_cross_validate_missing_branch():
    """有效值不足两个(全缺/单源)→ missing,来源明细保留。"""
    from src.modules.market.data_cross_validation import cross_validate

    none_verdict = cross_validate(
        [
            {"value": None, "source": "a"},
            {"value": "", "source": "b"},
        ]
    )
    assert none_verdict["status"] == "missing"
    assert none_verdict["reason"] == "no_valid_values"

    single_verdict = cross_validate([{"value": 5.0, "source": "a"}])
    assert single_verdict["status"] == "missing"
    assert single_verdict["reason"] == "insufficient_sources"
    # 单源时降级级别为 1(区别于全缺的 3),值仍保留在明细里
    assert single_verdict["degrade_level"] == 1
    assert single_verdict["sources"][0]["value"] == 5.0

    assert cross_validate([])["status"] == "missing"


def test_pick_primary_follows_verdict():
    """采值规则:一致取主源/争议仍取主源但标注/单源降级取值/全缺返回 None。"""
    from src.modules.market.data_cross_validation import (
        DEGRADE_DISPUTED,
        DEGRADE_MISSING,
        DEGRADE_SINGLE_SOURCE,
        cross_validate,
        pick_primary,
    )

    ok = cross_validate([{"value": 10.0, "source": "a"}, {"value": 10.1, "source": "b"}])
    assert pick_primary(
        [{"value": 10.0, "source": "a"}, {"value": 10.1, "source": "b"}], ok
    ) == (10.0, 0)

    disputed = cross_validate([{"value": 10.0, "source": "a"}, {"value": 99.0, "source": "b"}])
    value, degrade = pick_primary(
        [{"value": 10.0, "source": "a"}, {"value": 99.0, "source": "b"}], disputed
    )
    assert (value, degrade) == (10.0, DEGRADE_DISPUTED)

    single = cross_validate([{"value": 7.0, "source": "a"}])
    assert pick_primary([{"value": 7.0, "source": "a"}], single) == (
        7.0,
        DEGRADE_SINGLE_SOURCE,
    )

    empty = cross_validate([{"value": None, "source": "a"}])
    assert pick_primary([{"value": None, "source": "a"}], empty) == (None, DEGRADE_MISSING)


def test_make_provenance_shape():
    """血统工具组装 value/source/as_of/caliber/degrade_level。"""
    from src.modules.market.data_cross_validation import make_provenance

    p = make_provenance(
        value=12.5,
        source="ak.stock_zt_pool_em",
        as_of="2026-09-23T15:35:00",
        caliber="official",
        degrade_level=0,
    )
    assert p == {
        "value": 12.5,
        "source": "ak.stock_zt_pool_em",
        "as_of": "2026-09-23T15:35:00",
        "caliber": "official",
        "degrade_level": 0,
    }
    # 缺省不炸,字段齐全
    assert set(make_provenance().keys()) == {
        "value",
        "source",
        "as_of",
        "caliber",
        "degrade_level",
    }


def test_to_float_rejects_garbage():
    """宽容转数值:NaN/非数/空串 → None,千分位可解析。"""
    from src.modules.market.data_cross_validation import to_float

    assert to_float(None) is None
    assert to_float("") is None
    assert to_float("abc") is None
    assert to_float(float("nan")) is None
    assert to_float("1,234.5") == 1234.5
    assert to_float(-3) == -3.0
