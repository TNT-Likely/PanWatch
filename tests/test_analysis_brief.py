"""analysis_brief 模块单元测试。"""

from types import SimpleNamespace

from src.core.analysis_brief import format_deep_brief, format_lmd_brief


def test_format_lmd_brief_returns_none_without_report():
    """无老马视角报告时应返回 None。"""
    assert format_lmd_brief(None) is None


def test_format_lmd_brief_from_cached_snapshot():
    """有结构化快照时应拼出老马视角摘要。"""
    record = SimpleNamespace(
        analysis_date="2026-06-25",
        content="",
        raw_data={
            "lmd_snapshot": {
                "has_report": True,
                "valuation_score": 68,
                "valuation_verdict": "偏贵",
                "expectation_hint": "预期已price-in",
            }
        },
    )
    brief = format_lmd_brief(record)
    assert brief is not None
    assert "老马视角" in brief
    assert "估值68分" in brief
    assert "偏贵" in brief


def test_format_deep_brief_from_suggestion():
    """深度分析应从 raw_data.suggestion 提取结论。"""
    record = SimpleNamespace(
        analysis_date="2026-06-25",
        title="",
        content="",
        raw_data={
            "suggestion": {
                "action_label": "观望",
                "confidence": 6.5,
                "reason": "短期波动加大，等待更清晰信号",
            }
        },
    )
    brief = format_deep_brief(record)
    assert brief is not None
    assert "深度分析" in brief
    assert "观望" in brief
    assert "置信度 6.5/10" in brief


def test_format_deep_brief_returns_none_when_empty():
    """无深度分析记录时应返回 None。"""
    assert format_deep_brief(None) is None
