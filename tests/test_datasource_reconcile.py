"""数据源表温和对账(server.reconcile_data_sources):补齐缺失默认 + 删孤儿,保留用户自定义不动。"""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.web import models as M  # noqa: F401  确保模型注册到 Base.metadata
from src.web.database import Base
from src.web.models import DataSource

import server


def _make_session():
    """独立内存 sqlite,不碰真实 data/panwatch.db。"""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    return Session()


def test_reconcile_deletes_orphans_keeps_user_custom_and_fills_missing_defaults():
    """对账:删孤儿(news/cls、kline/tushare),保留用户自定义 quote/tencent(config/priority 原样),补回缺失默认。"""
    db = _make_session()

    # 孤儿 1:news/cls —— 既不在 marketdata 包内引擎集合,也不在当前 seed 列表里
    db.add(
        DataSource(
            name="财联社电报",
            type="news",
            provider="cls",
            config={"rn": 50},
            enabled=True,
            priority=2,
            supports_batch=False,
            test_symbols=[],
        )
    )
    # 孤儿 2:kline/tushare —— Step3 已从 seed 里删除,包内也没有对应 vendor
    db.add(
        DataSource(
            name="Tushare K线",
            type="kline",
            provider="tushare",
            config={"token": "", "description": "旧配置"},
            enabled=False,
            priority=10,
            supports_batch=False,
            test_symbols=["600519"],
        )
    )
    # 有效自定义:quote/tencent 是合法 seed 默认,但用户改过 config/priority —— 应原样保留
    db.add(
        DataSource(
            name="腾讯行情",
            type="quote",
            provider="tencent",
            config={"foo": 1},
            enabled=True,
            priority=99,
            supports_batch=True,
            test_symbols=["600519"],
        )
    )
    db.commit()
    # 故意不插入某个 seed 默认(例如东方财富 K线),验证 reconcile 会补回

    result = server.reconcile_data_sources(db)

    remaining = {(s.type, s.provider): s for s in db.query(DataSource).all()}

    # 孤儿被删
    assert ("news", "cls") not in remaining
    assert ("kline", "tushare") not in remaining

    # 用户自定义配置原样保留
    kept = remaining[("quote", "tencent")]
    assert kept.config == {"foo": 1}
    assert kept.priority == 99

    # 缺失的默认被补回
    assert ("kline", "eastmoney") in remaining

    # summary 里能看到删除记录
    deleted_pairs = {(d["type"], d["provider"]) for d in result["deleted"]}
    assert ("news", "cls") in deleted_pairs
    assert ("kline", "tushare") in deleted_pairs
    assert ("quote", "tencent") not in deleted_pairs

    db.close()
