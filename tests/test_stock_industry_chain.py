"""产业链自动分类测试。"""

from __future__ import annotations

from types import SimpleNamespace

from src.core.stock_industry_chain import (
    classify_stock,
    clear_chain_taxonomy_cache,
    load_chain_taxonomy,
)


def setup_function():
    clear_chain_taxonomy_cache()


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
    assert result["layer"] == "foundation"
    assert result["layer_label"] == "底层"
    assert result["match_source"] == "symbol"


def test_classify_stock_by_concept_keywords():
    """命中 AI 赛道关键词后，层级关键词应匹配到底层/中间件/应用。"""
    foundation = SimpleNamespace(
        symbol="999999",
        name="测试光模块",
        market="CN",
        concept_tags_auto=["人工智能", "光模块", "CPO"],
        concept_tags_manual=[],
    )
    middleware = SimpleNamespace(
        symbol="888888",
        name="测试大模型",
        market="CN",
        concept_tags_auto=["人工智能", "大模型", "AI平台"],
        concept_tags_manual=[],
    )
    integration = SimpleNamespace(
        symbol="777777",
        name="测试智算",
        market="CN",
        concept_tags_auto=["人工智能", "算力租赁", "智算"],
        concept_tags_manual=[],
    )
    application = SimpleNamespace(
        symbol="666666",
        name="测试营销",
        market="CN",
        concept_tags_auto=["AIGC", "数字营销"],
        concept_tags_manual=[],
    )
    taxonomy = load_chain_taxonomy()
    base = classify_stock(foundation, taxonomy=taxonomy)
    mid = classify_stock(middleware, taxonomy=taxonomy)
    inte = classify_stock(integration, taxonomy=taxonomy)
    app = classify_stock(application, taxonomy=taxonomy)
    assert base and base["layer"] == "foundation"
    assert mid and mid["layer"] == "middleware"
    assert inte and inte["layer"] == "integration"
    assert app and app["layer"] == "application"


def test_classify_stock_returns_other_when_no_match():
    """无匹配关键词时应归入「其他」。"""
    stock = SimpleNamespace(
        symbol="600519",
        name="贵州茅台",
        market="CN",
        concept_tags_auto=["白酒"],
        concept_tags_manual=[],
    )
    result = classify_stock(stock, industry="白酒", taxonomy=load_chain_taxonomy())
    assert result is not None
    assert result["sector"] == "OTHER"
    assert result["layer"] == "other"
    assert result["display"] == "其他"
    assert result["match_source"] == "fallback"


def test_classify_stock_rejects_generic_concepts_without_ai_sector():
    """仅有泛行业概念、未命中 AI 赛道时，不应归入人工智能产业链。"""
    cases = [
        SimpleNamespace(
            symbol="600172",
            name="黄河旋风",
            market="CN",
            concept_tags_auto=["电力设备", "智能电网", "超硬材料"],
            concept_tags_manual=[],
        ),
        SimpleNamespace(
            symbol="123456",
            name="测试半导体",
            market="CN",
            concept_tags_auto=["半导体", "集成电路", "芯片"],
            concept_tags_manual=[],
        ),
        SimpleNamespace(
            symbol="234567",
            name="测试光通信",
            market="CN",
            concept_tags_auto=["光模块", "光通信", "5G"],
            concept_tags_manual=[],
        ),
        SimpleNamespace(
            symbol="345678",
            name="测试游戏",
            market="CN",
            concept_tags_auto=["游戏", "传媒", "影视"],
            concept_tags_manual=[],
        ),
    ]
    taxonomy = load_chain_taxonomy()
    for stock in cases:
        result = classify_stock(stock, taxonomy=taxonomy)
        assert result is not None
        assert result["sector"] == "OTHER"
        assert result["layer"] == "other"
