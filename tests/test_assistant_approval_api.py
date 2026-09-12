"""HTTP contract for pausing and resuming one durable assistant task."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pan_agent import (
    AgentCheckpoint,
    ApprovalDecision,
    EventType,
    ModelMessage,
    PendingApproval,
    RunResult,
    RunStatus,
    RuntimeEvent,
    ToolRisk,
)

import src.modules.assistant.api as assistant_api
from src.modules.assistant.service import (
    AssistantApprovalConflictError,
    AssistantApprovalExpiredError,
)


class _WaitingRuntime:
    async def run(self, _request, sink):
        await sink.publish(RuntimeEvent(type=EventType.RUN_CREATED, run_id="12"))
        pending = PendingApproval(
            call_id="call-1",
            tool_name="create_price_alert",
            risk=ToolRisk.WRITE,
            arguments={
                "symbol": "600519",
                "market": "CN",
                "direction": "above",
                "target_price": 1800,
            },
        )
        checkpoint = AgentCheckpoint(
            messages=[ModelMessage(role="user", content="创建提醒")],
            step_index=1,
            tool_calls_used=1,
            pending_approvals=[pending],
        )
        return RunResult(
            run_id="12",
            status=RunStatus.WAITING_FOR_APPROVAL,
            checkpoint=checkpoint,
            pending_approvals=[pending],
        )


class _ResumedRuntime:
    def __init__(self):
        self.resume_calls = []

    async def resume(self, request, checkpoint, decisions, sink):
        self.resume_calls.append((request, checkpoint, decisions))
        await sink.publish(
            RuntimeEvent(
                type=EventType.ANSWER_TOKEN, run_id="12", data={"token": "已恢复"}
            )
        )
        return RunResult(run_id="12", status=RunStatus.COMPLETED, answer="已恢复")


class _ApprovalService:
    def __init__(self, runtime):
        self.runtime = runtime
        self.recorded_assistant_messages = []
        self.finished = []
        self.paused_results = []
        self.decision_outcome = None

    def record_user_message(self, _conversation_id, _content):
        return SimpleNamespace(id=11)

    def create_task(self, _conversation_id, _user_message_id):
        return SimpleNamespace(id=12)

    def build_failover_client(self):
        return object()

    def build_runtime(self, _client):
        return self.runtime

    def get_conversation(self, _conversation_id):
        return SimpleNamespace(
            messages=[SimpleNamespace(role="user", content="创建提醒")]
        )

    def pause_task(self, task_id, result):
        self.paused_results.append((task_id, result))
        return [
            SimpleNamespace(
                id="approval-1",
                call_id="call-1",
                tool_name="create_price_alert",
                risk="write",
                arguments={
                    "symbol": "600519",
                    "market": "CN",
                    "direction": "above",
                    "target_price": 1800,
                },
                presentation={
                    "tool_title": "创建价格提醒",
                    "summary": "为 CN:600519 创建价格 ≥ 1800 的盘中提醒，冷却 30 分钟。",
                },
                expires_at=datetime.now(UTC) + timedelta(minutes=10),
            )
        ]

    def resolve_approval_decision(self, _approval_id, _decision):
        if isinstance(self.decision_outcome, Exception):
            raise self.decision_outcome
        return self.decision_outcome

    def record_assistant_message(self, _conversation_id, content):
        self.recorded_assistant_messages.append(content)
        return SimpleNamespace(id=13, content=content, created_at=None)

    def finish_task(self, _task_id, result, _final_message_id):
        self.finished.append((result.status.value, result.error_code))

    def fail_task(self, _task_id, error_code):
        self.finished.append(("failed", error_code))


async def _read_events(response) -> list[tuple[str, dict]]:
    chunks = [chunk async for chunk in response.body_iterator]
    events = []
    for block in "".join(chunks).strip().split("\n\n"):
        lines = block.splitlines()
        event = next(
            line.removeprefix("event: ") for line in lines if line.startswith("event: ")
        )
        data = next(
            line.removeprefix("data: ") for line in lines if line.startswith("data: ")
        )
        events.append((event, json.loads(data)))
    return events


def test_waiting_runtime_persists_then_streams_an_approval_and_pause():
    service = _ApprovalService(_WaitingRuntime())

    async def run():
        response = await assistant_api.stream_assistant_message(
            1,
            assistant_api.SendAssistantMessageCommand(content="创建提醒"),
            service,
        )
        return await _read_events(response)

    events = asyncio.run(run())

    assert events[-2][0] == "approval_required"
    assert events[-2][1]["approval_id"] == "approval-1"
    assert events[-2][1]["presentation"] == {
        "tool_title": "创建价格提醒",
        "summary": "为 CN:600519 创建价格 ≥ 1800 的盘中提醒，冷却 30 分钟。",
    }
    assert events[-2][1]["calls"] == [
        {
            "call_id": "call-1",
            "name": "create_price_alert",
            "risk": "write",
            "arguments": {
                "symbol": "600519",
                "market": "CN",
                "direction": "above",
                "target_price": 1800,
            },
        }
    ]
    assert events[-1] == ("paused", {"task_id": 12, "reason": "approval_required"})
    assert service.paused_results
    assert service.recorded_assistant_messages == []
    assert service.finished == []


def test_approved_decision_resumes_with_all_checkpoint_decisions_and_streams_done():
    runtime = _ResumedRuntime()
    service = _ApprovalService(runtime)
    checkpoint = AgentCheckpoint(
        messages=[ModelMessage(role="user", content="创建提醒")],
        step_index=1,
        tool_calls_used=1,
        pending_approvals=[
            PendingApproval(
                call_id="call-1", tool_name="create_alert", risk=ToolRisk.WRITE
            ),
        ],
    )
    service.decision_outcome = SimpleNamespace(
        task=SimpleNamespace(id=12, conversation_id=1),
        checkpoint=checkpoint,
        decisions={"call-1": ApprovalDecision.APPROVED},
    )

    async def run():
        response = await assistant_api.stream_assistant_approval_decision(
            "approval-1",
            assistant_api.ApprovalDecisionCommand(decision=ApprovalDecision.APPROVED),
            service,
        )
        return await _read_events(response)

    events = asyncio.run(run())

    assert runtime.resume_calls[0][2] == {"call-1": ApprovalDecision.APPROVED}
    assert events[-1][0] == "done"
    assert events[-1][1]["content"] == "已恢复"


@pytest.mark.parametrize(
    "error",
    [
        AssistantApprovalExpiredError("expired"),
        AssistantApprovalConflictError("already decided"),
    ],
)
def test_expired_or_repeated_decisions_cannot_start_another_resume_worker(error):
    service = _ApprovalService(_ResumedRuntime())
    service.decision_outcome = error

    async def run():
        with pytest.raises(HTTPException) as exc_info:
            await assistant_api.stream_assistant_approval_decision(
                "approval-1",
                assistant_api.ApprovalDecisionCommand(
                    decision=ApprovalDecision.APPROVED
                ),
                service,
            )
        return exc_info.value

    exc = asyncio.run(run())

    assert exc.status_code == 409
    assert service.runtime.resume_calls == []
