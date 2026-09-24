"""UI 呈现地基单测:策略信号投影的 earnings_verification 徽章字段 + dashboard brief 的盘前决策优先级。

全离线:信号投影用未落库的 ORM 行对象;brief 端点用内存 SQLite + TestClient,
不连外部、不触碰真实 data/panwatch.db。
"""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.modules.portfolio.api import dashboard as dashboard_api
from src.modules.strategy.strategy_engine import _format_signal
from src.platform.persistence.database import Base, get_db
from src.platform.persistence.models import AnalysisHistory, StrategySignalRun


def _make_row(**payload_overrides) -> StrategySignalRun:
    """构造未落库的信号行(仅投影所需字段)。"""
    kwargs = {"payload": {}}
    kwargs.update(payload_overrides)
    return StrategySignalRun(
        id=1,
        snapshot_date="2026-09-23",
        stock_symbol="600001",
        stock_market="CN",
        stock_name="测试股",
        strategy_code="premarket_pipeline",
        strategy_name="盘前流水线",
        strategy_version="v1",
        risk_level="medium",
        source_pool="watchlist",
        score=80.0,
        rank_score=80.0,
        status="active",
        action="buy",
        action_label="买入",
        signal="入场[10.0,11.0] 止损9.5 止盈13.0",
        reason="板块共振",
        **kwargs,
    )


# ────────────────────────── 信号投影:earnings_verification ──────────────────────────


def test_signal_projection_pipeline_verification_passed():
    """流水线现形 payload(passed 且无告警)→ status=passed;缺失字段置 None"""
    row = _make_row(
        payload={
            "verification": {
                "passed": True,
                "checks": {"single_quarter": {"status": "PASS", "detail": "+12.3%"}},
                "warnings": [],
            }
        }
    )
    item = _format_signal(row)
    assert item["earnings_verification"] == {
        "status": "passed",
        "report_date": None,
        "single_quarter_yoy": None,
    }


def test_signal_projection_pipeline_verification_warn():
    """流水线现形 payload(通过但带告警)→ status=warn(财报存疑)"""
    row = _make_row(
        payload={
            "verification": {
                "passed": True,
                "checks": {"disclosure_window": {"status": "WARN", "detail": "预约披露窗"}},
                "warnings": ["disclosure_window: 预约披露窗"],
            }
        }
    )
    item = _format_signal(row)
    assert item["earnings_verification"] is not None
    assert item["earnings_verification"]["status"] == "warn"


def test_signal_projection_direct_shape_extracts_all_fields():
    """直接键形态(earnings_verification.{status,report_date,single_quarter_yoy})原样提取"""
    row = _make_row(
        payload={
            "earnings_verification": {
                "status": "passed",
                "report_date": "2026-06-30",
                "single_quarter_yoy": 0.234,
            }
        }
    )
    item = _format_signal(row)
    assert item["earnings_verification"] == {
        "status": "passed",
        "report_date": "2026-06-30",
        "single_quarter_yoy": 0.234,
    }


def test_signal_projection_no_verification_is_null():
    """payload 无核验数据 / payload 非 dict → earnings_verification=null"""
    assert _format_signal(_make_row())["earnings_verification"] is None
    assert _format_signal(_make_row(payload=None))["earnings_verification"] is None
    assert _format_signal(_make_row(payload="junk"))["earnings_verification"] is None


def test_signal_projection_available_without_payload():
    """include_payload=False 时徽章数据仍从原始行 payload 提取(payload 字段仍为空)"""
    row = _make_row(
        payload={
            "verification": {
                "passed": False,
                "checks": {},
                "warnings": [],
            }
        }
    )
    item = _format_signal(row, include_payload=False, factor_snapshot=None)
    assert item["payload"] == {}
    assert item["earnings_verification"] is not None
    assert item["earnings_verification"]["status"] == "failed"


# ────────────────────────── dashboard brief:盘前决策优先 ──────────────────────────


def _brief_setup(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    app = FastAPI()
    app.include_router(dashboard_api.router, prefix="/api/dashboard")

    def _db():
        s = Session()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _db
    return TestClient(app), Session


def _seed(session, agent_name: str, analysis_date: str, title: str):
    session.add(
        AnalysisHistory(
            agent_name=agent_name,
            stock_symbol="*",
            analysis_date=analysis_date,
            title=title,
            content=f"{agent_name} 正文",
        )
    )
    session.commit()


def test_brief_premarket_prefers_premain_pipeline(monkeypatch):
    """type=premarket 优先取 premarket_pipeline(即便日期相同/更旧)"""
    client, Session = _brief_setup(monkeypatch)
    session = Session()
    try:
        _seed(session, "premarket_outlook", "2026-09-23", "盘前分析报告")
        _seed(session, "premarket_pipeline", "2026-09-22", "盘前决策报告")
    finally:
        session.close()
    data = client.get("/api/dashboard/brief", params={"type": "premarket"}).json()
    assert data.get("empty") is None
    assert data["title"] == "盘前决策报告"
    assert data["agent_name"] == "premarket_pipeline"
    assert data["agent_label"] == "盘前决策"


def test_brief_premarket_falls_back_to_outlook(monkeypatch):
    """无 premarket_pipeline 记录时回退 premarket_outlook"""
    client, Session = _brief_setup(monkeypatch)
    session = Session()
    try:
        _seed(session, "premarket_outlook", "2026-09-23", "盘前分析报告")
    finally:
        session.close()
    data = client.get("/api/dashboard/brief", params={"type": "premarket"}).json()
    assert data["title"] == "盘前分析报告"
    assert data["agent_label"] == "盘前分析"


def test_brief_premarket_empty_when_no_rows(monkeypatch):
    """两个 agent 都无记录 → empty 响应,不抛错"""
    client, _ = _brief_setup(monkeypatch)
    data = client.get("/api/dashboard/brief", params={"type": "premarket"}).json()
    assert data == {"empty": True, "type": "premarket", "agent_label": "盘前决策"}


def test_brief_eod_still_uses_daily_report(monkeypatch):
    """type=eod 行为不变:取 daily_report 最新记录"""
    client, Session = _brief_setup(monkeypatch)
    session = Session()
    try:
        _seed(session, "daily_report", "2026-09-22", "收盘复盘报告")
        _seed(session, "premarket_pipeline", "2026-09-23", "盘前决策报告")
    finally:
        session.close()
    data = client.get("/api/dashboard/brief", params={"type": "eod"}).json()
    assert data["title"] == "收盘复盘报告"
    assert data["agent_label"] == "收盘复盘"
