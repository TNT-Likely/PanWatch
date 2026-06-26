"""产业链自动分类测试。"""

from __future__ import annotations

from types import SimpleNamespace

from src.core.stock_industry_chain import classify_stock, load_chain_taxonomy


def test_classify_stock_by_symbol_mapping():
    """已知龙头代码应直接归入对应产业链层级。"""
    stock = SimpleNamespace(
        symbol="300308",
        name="中际旭创",
        market="CN",
        concept_tags_auto=[],
        concept_tags_manual=[],
    )
    result = classify_stock(stock, taxonomy=load_chain_taxonomy())
    assert result is not None
    assert result["sector"] == "AI"
    assert result["layer"] == "upstream"
    assert result["match_source"] == "symbol"


def test_classify_stock_by_concept_keywords():
    """概念标签关键词应匹配到中游/下游。"""
    upstream = SimpleNamespace(
        symbol="999999",
        name="测试光模块",
        market="CN",
        concept_tags_auto=["光模块", "CPO"],
        concept_tags_manual=[],
    )
    midstream = SimpleNamespace(
        symbol="888888",
        name="测试云",
        market="CN",
        concept_tags_auto=["云计算", "大模型"],
        concept_tags_manual=[],
    )
    downstream = SimpleNamespace(
        symbol="777777",
        name="测试营销",
        market="CN",
        concept_tags_auto=["AIGC", "数字营销"],
        concept_tags_manual=[],
    )
    taxonomy = load_chain_taxonomy()
    up = classify_stock(upstream, taxonomy=taxonomy)
    mid = classify_stock(midstream, taxonomy=taxonomy)
    down = classify_stock(downstream, taxonomy=taxonomy)
    assert up and up["layer"] == "upstream"
    assert mid and mid["layer"] == "midstream"
    assert down and down["layer"] == "downstream"


def test_classify_stock_returns_none_when_no_match():
    """无匹配关键词时应返回 None。"""
    stock = SimpleNamespace(
        symbol="600519",
        name="贵州茅台",
        market="CN",
        concept_tags_auto=["白酒"],
        concept_tags_manual=[],
    )
    assert classify_stock(stock, industry="白酒", taxonomy=load_chain_taxonomy()) is None
