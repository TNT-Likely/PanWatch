"""分析报告去重：每类每标的仅保留最新一份。"""

from datetime import date

from src.core.analysis_history import prune_duplicate_analysis_reports, save_analysis
from src.web.database import SessionLocal
from src.web.models import AnalysisHistory


def test_save_analysis_keeps_only_latest_per_agent_symbol():
    """同一 agent + 标的保存新报告后，旧日期报告应被删除。"""
    db = SessionLocal()
    try:
        db.query(AnalysisHistory).filter(
            AnalysisHistory.agent_name == "lmd_outlook",
            AnalysisHistory.stock_symbol == "TEST001",
        ).delete(synchronize_session=False)
        db.commit()
    finally:
        db.close()

    assert save_analysis(
        agent_name="lmd_outlook",
        stock_symbol="TEST001",
        content="旧报告",
        title="旧",
        analysis_date=date(2026, 6, 20),
    )
    assert save_analysis(
        agent_name="lmd_outlook",
        stock_symbol="TEST001",
        content="新报告",
        title="新",
        analysis_date=date(2026, 6, 25),
    )

    db = SessionLocal()
    try:
        rows = (
            db.query(AnalysisHistory)
            .filter(
                AnalysisHistory.agent_name == "lmd_outlook",
                AnalysisHistory.stock_symbol == "TEST001",
            )
            .order_by(AnalysisHistory.id.asc())
            .all()
        )
        assert len(rows) == 1
        assert rows[0].content == "新报告"
        assert rows[0].analysis_date == "2026-06-25"
    finally:
        db.close()


def test_prune_duplicate_analysis_reports():
    """批量去重应保留最新记录并删除其余。"""
    db = SessionLocal()
    try:
        db.query(AnalysisHistory).filter(
            AnalysisHistory.agent_name == "daily_report",
            AnalysisHistory.stock_symbol == "TEST002",
        ).delete(synchronize_session=False)
        db.add(
            AnalysisHistory(
                agent_name="daily_report",
                stock_symbol="TEST002",
                analysis_date="2026-06-01",
                title="a",
                content="a",
                raw_data={},
            )
        )
        db.add(
            AnalysisHistory(
                agent_name="daily_report",
                stock_symbol="TEST002",
                analysis_date="2026-06-10",
                title="b",
                content="b",
                raw_data={},
            )
        )
        db.commit()
    finally:
        db.close()

    removed = prune_duplicate_analysis_reports()
    assert removed >= 1

    db = SessionLocal()
    try:
        rows = (
            db.query(AnalysisHistory)
            .filter(
                AnalysisHistory.agent_name == "daily_report",
                AnalysisHistory.stock_symbol == "TEST002",
            )
            .all()
        )
        assert len(rows) == 1
        assert rows[0].analysis_date == "2026-06-10"
    finally:
        db.close()
