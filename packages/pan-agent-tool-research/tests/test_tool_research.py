import asyncio

import pytest
from pan_agent import (
    AgentRuntime,
    BeforeModelTurnContext,
    DuplicateToolName,
    ExtensionToolContext,
    ModelTurn,
    ReadOnlyToolPolicy,
    RunLimits,
    RunRequest,
    ToolCall,
    ToolExposure,
    ToolRegistry,
    ToolResult,
    ToolRisk,
    ToolSpec,
    UnknownTool,
)
from pan_agent_tool_research import (
    ToolDescriptor,
    ToolResearchPlugin,
    ToolResearchRequest,
    ToolResearchService,
)


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


def build_registry(executor=fake_executor) -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        ToolSpec(
            name="find_research_candidates",
            title="发现研究候选",
            description="筛选值得进一步研究的股票候选。",
            input_schema={"type": "object", "properties": {}},
        ),
        executor,
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
        executor,
    )
    return registry


def descriptors() -> list[ToolDescriptor]:
    return [
        descriptor(
            "find_research_candidates",
            title="发现研究候选",
            summary="从市场和持仓范围中筛选值得进一步研究的股票候选。",
            keywords=["发现机会", "研究", "候选", "筛选"],
        ),
        descriptor(
            "create_price_alert",
            title="创建价格提醒",
            summary="为股票创建价格触发提醒。",
            keywords=["提醒", "价格", "创建"],
            risk=ToolRisk.WRITE,
        ),
    ]


def build_service() -> ToolResearchService:
    return ToolResearchService(build_registry(), descriptors=descriptors())


def test_tool_research_selects_relevant_tool_with_explainable_reason():
    service = build_service()

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
    service = build_service()

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
    service = build_service()

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
        ToolResearchService(
            registry,
            descriptors=[
                descriptor(
                    "unknown",
                    title="未知",
                    summary="未知工具",
                    keywords=["未知"],
                )
            ],
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
    with pytest.raises(DuplicateToolName):
        ToolResearchService(registry, descriptors=[item, item])


def test_plugin_emits_generic_extension_events_and_can_select_in_active_mode():
    events = []
    service = build_service()
    context = BeforeModelTurnContext(
        request=request(),
        messages=tuple(request().messages),
        available_tools=tuple(build_registry().registered_tools()),
        policy=ReadOnlyToolPolicy(),
        emit_event=lambda name, data: _record_event(events, name, data),
    )

    shadow = ToolResearchPlugin(service, mode="shadow")
    assert asyncio.run(shadow.before_model_turn(context)) is None
    assert [name for name, _data in events] == [
        "started",
        "candidates_scored",
        "completed",
    ]

    active = ToolResearchPlugin(service, mode="active")
    decision = asyncio.run(active.before_model_turn(context))
    assert decision is not None
    assert list(decision.tool_names or []) == [
        "find_research_candidates",
        "create_price_alert",
    ]
    assert [tool.name for tool in decision.additional_tools] == ["tool_search"]


def test_active_plugin_search_returns_loaded_tool_references_without_executing_tools():
    service = build_service()
    plugin = ToolResearchPlugin(service, mode="active")
    request_value = request()
    search_spec = plugin._search_tool_spec()
    context = ExtensionToolContext(
        request=request_value,
        messages=tuple(request_value.messages),
        call=ToolCall(
            id="search-1",
            name="tool_search",
            arguments={"query": "发现研究机会"},
        ),
        tool=search_spec,
        available_tools=(search_spec,),
        policy=ReadOnlyToolPolicy(),
        emit_event=lambda _name, _data: _record_event([], _name, _data),
    )

    result = asyncio.run(plugin.handle_tool_call(context))

    assert result is not None
    assert result.ok is True
    assert result.data["loaded_tools"] == ["find_research_candidates"]


def test_active_plugin_search_failure_falls_back_without_emptying_direct_tools():
    class BrokenService:
        async def research(self, *_args, **_kwargs):
            raise RuntimeError("catalog unavailable")

    plugin = ToolResearchPlugin(BrokenService(), mode="active")
    request_value = request()
    search_spec = plugin._search_tool_spec()
    context = ExtensionToolContext(
        request=request_value,
        messages=tuple(request_value.messages),
        call=ToolCall(
            id="search-1",
            name="tool_search",
            arguments={"query": "查询工具"},
        ),
        tool=search_spec,
        available_tools=(search_spec,),
        policy=ReadOnlyToolPolicy(),
        emit_event=lambda _name, _data: _record_event([], _name, _data),
    )

    result = asyncio.run(plugin.handle_tool_call(context))

    assert result is not None
    assert result.ok is True
    assert result.data["loaded_tools"] == []
    assert result.data["fallback"] is True


def test_active_plugin_completes_model_side_search_and_deferred_tool_loading():
    async def candidate_executor(_request, _arguments):
        return ToolResult.success(
            summary="候选查询完成",
            data={"items": []},
            sources=[],
            observed_at=__import__("datetime").datetime.now(__import__("datetime").UTC),
        )

    registry = build_registry(executor=candidate_executor)
    registry.set_exposure("find_research_candidates", ToolExposure.DEFERRED)
    service = ToolResearchService(registry, descriptors=descriptors())
    plugin = ToolResearchPlugin(service, mode="active")

    class Model:
        def __init__(self):
            self.tool_names = []
            self.turn = 0

        async def run_turn(self, _messages, tools, _emit_token, tool_choice=None):
            self.tool_names.append([tool.name for tool in tools])
            self.turn += 1
            if self.turn == 1:
                return ModelTurn(
                    tool_calls=[
                        ToolCall(
                            id="search-1",
                            name="tool_search",
                            arguments={"query": "发现研究机会"},
                        )
                    ]
                )
            if self.turn == 2:
                return ModelTurn(
                    tool_calls=[
                        ToolCall(
                            id="candidate-1",
                            name="find_research_candidates",
                            arguments={},
                        )
                    ]
                )
            return ModelTurn(content="完成")

    model = Model()

    result = asyncio.run(
        AgentRuntime(
            model,
            registry,
            policy=ReadOnlyToolPolicy(),
            extensions=[plugin],
        ).run(
            RunRequest(
                run_id="tool-search-run",
                messages=[{"role": "user", "content": "发现几个研究机会"}],
                limits=RunLimits(max_steps=4),
            ),
            _CollectingSink(),
        )
    )

    assert result.status.value == "completed"
    assert model.tool_names[0] == ["tool_search"]
    assert "find_research_candidates" in model.tool_names[1]


class _CollectingSink:
    async def publish(self, _event):
        return None


async def _record_event(events, name, data):
    events.append((name, data))
