import pytest

from pan_agent import DisallowedTool, DuplicateToolName, ToolRegistry, ToolRisk, ToolSpec


async def fake_executor(_request, _arguments):  # pragma: no cover - registry policy test
    raise AssertionError("must not execute")


def read_spec(name: str) -> ToolSpec:
    return ToolSpec(name=name, title=name, description="read value", risk=ToolRisk.READ,
                    input_schema={"type": "object", "properties": {}})


def test_write_confirmation_and_duplicate_tools_are_rejected():
    registry = ToolRegistry()
    registry.register(read_spec("lookup"), fake_executor)

    with pytest.raises(DuplicateToolName):
        registry.register(read_spec("lookup"), fake_executor)
    with pytest.raises(DisallowedTool):
        registry.register(ToolSpec(name="delete", title="delete", description="write", risk=ToolRisk.WRITE,
                                   input_schema={"type": "object", "properties": {}}), fake_executor)
    with pytest.raises(DisallowedTool):
        registry.register(ToolSpec(name="confirm", title="confirm", description="confirm", risk=ToolRisk.READ,
                                   confirmation_required=True,
                                   input_schema={"type": "object", "properties": {}}), fake_executor)

