"""详情报告导出 PDF(后台直出:xhtml2pdf + reportlab STSong-Light 中文字体)。"""

from __future__ import annotations

import io


def test_render_pdf_returns_valid_bytes_with_chinese():
    """markdown→PDF:返回合法 PDF 字节,且中文进入文本层(非豆腐块、可复制)。"""
    from src.core.pdf_export import render_analysis_pdf

    md = "# 广汽集团(601238)深度分析\n\n**最终决策:持有**\n\n- 多头:业绩拐点确认\n- 空头:估值偏高"
    data = render_analysis_pdf("【深度】广汽集团(601238):持有", md)
    assert isinstance(data, (bytes, bytearray))
    assert bytes(data[:4]) == b"%PDF"
    assert len(data) > 1500

    from pypdf import PdfReader

    txt = PdfReader(io.BytesIO(bytes(data))).pages[0].extract_text() or ""
    assert "广汽集团" in txt
    assert "持有" in txt


def test_render_pdf_handles_empty_markdown():
    """空正文也不崩,仍返回合法 PDF(至少有标题)。"""
    from src.core.pdf_export import render_analysis_pdf

    data = render_analysis_pdf("标题", "")
    assert bytes(data[:4]) == b"%PDF"


def _mem_db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import src.web.models  # noqa: F401
    from src.web.database import Base

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_pdf_endpoint_returns_pdf_for_existing_record():
    """端点:存在记录 → 返回 application/pdf 附件文件。"""
    from src.web.api import agents
    from src.web.models import AnalysisHistory

    db = _mem_db()
    try:
        db.add(AnalysisHistory(
            agent_name="tradingagents", stock_symbol="601238",
            analysis_date="2026-06-20", title="【深度】广汽集团(601238):持有",
            content="# 广汽集团深度分析\n\n**持有** 置信度 5/10",
        ))
        db.commit()
        resp = agents.export_tradingagents_analysis_pdf(
            stock_symbol="601238", analysis_date="2026-06-20", db=db)
        assert resp.media_type == "application/pdf"
        assert bytes(resp.body[:4]) == b"%PDF"
        assert "attachment" in resp.headers["content-disposition"]
    finally:
        db.close()


def test_pdf_endpoint_404_when_missing():
    """端点:无记录 → HTTP 404。"""
    import pytest
    from fastapi import HTTPException

    from src.web.api import agents

    db = _mem_db()
    try:
        with pytest.raises(HTTPException) as ei:
            agents.export_tradingagents_analysis_pdf(
                stock_symbol="000000", analysis_date="2026-06-20", db=db)
        assert ei.value.status_code == 404
    finally:
        db.close()
