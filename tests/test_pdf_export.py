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


def test_assemble_report_markdown_mirrors_detail_page_sections():
    """从 raw_data 拼出的报告含详情页全部分节:PM/交易员/4分析师全文/多空辩论全文/风控辩论全文。"""
    from src.core.pdf_export import assemble_report_markdown

    raw = {
        "suggestion": {"action_label": "持有", "confidence": 5.0},
        "final_decision": "PM决策正文XYZ",
        "trader_plan": "交易员计划正文XYZ",
        "analyst_reports": {
            "market": "技术面分析正文XYZ", "social": "情绪面分析正文XYZ",
            "news": "新闻面分析正文XYZ", "fundamentals": "基本面分析正文XYZ",
        },
        "debate_history": {"history": "多头观点AAA 空头观点BBB", "judge_decision": "研究主管裁决XYZ"},
        "risk_debate": {"history": "激进CCC 保守DDD", "judge_decision": "风控裁决XYZ"},
    }
    md = assemble_report_markdown(raw)
    for must in [
        "PM决策正文XYZ", "交易员计划正文XYZ",
        "技术面分析正文XYZ", "情绪面分析正文XYZ", "新闻面分析正文XYZ", "基本面分析正文XYZ",
        "多头观点AAA", "空头观点BBB", "研究主管裁决XYZ",
        "激进CCC", "风控裁决XYZ",
        "技术分析师", "看多看空辩论", "风控辩论",
    ]:
        assert must in md, f"缺少: {must}"


def _mem_db():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    import src.web.models  # noqa: F401
    from src.web.database import Base

    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_pdf_endpoint_returns_full_detail_content():
    """端点:返回 application/pdf 附件,且含详情页完整内容(分析师/辩论全文,来自 raw_data,非仅 content 摘要)。"""
    from src.web.api import agents
    from src.web.models import AnalysisHistory

    db = _mem_db()
    try:
        db.add(AnalysisHistory(
            agent_name="tradingagents", stock_symbol="601238",
            analysis_date="2026-06-20", title="【深度】广汽集团(601238):持有",
            content="# 摘要\n\n**持有**",  # content 是精简版,不含下面这些
            raw_data={
                "suggestion": {"action_label": "持有", "confidence": 5.0},
                "final_decision": "PM决策正文",
                "analyst_reports": {"market": "技术面分析正文UNIQUE", "fundamentals": "基本面正文"},
                "debate_history": {"history": "多头观点UNIQUE 空头观点", "judge_decision": "研究主管裁决"},
                "risk_debate": {"history": "激进 保守", "judge_decision": "风控裁决"},
            },
        ))
        db.commit()
        resp = agents.export_tradingagents_analysis_pdf(
            stock_symbol="601238", analysis_date="2026-06-20", db=db)
        assert resp.media_type == "application/pdf"
        assert bytes(resp.body[:4]) == b"%PDF"
        assert "attachment" in resp.headers["content-disposition"]

        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(bytes(resp.body)))
        txt = "\n".join((p.extract_text() or "") for p in reader.pages)
        # content 摘要里没有的「分析师全文 / 辩论全文」确实进了 PDF
        assert "技术面分析正文UNIQUE" in txt
        assert "多头观点UNIQUE" in txt
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
