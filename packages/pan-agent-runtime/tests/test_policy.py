import asyncio

from pan_agent import (
    ReadOnlyToolPolicy,
    RunRequest,
    ToolCall,
    ToolPermissionDecision,
    ToolRisk,
    ToolSpec,
)


def request() -> RunRequest:
    return RunRequest(run_id="policy-run", messages=[{"role": "user", "content": "hello"}])


def test_read_only_policy_hides_write_tools_and_allows_read_without_confirmation():
    policy = ReadOnlyToolPolicy()
    read = ToolSpec(name="lookup", title="Lookup", description="Read", risk=ToolRisk.READ)
    write = ToolSpec(name="create_alert", title="Alert", description="Write", risk=ToolRisk.WRITE)

    assert policy.is_tool_visible(request(), read) is True
    assert policy.is_tool_visible(request(), write) is False
    assert asyncio.run(policy.decide(request(), read, ToolCall(id="c1", name="lookup"))) == ToolPermissionDecision.allow()


def test_read_only_policy_denies_direct_confirmation_required_calls():
    policy = ReadOnlyToolPolicy()
    confirmed_read = ToolSpec(
        name="export_report",
        title="Export report",
        description="Read with confirmation",
        risk=ToolRisk.READ,
        confirmation_required=True,
    )

    decision = asyncio.run(policy.decide(request(), confirmed_read, ToolCall(id="c2", name="export_report")))

    assert decision.mode.value == "deny"
    assert decision.reason
