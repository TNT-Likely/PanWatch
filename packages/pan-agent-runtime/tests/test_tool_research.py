import asyncio

import pytest
from pan_agent import (
    DuplicateToolName,
    ReadOnlyToolPolicy,
    RunRequest,
    ToolRegistry,
    ToolResearchRequest,
    ToolResearchService,
    ToolRisk,
    ToolSpec,
    UnknownTool,
)
from pan_agent.tool_research import ToolDescriptor


async def fake_executor(_request, _arguments):
    raise AssertionError("tool research must not execute tools")


def request(content: str = "发现研究机会") -> RunRequest:
    return RunRequest(
        run_id="research-run",
        messages=[{"role": "user", "content": content}],
    )


def descriptor(
    name: str,
    *,
    title: str,
    summary: str,
    keywords: list[str],
    risk: ToolRisk = ToolRisk.READ,
    domain: str = "investment_research",
) -> ToolDescriptor:
    return ToolDescriptor(
        tool_name=name,
        title=title,
        summary=summary,
        keywords=keywords,
        aliases=keywords,
        domain=domain,
        capabilities=keywords,
        risk=risk,
        confirmation_required=risk is not ToolRisk.READ,
    )


def build_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="find_research_candidates",
            title="发现研究候选",
            description="筛选值得进一步研究的股票候选。",
            input_schema={"type": "object", "properties": {}},
        ),
        fake_executor,
    )
    registry.register(
        ToolSpec(
            name="create_price_alert",
            title="创建价格提醒",
            description="创建价格提醒。",
            risk=ToolRisk.WRITE,
            confirmation_required=True,
            input_schema={"type": "object", "properties": {}},
        ),
        fake_executor,
    )
    registry.register_descriptor(
        descriptor(
            "find_research_candidates",
            title="发现研究候选",
            summary="从市场和持仓范围中筛选值得进一步研究的股票候选。",
            keywords=["发现机会", "研究", "候选", "筛选"],
        )
    )
    registry.register_descriptor(
        descriptor(
            "create_price_alert",
            title="创建价格提醒",
            summary="为股票创建价格触发提醒。",
            keywords=["提醒", "价格", "创建"],
            risk=ToolRisk.WRITE,
        )
    )
    return registry


def test_tool_research_selects_relevant_tool_with_explainable_reason():
    service = ToolResearchService(build_registry())

    result = asyncio.run(
        service.research(
            ToolResearchRequest(query="帮我发现几个新的研究机会"),
            policy=ReadOnlyToolPolicy(),
            runtime_request=request(),
        )
    )

    assert result.fallback is False
    assert result.selected_tools == ["find_research_candidates"]
    assert result.candidates[0].tool_name == "find_research_candidates"
    assert result.candidates[0].score > 0
    assert result.candidates[0].match_reasons
    assert result.candidates[0].domain == "investment_research"


def test_tool_research_hard_filters_write_tools_and_denied_tools():
    service = ToolResearchService(build_registry())

    result = asyncio.run(
        service.research(
            ToolResearchRequest(query="创建价格提醒"),
            policy=ReadOnlyToolPolicy(),
            runtime_request=request("创建价格提醒"),
        )
    )

    assert "create_price_alert" not in result.selected_tools
    assert result.selected_tools == []


def test_tool_research_returns_empty_result_for_no_match_without_inventing_tools():
    service = ToolResearchService(build_registry())

    result = asyncio.run(
        service.research(
            ToolResearchRequest(query="写一封邮件"),
            policy=ReadOnlyToolPolicy(),
            runtime_request=request("写一封邮件"),
        )
    )

    assert result.fallback is False
    assert result.selected_tools == []
    assert result.candidates == []


def test_registry_rejects_unknown_or_duplicate_descriptors():
    registry = ToolRegistry()
    with pytest.raises(UnknownTool):
        registry.register_descriptor(
            descriptor(
                "unknown",
                title="未知",
                summary="未知工具",
                keywords=["未知"],
            )
        )

    registry.register(
        ToolSpec(
            name="lookup",
            title="查询",
            description="查询值。",
            input_schema={"type": "object", "properties": {}},
        ),
        fake_executor,
    )
    item = descriptor(
        "lookup", title="查询", summary="查询值。", keywords=["查询"]
    )
    registry.register_descriptor(item)
    with pytest.raises(DuplicateToolName):
        registry.register_descriptor(item)
