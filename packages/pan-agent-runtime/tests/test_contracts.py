from datetime import UTC, datetime

from pan_agent import (
    AgentCheckpoint,
    ModelMessage,
    PendingApproval,
    RunLimits,
    ToolResult,
    ToolRisk,
    ToolSpec,
)


def test_tool_schema_and_default_runtime_limits_are_stable():
    spec = ToolSpec(
        name="lookup",
        title="查询",
        description="Read a value",
        risk=ToolRisk.READ,
        input_schema={"type": "object", "properties": {"key": {"type": "string"}}},
    )

    assert spec.openai_schema()["function"]["name"] == "lookup"
    assert RunLimits().model_dump() == {
        "max_steps": 6,
        "max_tool_calls": 8,
        "tool_timeout_seconds": 20,
        "run_timeout_seconds": 90,
        "step_retry_count": 1,
    }


def test_successful_tool_results_require_freshness_and_provenance():
    observed_at = datetime.now(UTC)
    result = ToolResult.success(
        summary="查询完成",
        data={"price": 1},
        sources=[{"name": "行情源", "url": "https://example.test/quote"}],
        observed_at=observed_at,
    )

    assert result.ok is True
    assert result.observed_at == observed_at
    assert result.sources[0].name == "行情源"


def test_pending_approval_round_trips_through_a_json_checkpoint():
    checkpoint = AgentCheckpoint(
        messages=[ModelMessage(role="user", content="hello")],
        step_index=2,
        tool_calls_used=1,
        pending_approvals=[
            PendingApproval(
                call_id="call-1",
                tool_name="create_alert",
                risk=ToolRisk.WRITE,
                arguments={"symbol": "CN:601238"},
            )
        ],
    )

    restored = AgentCheckpoint.model_validate(checkpoint.model_dump(mode="json"))

    assert restored.pending_approvals[0].call_id == "call-1"
    assert restored.pending_approvals[0].arguments == {"symbol": "CN:601238"}
