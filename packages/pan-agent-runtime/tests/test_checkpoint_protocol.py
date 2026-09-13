from datetime import UTC

from pan_agent import (
    AgentCheckpoint,
    CheckpointEnvelope,
    CheckpointReason,
    EventType,
    ModelMessage,
    RuntimeEvent,
)


def test_checkpoint_envelope_round_trips_provider_neutral_state():
    checkpoint = AgentCheckpoint(
        messages=[ModelMessage(role="user", content="继续分析")],
        answer="已完成第一步",
        step_index=2,
        tool_calls_used=3,
    )

    envelope = CheckpointEnvelope.from_checkpoint(
        run_id="run-42",
        checkpoint=checkpoint,
        reason=CheckpointReason.TOOL_COMPLETED,
        runtime_version="pan-agent-runtime@0.1.0",
    )
    restored = CheckpointEnvelope.model_validate(envelope.model_dump(mode="json"))

    assert restored.schema_version == 1
    assert restored.run_id == "run-42"
    assert restored.reason is CheckpointReason.TOOL_COMPLETED
    assert restored.to_checkpoint() == checkpoint
    assert restored.created_at.tzinfo is not None


def test_runtime_event_has_replay_metadata_and_utc_timestamp():
    event = RuntimeEvent(type=EventType.RUN_CREATED, run_id="run-42")

    assert event.schema_version == 1
    assert event.event_id
    assert event.occurred_at.tzinfo is UTC
