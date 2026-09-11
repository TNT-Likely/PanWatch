import pytest
from pan_agent import (
    DuplicateToolName,
    ReadOnlyToolPolicy,
    RunRequest,
    ToolRegistry,
    ToolRisk,
    ToolSpec,
)


async def fake_executor(_request, _arguments):  # pragma: no cover - registry policy test
    raise AssertionError("must not execute")


def read_spec(name: str) -> ToolSpec:
    return ToolSpec(name=name, title=name, description="read value", risk=ToolRisk.READ,
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
