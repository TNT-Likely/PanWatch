"""Forward-only schema coverage for durable assistant approval records."""

from sqlalchemy import create_engine, text


def test_m123_adds_approval_workflow_to_the_m122_assistant_schema(tmp_path):
    from src.platform.persistence.migrations import (
        _m122_assistant_task_snapshots,
        _m123_assistant_approval_workflow,
    )

    engine = create_engine(f"sqlite:///{tmp_path / 'legacy-assistant.db'}")
    with engine.begin() as conn:
        _m122_assistant_task_snapshots(conn)
        _m123_assistant_approval_workflow(conn)
        task_columns = {row[1] for row in conn.execute(text("PRAGMA table_info(assistant_task_runs)"))}
        invocation_columns = {row[1] for row in conn.execute(text("PRAGMA table_info(assistant_tool_invocations)"))}
        tables = {row[0] for row in conn.execute(text("SELECT name FROM sqlite_master WHERE type = 'table'"))}
        approval_indexes = {row[1] for row in conn.execute(text("PRAGMA index_list(assistant_tool_approvals)"))}

        # Replaying a forward migration must remain safe during application startup.
        _m123_assistant_approval_workflow(conn)
    engine.dispose()

    assert {"checkpoint"} <= task_columns
    assert {"arguments", "risk"} <= invocation_columns
    assert tables >= {"assistant_tool_approvals", "assistant_tool_permissions"}
    assert approval_indexes >= {"ux_assistant_approval_run_call"}


def test_m124_adds_context_snapshots_idempotently(tmp_path):
    from src.platform.persistence.migrations import _m124_assistant_context_snapshots

    engine = create_engine(f"sqlite:///{tmp_path / 'context-snapshot.db'}")
    with engine.begin() as conn:
        _m124_assistant_context_snapshots(conn)
        _m124_assistant_context_snapshots(conn)
        columns = {
            row[1]
            for row in conn.execute(text("PRAGMA table_info(assistant_context_snapshots)"))
        }
        indexes = {
            row[1]
            for row in conn.execute(text("PRAGMA index_list(assistant_context_snapshots)"))
        }
    engine.dispose()

    assert {"conversation_id", "version", "summary", "usage_before", "usage_after"} <= columns
    assert "ux_assistant_context_snapshot_version" in indexes
