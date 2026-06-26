"""产业链自动分类测试。"""

from __future__ import annotations

from types import SimpleNamespace

from src.core.stock_industry_chain import (
    classify_stock,
    classify_stock_by_rules,
    clear_chain_taxonomy_cache,
    has_manual_industry_chain,
    load_chain_taxonomy,
    normalize_chain_display,
    resolve_industry_chain,
    set_manual_industry_chain,
)


def setup_function():
    clear_chain_taxonomy_cache()


def test_classify_stock_by_symbol_mapping():
    """已知龙头代码应直接归入对应 AI 轮动环节。"""
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
    assert result["layer"] == "cpo"
    assert result["layer_label"] == "CPO"
    assert result["display"] == "CPO"
    assert result["match_source"] == "symbol"


def test_classify_stock_by_concept_keywords():
    """命中 AI 赛道关键词后，轮动环节关键词应匹配到对应层级。"""
    cpo = SimpleNamespace(
        symbol="999999",
        name="测试光模块",
        market="CN",
        concept_tags_auto=["人工智能", "光模块", "CPO"],
        concept_tags_manual=[],
    )
    cloud_llm = SimpleNamespace(
        symbol="888888",
        name="测试大模型",
        market="CN",
        concept_tags_auto=["人工智能", "大模型", "AI平台"],
        concept_tags_manual=[],
    )
    idc = SimpleNamespace(
        symbol="777777",
        name="测试智算",
        market="CN",
        concept_tags_auto=["人工智能", "算力租赁", "智算"],
        concept_tags_manual=[],
    )
    physical_ai = SimpleNamespace(
        symbol="666666",
        name="测试机器人",
        market="CN",
        concept_tags_auto=["人工智能", "人形机器人", "机器视觉"],
        concept_tags_manual=[],
    )
    taxonomy = load_chain_taxonomy()
    cpo_result = classify_stock(cpo, taxonomy=taxonomy, use_ai=False)
    llm_result = classify_stock(cloud_llm, taxonomy=taxonomy, use_ai=False)
    idc_result = classify_stock(idc, taxonomy=taxonomy, use_ai=False)
    physical_result = classify_stock(physical_ai, taxonomy=taxonomy, use_ai=False)
    assert cpo_result and cpo_result["layer"] == "cpo"
    assert llm_result and llm_result["layer"] == "cloud_llm"
    assert idc_result and idc_result["layer"] == "idc"
    assert physical_result and physical_result["layer"] == "physical_ai"


def test_classify_stock_returns_other_when_no_match():
    """无匹配关键词时应归入「其他」。"""
    stock = SimpleNamespace(
        symbol="600519",
        name="贵州茅台",
        market="CN",
        concept_tags_auto=["白酒"],
        concept_tags_manual=[],
    )
    result = classify_stock(stock, industry="白酒", taxonomy=load_chain_taxonomy(), use_ai=False)
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
        result = classify_stock(stock, taxonomy=taxonomy, use_ai=False)
        assert result is not None
        assert result["sector"] == "OTHER"
        assert result["layer"] == "other"


def test_classify_stock_server_after_semi_equip():
    """服务器龙头应归入服务器环节，材料设备龙头归入材料&设备。"""
    taxonomy = load_chain_taxonomy()
    server = classify_stock(
        SimpleNamespace(
            symbol="000977",
            name="浪潮信息",
            market="CN",
            concept_tags_auto=[],
            concept_tags_manual=[],
        ),
        taxonomy=taxonomy,
    )
    equip = classify_stock(
        SimpleNamespace(
            symbol="002371",
            name="北方华创",
            market="CN",
            concept_tags_auto=[],
            concept_tags_manual=[],
        ),
        taxonomy=taxonomy,
    )
    assert server and server["layer"] == "server"
    assert equip and equip["layer"] == "semi_pcb_equip"


def test_normalize_chain_display_legacy_layers():
    """旧版分层 key 应映射为新版轮动环节展示名。"""
    taxonomy = load_chain_taxonomy()
    assert normalize_chain_display(
        {"sector": "AI", "layer": "foundation", "display": "人工智能·底层"},
        taxonomy=taxonomy,
    ) == "材料&设备"
    assert normalize_chain_display(
        {"sector": "AI", "layer": "application", "display": "人工智能·应用"},
        taxonomy=taxonomy,
    ) == "物理AI"
    assert normalize_chain_display(
        {"sector": "OTHER", "layer": "other", "display": "其他"},
        taxonomy=taxonomy,
    ) == "其他"


def test_classify_stock_rules_only_for_generic_optics():
    """光模块等概念在未命中 AI 赛道时，规则层应归入其他（AI 层另测）。"""
    stock = SimpleNamespace(
        symbol="234567",
        name="测试光通信",
        market="CN",
        concept_tags_auto=["光模块", "光通信", "5G"],
        concept_tags_manual=[],
    )
    result = classify_stock_by_rules(stock, taxonomy=load_chain_taxonomy())
    assert result["layer"] == "other"


def test_empty_manual_chain_does_not_force_other():
    """空的 industry_chain_manual 不应覆盖自动分类为「其他」。"""
    stock = SimpleNamespace(
        symbol="NVDA",
        name="英伟达",
        market="US",
        industry_chain_auto={
            "sector": "AI",
            "layer": "foundation",
            "layer_label": "底层",
            "match_source": "symbol",
        },
        industry_chain_manual={},
    )
    assert not has_manual_industry_chain(stock)
    resolved = resolve_industry_chain(stock)
    assert resolved is not None
    assert resolved["layer"] == "foundation"
    assert resolved["sector"] == "AI"
    assert normalize_chain_display(resolved, taxonomy=load_chain_taxonomy()) == "材料&设备"


def test_resolve_industry_chain_manual_overrides_auto(monkeypatch):
    """手动分类应覆盖自动分类结果。"""
    stock = SimpleNamespace(
        symbol="600172",
        name="黄河旋风",
        market="CN",
        industry_chain_auto={
            "sector": "OTHER",
            "layer": "other",
            "layer_label": "其他",
            "match_source": "fallback",
        },
        industry_chain_manual={"sector": "AI", "layer": "power"},
    )
    resolved = resolve_industry_chain(stock)
    assert resolved is not None
    assert resolved["layer"] == "power"
    assert resolved["source"] == "manual"
    assert resolved["match_source"] == "manual"


def test_classify_stock_uses_ai_when_rules_fail(monkeypatch):
    """规则归为其他时，应尝试 AI 分类。"""
    stock = SimpleNamespace(
        symbol="234567",
        name="测试光通信",
        market="CN",
        concept_tags_auto=["光模块", "光通信"],
        concept_tags_manual=[],
    )

    async def _fake_ai(*_args, **_kwargs):
        return {
            "sector": "AI",
            "layer": "cpo",
            "layer_label": "CPO",
            "display": "CPO",
            "match_source": "ai",
            "score": 50,
            "matched": ["ai:cpo"],
        }

    monkeypatch.setattr(
        "src.core.stock_industry_chain.classify_stock_by_ai",
        _fake_ai,
    )
    result = classify_stock(stock, taxonomy=load_chain_taxonomy(), use_ai=True)
    assert result["layer"] == "cpo"
    assert result["match_source"] == "ai"
