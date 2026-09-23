"""行业数据服务单测:主源全禁走备源降级、双源交叉、涨停口径、动量兜底、缓存刷新。

全部离线:akshare/Discovery/klines 通道以 monkeypatch 替身注入,不发起真实网络请求。
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.platform.persistence.database import Base
from src.platform.persistence.models import MacroIndicatorValue, SectorSnapshot, ValuationSeries


@pytest.fixture
def db():
    """独立内存库,不复用真实 DB 引擎。"""
    import src.platform.persistence.models  # noqa: F401

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    session = factory()
    yield session
    session.close()
    engine.dispose()


BOARDS = [
    {"code": "BK0475", "name": "示例行业甲", "change_pct": 2.1, "turnover": 1.0e9},
    {"code": "BK0478", "name": "示例行业乙", "change_pct": -1.3, "turnover": 5.0e8},
]


def _stub_fetchers(monkeypatch, *, ths=None, em=None, zt=None, spot=None, boards=None):
    """按需替换行业快照的取数步骤;默认东财系全部抛错(全禁)。

    传入的 dict/list 会被包成无参可调用(与真实 fetch 函数签名一致)。
    """
    from src.modules.market import sector_data_service as svc

    def _boom(*a, **kw):
        raise RuntimeError("东财源已禁用(测试注入)")

    def _wrap(data):
        if isinstance(data, dict):
            return lambda *a, **kw: dict(data)
        return lambda *a, **kw: list(data)

    boards_data = BOARDS if boards is None else boards  # 缺省给默认榜单;显式 [] 禁用
    monkeypatch.setattr(svc, "fetch_board_list", _wrap(boards_data))
    monkeypatch.setattr(svc, "fetch_em_sector_flow", _wrap(em) if em is not None else _boom)
    monkeypatch.setattr(svc, "fetch_ths_sector_flow", _wrap(ths) if ths is not None else _boom)
    monkeypatch.setattr(
        svc, "fetch_official_limit_up", _wrap(zt) if zt is not None else _boom
    )
    monkeypatch.setattr(svc, "fetch_market_spot", _wrap(spot) if spot is not None else _boom)


# ────────────────────────── 东财全禁 → 备源降级 ──────────────────────────


def test_collect_daily_snapshot_backup_path_when_eastmoney_all_disabled(db, monkeypatch):
    """东财系接口全禁用时:走同花顺备源产出快照,且降级标注齐全、幂等。"""
    _stub_fetchers(
        monkeypatch,
        ths={"示例行业甲": {"main": 1.0e8}, "示例行业乙": {"main": -2.0e7}},
    )

    from src.modules.market import sector_data_service as svc

    summary = svc.collect_daily_snapshot(db)
    assert summary["upserted"] == 2
    assert summary["sources_available"] == {
        "discovery_boards": True,
        "em_flow": False,
        "ths_flow": True,
        "official_limit_up": False,
        "market_spot": False,
    }
    assert summary["degraded"] is True

    day = summary["snapshot_date"]
    rows = (
        db.query(SectorSnapshot)
        .filter(SectorSnapshot.snapshot_date == day)
        .order_by(SectorSnapshot.board_code)
        .all()
    )
    assert len(rows) == 2
    first = rows[0]
    assert first.board_name == "示例行业甲"
    # 备源单源:取到值但降级级别=1
    assert first.main_net_inflow == pytest.approx(1.0e8)
    prov = first.meta["provenance"]["main_net_inflow"]
    assert prov["source"] == "ak.stock_fund_flow_industry"
    assert prov["caliber"] == "ths_total"
    assert prov["degrade_level"] == 1
    assert prov["cross_validation"]["status"] == "missing"
    # 涨停数不可得:字段空 + meta 记录口径与来源可用性
    assert first.limit_up_count is None
    assert first.limit_up_caliber == ""
    assert first.meta["sources_available"]["official_limit_up"] is False
    assert first.meta["limit_up"]["self_counted_total"] is None
    # 榜单字段照常落地
    assert first.change_pct == pytest.approx(2.1)

    # 幂等:重跑不产生重复行,且值被更新而非追加
    _stub_fetchers(
        monkeypatch,
        ths={"示例行业甲": {"main": 1.5e8}, "示例行业乙": {"main": -2.0e7}},
    )
    summary2 = svc.collect_daily_snapshot(db, snapshot_date=day)
    assert summary2["upserted"] == 2
    count = (
        db.query(SectorSnapshot)
        .filter(SectorSnapshot.snapshot_date == day, SectorSnapshot.board_code == "BK0475")
        .count()
    )
    assert count == 1
    refreshed = (
        db.query(SectorSnapshot)
        .filter(SectorSnapshot.snapshot_date == day, SectorSnapshot.board_code == "BK0475")
        .first()
    )
    assert refreshed.main_net_inflow == pytest.approx(1.5e8)


def test_collect_daily_snapshot_rank_by_main_net_inflow(db, monkeypatch):
    """rank 按当日主力净流入降序;缺值行业不参与排名。"""
    _stub_fetchers(
        monkeypatch,
        ths={"示例行业甲": {"main": 1.0e8}, "示例行业乙": {"main": 9.0e8}},
    )
    from src.modules.market import sector_data_service as svc

    summary = svc.collect_daily_snapshot(db)
    rows = {
        r.board_code: r
        for r in db.query(SectorSnapshot)
        .filter(SectorSnapshot.snapshot_date == summary["snapshot_date"])
        .all()
    }
    assert rows["BK0478"].rank == 1  # 9.0e8 更大
    assert rows["BK0475"].rank == 2


def test_collect_daily_snapshot_dual_source_ok_and_disputed(db, monkeypatch):
    """双源一致 → ok(取主源);偏差超 20% 容差 → disputed 且保留双方明细。"""
    from src.modules.market import sector_data_service as svc

    # 一致:东财 1.0e8 vs 同花顺 1.05e8(偏差 4.8% ≤ 20%)
    _stub_fetchers(
        monkeypatch,
        em={"示例行业甲": {"main": 1.0e8, "small": -3.0e7}},
        ths={"示例行业甲": {"main": 1.05e8}},
        zt={"示例行业甲": 5},
        spot=[],
    )
    summary = svc.collect_daily_snapshot(db)
    row = (
        db.query(SectorSnapshot)
        .filter(
            SectorSnapshot.snapshot_date == summary["snapshot_date"],
            SectorSnapshot.board_code == "BK0475",
        )
        .first()
    )
    prov = row.meta["provenance"]["main_net_inflow"]
    assert prov["cross_validation"]["status"] == "ok"
    assert prov["degrade_level"] == 0
    assert row.main_net_inflow == pytest.approx(1.0e8)  # 采主源
    # 官方涨停池命中:official 口径 + 计数
    assert row.limit_up_count == 5
    assert row.limit_up_caliber == "official"

    # 争议:同花顺 3.0e8 vs 东财 1.0e8(偏差 66% > 20%)
    _stub_fetchers(
        monkeypatch,
        em={"示例行业甲": {"main": 1.0e8, "small": None}},
        ths={"示例行业甲": {"main": 3.0e8}},
        zt={"示例行业甲": 5},
        spot=[],
    )
    summary2 = svc.collect_daily_snapshot(db, snapshot_date=summary["snapshot_date"])
    assert summary2["degraded"] is True
    row2 = (
        db.query(SectorSnapshot)
        .filter(
            SectorSnapshot.snapshot_date == summary["snapshot_date"],
            SectorSnapshot.board_code == "BK0475",
        )
        .first()
    )
    prov2 = row2.meta["provenance"]["main_net_inflow"]
    assert prov2["cross_validation"]["status"] == "disputed"
    assert prov2["degrade_level"] == 2
    sources = {s["source"]: s["value"] for s in prov2["cross_validation"]["sources"]}
    assert sources["ak.stock_sector_fund_flow_rank"] == 1.0e8
    assert sources["ak.stock_fund_flow_industry"] == 3.0e8
    # disputed 仍采主源值,但降级标注不丢
    assert row2.main_net_inflow == pytest.approx(1.0e8)


def test_collect_daily_snapshot_self_counted_limit_up_total(db, monkeypatch):
    """涨停池禁用、全市场快照可用时:自算总数记入 meta(self_counted 口径)。"""
    spot = [
        {"code": "000001", "name": "平安甲", "price": 11.0, "prev_close": 10.0},  # 涨停
        {"code": "300750", "name": "创业乙", "price": 24.0, "prev_close": 20.0},  # 20% 涨停
        {"code": "600000", "name": "ST丙", "price": 5.5, "prev_close": 5.0},  # ST 过滤
        {"code": "600001", "name": "未涨丁", "price": 10.5, "prev_close": 10.0},  # 非涨停
    ]
    _stub_fetchers(
        monkeypatch,
        ths={"示例行业甲": {"main": 1.0e8}},
        spot=spot,
    )
    from src.modules.market import sector_data_service as svc

    summary = svc.collect_daily_snapshot(db)
    assert summary["limit_up"]["self_counted_total"] == 2
    row = (
        db.query(SectorSnapshot)
        .filter(
            SectorSnapshot.snapshot_date == summary["snapshot_date"],
            SectorSnapshot.board_code == "BK0475",
        )
        .first()
    )
    assert row.limit_up_count is None  # 行业归属仅官方池可得
    assert row.meta["limit_up"]["self_counted_total"] == 2


# ────────────────────────── 涨停自算判定 ──────────────────────────


def test_detect_limit_up_rules():
    """自算涨停:昨收精确判定 + 分段比例 + ST 前缀过滤。"""
    from src.modules.market.sector_data_service import detect_limit_up

    assert detect_limit_up("平安甲", "000001", 11.0, 10.0) is True  # 主板 10%
    assert detect_limit_up("未涨乙", "000001", 10.5, 10.0) is False
    assert detect_limit_up("创业丙", "300001", 24.0, 20.0) is True  # 创业板 20%
    assert detect_limit_up("科创丁", "688001", 24.0, 20.0) is True  # 科创板 20%
    assert detect_limit_up("北交戊", "830001", 13.0, 10.0) is True  # 北交所 30%
    assert detect_limit_up("ST己", "600000", 5.5, 5.0) is False  # ST 过滤
    assert detect_limit_up("*ST庚", "600000", 5.5, 5.0) is False
    assert detect_limit_up("缺价辛", "000001", None, 10.0) is False
    assert detect_limit_up("零昨收壬", "000001", 11.0, 0.0) is False


# ────────────────────────── 行业动量 ──────────────────────────


def test_sector_momentum_snapshot_history_fallback(db, monkeypatch):
    """主源(行业K线)与备源(成分股)全禁时,兜底用快照库历史行合成。"""
    from src.modules.market import sector_data_service as svc

    # 落 3 天历史快照
    for i, (d, pct) in enumerate(
        [("2026-09-21", 1.0), ("2026-09-22", -0.5), ("2026-09-23", 2.0)]
    ):
        db.add(
            SectorSnapshot(
                snapshot_date=d,
                board_code="BK0475",
                board_name="示例行业甲",
                change_pct=pct,
            )
        )
    db.commit()

    monkeypatch.setattr(svc, "fetch_board_hist_closes", lambda name, days=40: [])
    monkeypatch.setattr(svc, "fetch_board_constituents", lambda code, limit=10: [])
    monkeypatch.setattr(svc, "fetch_board_list", lambda limit=100, proxy=None: [])

    result = svc.sector_momentum(db, "BK0475")
    assert result["source"] == "sector_snapshots_history"
    assert result["degrade_level"] == 2
    # 复利合成全部可得序列:(1+1%)×(1-0.5%)×(1+2%)-1 ≈ 2.5049%
    assert result["momentum"]["d5"] == pytest.approx(2.5049, abs=1e-3)


def test_sector_momentum_fallback_uses_recent_rows_not_stale(db, monkeypatch):
    """兜底查询取「最近」N 行:历史行数超过窗口时不得用数月前的旧行算动量。"""
    from src.modules.market import sector_data_service as svc

    rows = [
        # 陈旧行:1 月上旬 7 个交易日,逐日 +0.1%
        ("2026-01-05", 0.1), ("2026-01-06", 0.1), ("2026-01-07", 0.1),
        ("2026-01-08", 0.1), ("2026-01-09", 0.1), ("2026-01-12", 0.1),
        ("2026-01-13", 0.1),
        # 近期行:9 月末 3 个交易日
        ("2026-09-21", 1.0), ("2026-09-22", -0.5), ("2026-09-23", 2.0),
    ]
    for d, pct in rows:
        db.add(
            SectorSnapshot(
                snapshot_date=d,
                board_code="BK0475",
                board_name="示例行业甲",
                change_pct=pct,
            )
        )
    db.commit()

    monkeypatch.setattr(svc, "fetch_board_hist_closes", lambda name, days=40: [])
    monkeypatch.setattr(svc, "fetch_board_constituents", lambda code, limit=10: [])
    monkeypatch.setattr(svc, "fetch_board_list", lambda limit=100, proxy=None: [])

    result = svc.sector_momentum(db, "BK0475", days=3)
    assert result["source"] == "sector_snapshots_history"
    # 窗口=3 → 取最近的 3 行(9 月),复利 (1+1%)×(1-0.5%)×(1+2%)-1 ≈ 2.5049%。
    # 若误取最旧 3 行(1 月)会得到 ≈0.3003% 的陈旧动量。
    assert result["momentum"]["d5"] == pytest.approx(2.5049, abs=1e-3)
    assert result["momentum_window_end"] == "2026-09-23"


def test_sector_momentum_constituent_backup(db, monkeypatch):
    """备源:成分股经 klines 通道按成交额加权合成。"""
    from src.modules.market import sector_data_service as svc

    bars_a = [
        SimpleNamespace(date=f"2026-09-{d:02d}", open=10, close=c, high=10, low=9, volume=1)
        for d, c in [(1, 10.0), (2, 11.0), (3, 12.0), (4, 13.0), (5, 14.0), (6, 15.0)]
    ]
    bars_b = [
        SimpleNamespace(date=f"2026-09-{d:02d}", open=10, close=c, high=10, low=9, volume=1)
        for d, c in [(1, 10.0), (2, 10.0), (3, 10.0), (4, 10.0), (5, 10.0), (6, 10.4)]
    ]

    class _FakeMD:
        def klines(self, symbol, *, market, days=120, min_count=1):
            return bars_a if symbol == "600001" else bars_b

    import src.platform.marketdata.marketdata_client as md_client

    monkeypatch.setattr(md_client, "get_market_data", lambda: _FakeMD())
    monkeypatch.setattr(
        svc, "fetch_board_list", lambda limit=100, proxy=None: list(BOARDS)
    )
    monkeypatch.setattr(svc, "fetch_board_hist_closes", lambda name, days=40: [])
    monkeypatch.setattr(
        svc,
        "fetch_board_constituents",
        lambda code, limit=10: [
            {"symbol": "600001", "turnover": 3.0},
            {"symbol": "600002", "turnover": 1.0},
        ],
    )

    result = svc.sector_momentum(db, "BK0475")
    assert result["source"] == "board_constituents_kline"
    assert result["degrade_level"] == 1
    # 5 日涨幅:a=50%,b=4% → 加权 (3*50 + 1*4)/4 = 38.5%
    assert result["momentum"]["d5"] == pytest.approx(38.5, abs=1e-3)


def test_sector_momentum_all_sources_fail_soft(db, monkeypatch):
    """三级全不可得:fail-soft 返回 degrade=3、momentum 全 None,不抛错。"""
    from src.modules.market import sector_data_service as svc

    monkeypatch.setattr(svc, "fetch_board_hist_closes", lambda name, days=40: [])
    monkeypatch.setattr(svc, "fetch_board_constituents", lambda code, limit=10: [])
    monkeypatch.setattr(svc, "fetch_board_list", lambda limit=100, proxy=None: [])

    result = svc.sector_momentum(db, "BK9999")
    assert result["degrade_level"] == 3
    assert result["momentum"] == {"d5": None, "d10": None, "d20": None}


def test_sector_momentum_official_kline_primary(db, monkeypatch):
    """主源:行业历史K线足够长时直接算 5/10/20 日涨幅。"""
    from src.modules.market import sector_data_service as svc

    closes = [100.0 + i for i in range(30)]  # 匀速上涨
    monkeypatch.setattr(
        svc, "fetch_board_list", lambda limit=100, proxy=None: list(BOARDS)
    )
    monkeypatch.setattr(svc, "fetch_board_hist_closes", lambda name, days=40: list(closes))

    result = svc.sector_momentum(db, "BK0475")
    assert result["source"] == "ak.stock_board_industry_hist_em"
    assert result["degrade_level"] == 0
    assert result["board_name"] == "示例行业甲"
    # d5: 129/124-1 ≈ 4.03%
    assert result["momentum"]["d5"] == pytest.approx(4.032, abs=1e-2)


# ────────────────────────── 宏观缓存 ──────────────────────────


def _fake_macro_client(monkeypatch, indicators):
    """替换 marketdata_client.get_market_data,macro() 返回注入指标。"""
    import src.platform.marketdata.marketdata_client as md_client

    class _FakeMD:
        def macro(self, *, market="CN"):
            return SimpleNamespace(ok=bool(indicators), data=list(indicators), error="")

    monkeypatch.setattr(md_client, "get_market_data", lambda: _FakeMD())


def test_refresh_macro_cache_upsert_idempotent(db, monkeypatch):
    """宏观缓存:按 (indicator, period) UPSERT;同期新值覆盖,重跑不重复。"""
    from marketdata.types import MacroIndicator

    from src.modules.market.sector_data_service import refresh_macro_cache

    _fake_macro_client(
        monkeypatch,
        [
            MacroIndicator(
                name="CPI同比", value=2.1, period="2026年08月", source="macro_china_cpi"
            ),
            MacroIndicator(
                name="制造业PMI", value=49.8, period="2026年08月", source="macro_china_pmi"
            ),
        ],
    )
    result = refresh_macro_cache(db)
    assert result["ok"] is True
    assert result["upserted"] == 2
    assert db.query(MacroIndicatorValue).count() == 2

    # 同期新值覆盖 + 新期新增
    _fake_macro_client(
        monkeypatch,
        [
            MacroIndicator(
                name="CPI同比", value=2.2, period="2026年08月", source="macro_china_cpi"
            ),
            MacroIndicator(
                name="CPI同比", value=2.0, period="2026年09月", source="macro_china_cpi"
            ),
        ],
    )
    result2 = refresh_macro_cache(db)
    assert result2["upserted"] == 2
    assert db.query(MacroIndicatorValue).count() == 3  # 08月两条合并,09月新增
    row = (
        db.query(MacroIndicatorValue)
        .filter(
            MacroIndicatorValue.indicator == "CPI同比",
            MacroIndicatorValue.period == "2026年08月",
        )
        .first()
    )
    assert row.value == 2.2


def test_refresh_macro_cache_all_sources_fail_soft(db, monkeypatch):
    """宏观全源失败:fail-soft 返回 ok=False,不写行、不抛错。"""
    import src.platform.marketdata.marketdata_client as md_client

    from src.modules.market.sector_data_service import refresh_macro_cache

    class _FakeMD:
        def macro(self, *, market="CN"):
            return SimpleNamespace(ok=False, data=None, error="全源失败")

    monkeypatch.setattr(md_client, "get_market_data", lambda: _FakeMD())
    result = refresh_macro_cache(db)
    assert result["ok"] is False
    assert result["upserted"] == 0
    assert db.query(MacroIndicatorValue).count() == 0


# ────────────────────────── 估值序列 ──────────────────────────


def test_refresh_valuation_series_incremental_and_backup(db, monkeypatch):
    """估值序列:主源增量补齐;主源失败时 baostock 备源兜底并标降级。"""
    from src.modules.market import sector_data_service as svc

    rows_a = [
        {"trade_date": "2026-09-21", "pe_ttm": 20.0, "pb": 2.0, "source": "ak.stock_value_em"},
        {"trade_date": "2026-09-22", "pe_ttm": 21.0, "pb": 2.1, "source": "ak.stock_value_em"},
    ]
    monkeypatch.setattr(svc, "_valuation_from_eastmoney", lambda s, since: rows_a)
    report = svc.refresh_valuation_series(db, ["600000"])
    assert report["symbols"]["600000"]["upserted"] == 2
    assert report["symbols"]["600000"]["source"] == "ak.stock_value_em"

    # 增量:已有 09-22,只补之后的行
    rows_b = [
        {"trade_date": "2026-09-22", "pe_ttm": 21.0, "pb": 2.1, "source": "ak.stock_value_em"},
        {"trade_date": "2026-09-23", "pe_ttm": 22.0, "pb": 2.2, "source": "ak.stock_value_em"},
    ]
    seen_since: dict[str, str | None] = {}

    def _fake_em(symbol, since):
        seen_since["v"] = since
        return rows_b

    monkeypatch.setattr(svc, "_valuation_from_eastmoney", _fake_em)
    report2 = svc.refresh_valuation_series(db, ["600000"])
    assert seen_since["v"] == "2026-09-22"
    assert report2["symbols"]["600000"]["upserted"] == 1
    assert db.query(ValuationSeries).filter(ValuationSeries.symbol == "600000").count() == 3

    # 主源失败 → baostock 兜底成功,标记降级
    def _boom(symbol, since):
        raise RuntimeError("东财估值不可用")

    monkeypatch.setattr(svc, "_valuation_from_eastmoney", _boom)
    monkeypatch.setattr(
        svc,
        "_valuation_from_baostock",
        lambda s, start, end: [
            {"trade_date": "2026-09-24", "pe_ttm": 23.0, "pb": 2.3, "source": "baostock"}
        ],
    )
    report3 = svc.refresh_valuation_series(db, ["600000"])
    sym3 = report3["symbols"]["600000"]
    assert sym3["source"] == "baostock"
    assert sym3["degraded"] is True
    assert sym3["upserted"] == 1

    # 双源全挂:该只 fail-soft,errors 记录,不影响返回
    monkeypatch.setattr(svc, "_valuation_from_eastmoney", _boom)
    monkeypatch.setattr(
        svc, "_valuation_from_baostock", lambda s, start, end: (_ for _ in ()).throw(RuntimeError("bb"))
    )
    report4 = svc.refresh_valuation_series(db, ["600000"])
    assert report4["symbols"]["600000"]["upserted"] == 0
    assert len(report4["symbols"]["600000"]["errors"]) == 2


# ────────────────────────── baostock 备源登录/错误呈现 ──────────────────────────


def _install_fake_baostock(monkeypatch, *, login_error=None, query_error=None, rows=None):
    """向 sys.modules 注入假 baostock 模块,记录 login/logout/query 调用。"""
    import types

    calls = {"login": 0, "logout": 0, "query_args": None}

    class _FakeLG:
        def __init__(self, error_code, error_msg, data_rows):
            self.error_code = error_code
            self.error_msg = error_msg
            self._rows = data_rows
            self._i = 0

        def next(self):
            if self._i < len(self._rows):
                self._i += 1
                return True
            return False

        def get_row_data(self):
            return self._rows[self._i - 1]

    class _FakeBS:
        def login(self):
            calls["login"] += 1
            if login_error:
                return SimpleNamespace(error_code=login_error[0], error_msg=login_error[1])
            return SimpleNamespace(error_code="0", error_msg="")

        def logout(self):
            calls["logout"] += 1
            return SimpleNamespace(error_code="0", error_msg="")

        def query_history_k_data_plus(self, code, fields, **kw):
            calls["query_args"] = {"code": code, "fields": fields, **kw}
            if query_error:
                return _FakeLG(query_error[0], query_error[1], [])
            return _FakeLG("0", "", rows or [])

    fake = types.ModuleType("baostock")
    fake.login = _FakeBS().login
    fake.logout = _FakeBS().logout
    fake.query_history_k_data_plus = _FakeBS().query_history_k_data_plus
    monkeypatch.setitem(__import__("sys").modules, "baostock", fake)
    return calls


def test_load_baostock_missing_package_raises_reason(monkeypatch):
    """baostock 缺库(本环境实况):必须抛 RuntimeError 带原因,不得静默返回空。"""
    import builtins

    from src.modules.market.sector_data_service import _load_baostock

    monkeypatch.delitem(__import__("sys").modules, "baostock", raising=False)
    real_import = builtins.__import__

    def _no_baostock(name, *a, **kw):
        if name == "baostock":
            raise ModuleNotFoundError("No module named 'baostock'")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", _no_baostock)
    with pytest.raises(RuntimeError, match="baostock 未安装"):
        _load_baostock()


def test_baostock_backup_full_flow_login_query_logout(monkeypatch):
    """登录成功:查询解析行、logout 在 finally 执行、代码带 sh. 前缀。"""
    from src.modules.market.sector_data_service import _valuation_from_baostock

    calls = _install_fake_baostock(
        monkeypatch,
        rows=[
            ["2026-09-22", "21.5", "2.10"],
            ["2026-09-23", "22.0", ""],  # 空串 → pe 22.0 / pb None
        ],
    )
    out = _valuation_from_baostock("600000", "2026-09-01", "2026-09-30")
    assert calls["login"] == 1
    assert calls["logout"] == 1
    assert calls["query_args"]["code"] == "sh.600000"
    assert calls["query_args"]["fields"] == "date,peTTM,pbMRQ"
    assert out == [
        {"trade_date": "2026-09-22", "pe_ttm": 21.5, "pb": 2.1, "source": "baostock"},
        {"trade_date": "2026-09-23", "pe_ttm": 22.0, "pb": None, "source": "baostock"},
    ]


def test_baostock_not_logged_in_error_surfaced(monkeypatch):
    """未登录(BSERR_NO_LOGIN):错误码/信息必须抛出进 errors,不得静默返空。"""
    from src.modules.market.sector_data_service import _valuation_from_baostock

    calls = _install_fake_baostock(
        monkeypatch, query_error=("10000", "用户未登录")
    )
    with pytest.raises(RuntimeError) as exc:
        _valuation_from_baostock("600000", "2026-09-01", "2026-09-30")
    assert "10000" in str(exc.value) and "用户未登录" in str(exc.value)
    assert calls["logout"] == 1  # finally 里 logout 兜底


def test_baostock_login_failure_raises(monkeypatch):
    """login 返回非 0 错误码:抛 RuntimeError 且不触发查询。"""
    from src.modules.market.sector_data_service import _valuation_from_baostock

    calls = _install_fake_baostock(
        monkeypatch, login_error=("network", "连接失败")
    )
    with pytest.raises(RuntimeError) as exc:
        _valuation_from_baostock("600000", "2026-09-01", "2026-09-30")
    assert "登录失败" in str(exc.value) and "network" in str(exc.value)
    assert calls["query_args"] is None  # 登录失败不发起查询
