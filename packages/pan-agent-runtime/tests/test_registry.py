import pytest
from pan_agent import (
    DuplicateToolName,
    ReadOnlyToolPolicy,
    RunRequest,
    ToolExposure,
    ToolRegistry,
    ToolRisk,
    ToolSpec,
)


async def fake_executor(_request, _arguments):  # pragma: no cover - registry policy test
    raise AssertionError("must not execute")


def read_spec(name: str, *, exposure: ToolExposure = ToolExposure.DIRECT) -> ToolSpec:
    return ToolSpec(name=name, title=name, description="read value", risk=ToolRisk.READ,
                    exposure=exposure,
                    input_schema={"type": "object", "properties": {}})


def write_spec(name: str) -> ToolSpec:
    return ToolSpec(name=name, title=name, description="write value", risk=ToolRisk.WRITE,
                    input_schema={"type": "object", "properties": {}})


def request() -> RunRequest:
    return RunRequest(run_id="registry-run", messages=[{"role": "user", "content": "hello"}])


def test_duplicate_tool_names_are_rejected():
    registry = ToolRegistry()
    registry.register(read_spec("lookup"), fake_executor)

    with pytest.raises(DuplicateToolName):
        registry.register(read_spec("lookup"), fake_executor)


def test_registry_registers_write_tools_but_default_policy_hides_them_from_the_model():
    registry = ToolRegistry()
    registry.register(write_spec("create_alert"), fake_executor)

    assert registry.model_tools(request(), ReadOnlyToolPolicy()) == []
    assert registry.registered_tools()[0].name == "create_alert"


def test_registry_can_limit_model_tools_by_names_without_changing_policy_checks():
    registry = ToolRegistry()
    registry.register(read_spec("lookup"), fake_executor)
    registry.register(read_spec("search"), fake_executor)

    assert [tool.name for tool in registry.model_tools(
        request(), ReadOnlyToolPolicy(), names=["search"]
    )] == ["search"]


def test_registry_hides_deferred_and_hidden_tools_until_explicitly_loaded():
    registry = ToolRegistry()
    registry.register(read_spec("direct"), fake_executor)
    registry.register(read_spec("deferred", exposure=ToolExposure.DEFERRED), fake_executor)
    registry.register(read_spec("hidden", exposure=ToolExposure.HIDDEN), fake_executor)

    assert [tool.name for tool in registry.model_tools(request(), ReadOnlyToolPolicy())] == [
        "direct"
    ]
    assert [tool.name for tool in registry.model_tools(
        request(), ReadOnlyToolPolicy(), names=["deferred"], include_deferred=True
    )] == ["deferred"]
    assert registry.model_tools(
        request(), ReadOnlyToolPolicy(), names=["hidden"], include_deferred=True
    ) == []


def test_registry_keeps_explicitly_allowlisted_deferred_tool_visible():
    registry = ToolRegistry()
    registry.register(read_spec("deferred", exposure=ToolExposure.DEFERRED), fake_executor)
    allowed_request = RunRequest(
        run_id="registry-action",
        messages=[{"role": "user", "content": "执行指定操作"}],
        context={"allowed_tool_names": ["deferred"]},
    )

    assert [tool.name for tool in registry.model_tools(
        allowed_request, ReadOnlyToolPolicy()
    )] == ["deferred"]
