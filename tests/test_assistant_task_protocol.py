from sqlalchemy import create_engine, text


def test_m125_adds_task_protocol_metadata_idempotently(tmp_path):
    from src.platform.persistence.migrations import (
        _m122_assistant_task_snapshots,
        _m123_assistant_approval_workflow,
        _m124_assistant_context_snapshots,
        _m125_assistant_task_protocol,
    )

    engine = create_engine(f"sqlite:///{tmp_path / 'task-protocol.db'}")
    with engine.begin() as conn:
        _m122_assistant_task_snapshots(conn)
        _m123_assistant_approval_workflow(conn)
        _m124_assistant_context_snapshots(conn)
        _m125_assistant_task_protocol(conn)
        _m125_assistant_task_protocol(conn)

        columns = {
            row[1]
            for row in conn.execute(text("PRAGMA table_info(assistant_task_runs)"))
        }

    engine.dispose()

    assert {
        "state_version",
        "current_step",
        "last_event_id",
        "checkpoint_id",
    } <= columns
