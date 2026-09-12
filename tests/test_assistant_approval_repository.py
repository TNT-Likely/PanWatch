"""Repository behavior for approval checkpoints and trusted tool policies."""

from datetime import UTC, datetime, timedelta

import pytest
from pan_agent import (
    AgentCheckpoint,
    ApprovalDecision,
    ModelMessage,
    PendingApproval,
    PermissionMode,
    RunRequest,
    RunResult,
    RunStatus,
    ToolCall,
    ToolRisk,
    ToolSpec,
)
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.platform.persistence.database import Base


def _repository():
    from src.modules.assistant.repository import AssistantRepository

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    repository = AssistantRepository(session)
    conversation = repository.create_conversation(stock_symbol=None, stock_market=None, initial_context=None)
    task = repository.create_task(conversation_id=conversation.id, user_message_id=None, context={})
    return engine, session, repository, task


def _checkpoint() -> AgentCheckpoint:
    return AgentCheckpoint(
        messages=[ModelMessage(role="user", content="创建提醒")],
        step_index=1,
        tool_calls_used=1,
        pending_approvals=[
            PendingApproval(
                call_id="call-1",
                tool_name="create_alert",
                risk=ToolRisk.WRITE,
                arguments={"symbol": "600519"},
            )
        ],
    )


def test_checkpoint_and_approval_decision_are_durable_and_exactly_once():
    engine, session, repository, task = _repository()
    checkpoint = _checkpoint()
    repository.save_checkpoint(task.id, checkpoint)
    approvals = repository.create_approvals(
        task.id,
        checkpoint.pending_approvals,
        expires_at=datetime.now(UTC) + timedelta(minutes=10),
    )

    first, accepted_first = repository.decide_approval(
        approvals[0].id,
        ApprovalDecision.APPROVED,
        decided_by="local",
    )
    second, accepted_second = repository.decide_approval(
        approvals[0].id,
        ApprovalDecision.REJECTED,
        decided_by="local",
    )

    assert repository.get_task_checkpoint(task.id) == checkpoint
    assert accepted_first is True
    assert accepted_second is False
    assert first.status == "approved"
    assert second.status == "approved"
    session.close()
    engine.dispose()


def test_permission_resolution_uses_tool_then_risk_defaults_and_safety_floors():
    engine, session, repository, _task = _repository()
    write_tool = ToolSpec(name="create_alert", title="提醒", description="write", risk=ToolRisk.WRITE)
    destructive = ToolSpec(name="delete_alert", title="删除", description="delete", risk=ToolRisk.DESTRUCTIVE)
    confirmed = ToolSpec(
        name="export_report",
        title="导出",
        description="export",
        risk=ToolRisk.READ,
        confirmation_required=True,
    )
    repository.upsert_tool_permission("local", "risk", ToolRisk.WRITE.value, PermissionMode.ALLOW)
    repository.upsert_tool_permission("local", "tool", "create_alert", PermissionMode.ASK)
    repository.upsert_tool_permission("local", "tool", "delete_alert", PermissionMode.ALLOW)
    repository.upsert_tool_permission("local", "tool", "export_report", PermissionMode.ALLOW)

    assert repository.resolve_permission(write_tool).mode is PermissionMode.ASK
    assert repository.resolve_permission(destructive).mode is PermissionMode.DENY
    assert repository.resolve_permission(confirmed).mode is PermissionMode.ASK
    session.close()
    engine.dispose()


def test_service_builds_a_stable_policy_snapshot_for_one_runtime():
    from src.modules.assistant.service import AssistantService

    engine, session, repository, _task = _repository()
    write_tool = ToolSpec(name="create_alert", title="提醒", description="write", risk=ToolRisk.WRITE)
    repository.upsert_tool_permission("local", "tool", "create_alert", PermissionMode.DENY)

    policy = AssistantService(repository).build_tool_policy()
    repository.upsert_tool_permission("local", "tool", "create_alert", PermissionMode.ALLOW)

    policy_request = RunRequest(run_id="policy", messages=[ModelMessage(role="user", content="x")])
    decision = __import__("asyncio").run(
        policy.decide(
            policy_request,
            write_tool,
            ToolCall(id="call-1", name="create_alert"),
        )
    )

    assert policy.is_tool_visible(policy_request, write_tool) is False
    assert decision.mode is PermissionMode.DENY
    session.close()
    engine.dispose()


def test_service_persists_a_waiting_batch_then_builds_exact_resume_decisions():
    from src.modules.assistant.service import AssistantService

    engine, session, repository, task = _repository()
    checkpoint = _checkpoint()
    service = AssistantService(repository)
    approvals = service.pause_task(
        task.id,
        RunResult(
            run_id=str(task.id),
            status=RunStatus.WAITING_FOR_APPROVAL,
            checkpoint=checkpoint,
            pending_approvals=checkpoint.pending_approvals,
        ),
    )

    outcome = service.resolve_approval_decision(approvals[0].id, ApprovalDecision.REJECTED)

    assert outcome.task.id == task.id
    assert outcome.checkpoint == checkpoint
    assert outcome.decisions == {"call-1": ApprovalDecision.REJECTED}
    session.close()
    engine.dispose()


def test_service_exposes_and_updates_tool_permission_settings_with_safety_floor():
    from src.modules.assistant.service import AssistantService

    engine, session, repository, _task = _repository()
    service = AssistantService(repository)

    initial = service.get_tool_permissions()
    defaults = {row["risk"]: row["mode"] for row in initial["defaults"]}
    assert defaults == {
        "read": "allow",
        "write": "ask",
        "external": "ask",
        "destructive": "deny",
    }

    updated = service.update_tool_permission(
        selector_kind="tool",
        selector_value="create_alert",
        mode=PermissionMode.ALLOW,
        risk=ToolRisk.WRITE,
    )
    assert {
        "selector_kind": "tool",
        "selector_value": "create_alert",
        "mode": "allow",
    } in updated["overrides"]

    with pytest.raises(ValueError, match="只能设为禁止"):
        service.update_tool_permission(
            selector_kind="risk",
            selector_value="destructive",
            mode=PermissionMode.ALLOW,
            risk=None,
        )
    session.close()
    engine.dispose()
