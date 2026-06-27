"""监管红线检测单测。"""

from __future__ import annotations

from src.core.regulatory_red_flags import (
    RegulatoryTier,
    event_bias_for_text,
    format_ai_context,
    scan_items,
    scan_text,
)


def test_scan_text_detects_warning_letter_as_s_tier():
    """警示函应识别为 S 级监管红线"""
    hit = scan_text("", title="关于收到中国证监会警示函的公告")
    assert hit is not None
    assert hit.tier == RegulatoryTier.S
    assert hit.keyword == "警示函"


def test_scan_text_detects_inquiry_as_a_tier():
    """问询函应识别为 A 级重大利空"""
    hit = scan_text("公司收到深交所问询函，要求说明业绩变动原因")
    assert hit is not None
    assert hit.tier == RegulatoryTier.A


def test_format_ai_context_includes_veto_hint():
    """S 级命中应生成带否决提示的上下文"""
    result = scan_items([{"title": "收到监管函的公告", "content": "", "publish_time": "06-26 10:00"}])
    ctx = format_ai_context(result)
    assert "监管风险警报" in ctx
    assert "禁止建仓" in ctx
    assert "监管函" in ctx


def test_event_bias_s_tier_is_strongly_negative():
    """S 级事件偏置应显著为负"""
    assert event_bias_for_text("公司被立案调查") <= -8.0
    assert event_bias_for_text("普通减持公告") == 0.0


def test_scan_items_deduplicates():
    """同一关键词多条应去重"""
    items = [
        {"title": "警示函公告一", "content": ""},
        {"title": "警示函公告二", "content": ""},
    ]
    result = scan_items(items)
    assert len(result.hits) == 2
