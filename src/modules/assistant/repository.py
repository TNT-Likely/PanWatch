"""Persistence operations for assistant conversations and messages."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from pan_agent import (
    AgentCheckpoint,
    ApprovalDecision,
    CheckpointEnvelope,
    CheckpointReason,
    ContextCompressionMode,
    ContextSummary,
    ContextUsage,
    PendingApproval,
    PermissionMode,
    ToolPermissionDecision,
    ToolRisk,
    ToolSpec,
)
from pan_agent import (
    __version__ as PAN_AGENT_RUNTIME_VERSION,
)
from sqlalchemy.orm import Session
from sqlalchemy.sql import func

# 助手表由共享持久化平台注册；repository 是其唯一的模块内访问边界，
# 不需要再经由一个只做 re-export 的 ``assistant.models`` 转发层。
from src.platform.persistence.models import (
    AssistantContextSnapshot,
    AssistantTaskEvent,
    AssistantTaskNotification,
    NotificationEvent,
    NotificationReceipt,
    AssistantTaskRun,
    AssistantToolApproval,
    AssistantToolInvocation,
    AssistantToolPermission,
    ChatConversation,
    ChatMessage,
)
from src.platform.tasking.contracts import TaskEvent, TaskEventType, TaskStatus

from .trace import historical_trace_event


class AssistantRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    @property
    def session(self) -> Session:
        """Expose the unit-of-work only to this module's service layer."""
        return self._session

    def create_conversation(self, *, stock_symbol: str | None, stock_market: str | None, initial_context: str | None) -> ChatConversation:
        conversation = ChatConversation(
            stock_symbol=stock_symbol,
            stock_market=stock_market,
            initial_context=initial_context,
        )
        self._session.add(conversation)
        self._session.commit()
        self._session.refresh(conversation)
        return conversation

    def list_conversations(self, limit: int = 30) -> list[ChatConversation]:
        return (
            self._session.query(ChatConversation)
            .order_by(ChatConversation.updated_at.desc())
            .limit(limit)
            .all()
        )

    def get_conversation(self, conversation_id: int) -> ChatConversation | None:
        return self._session.query(ChatConversation).filter(ChatConversation.id == conversation_id).first()

    def list_messages(self, conversation_id: int) -> list[ChatMessage]:
        return (
            self._session.query(ChatMessage)
            .filter(ChatMessage.conversation_id == conversation_id)
            .order_by(ChatMessage.created_at.asc())
            .all()
        )

    def get_message(self, message_id: int) -> ChatMessage | None:
        return self._session.query(ChatMessage).filter(ChatMessage.id == message_id).first()

    def latest_task_snapshot(self, conversation_id: int) -> dict | None:
        task = (
            self._session.query(AssistantTaskRun)
            .filter(AssistantTaskRun.conversation_id == conversation_id)
            .order_by(AssistantTaskRun.id.desc())
            .first()
        )
        return self.get_task_snapshot(task.id) if task else None

    def message_results(self, conversation_id: int) -> dict[int, dict]:
        """Return structured results keyed by their final assistant message."""
        rows = (
            self._session.query(AssistantTaskRun)
            .filter(
                AssistantTaskRun.conversation_id == conversation_id,
                AssistantTaskRun.final_message_id.isnot(None),
                AssistantTaskRun.result_data.isnot(None),
            )
            .all()
        )
        return {
            int(row.final_message_id): row.result_data
            for row in rows
            if row.final_message_id is not None and isinstance(row.result_data, dict)
        }

    def message_task_runs(self, conversation_id: int) -> dict[int, AssistantTaskRun]:
        """Return completed task runs keyed by their final assistant message."""
        rows = (
            self._session.query(AssistantTaskRun)
            .filter(
                AssistantTaskRun.conversation_id == conversation_id,
                AssistantTaskRun.final_message_id.isnot(None),
            )
            .order_by(AssistantTaskRun.created_at.asc())
            .all()
        )
        return {
            int(row.final_message_id): row
            for row in rows
            if row.final_message_id is not None
        }

    def tool_invocations_for_tasks(
        self, task_run_ids: list[int]
    ) -> dict[int, list[AssistantToolInvocation]]:
        if not task_run_ids:
            return {}
        rows = (
            self._session.query(AssistantToolInvocation)
            .filter(AssistantToolInvocation.task_run_id.in_(task_run_ids))
            .order_by(
                AssistantToolInvocation.task_run_id.asc(),
                AssistantToolInvocation.created_at.asc(),
            )
            .all()
        )
        grouped: dict[int, list[AssistantToolInvocation]] = {}
        for row in rows:
            grouped.setdefault(int(row.task_run_id), []).append(row)
        return grouped

    def message_traces(self, conversation_id: int) -> dict[int, list[dict]]:
        """Restore the user-visible execution trace for persisted answers."""
        tasks = self.message_task_runs(conversation_id)
        if not tasks:
            return {}
        message_by_task = {
            int(task.id): message_id for message_id, task in tasks.items()
        }
        rows = (
            self._session.query(AssistantTaskEvent)
            .filter(AssistantTaskEvent.task_run_id.in_(list(message_by_task)))
            .order_by(
                AssistantTaskEvent.task_run_id.asc(),
                AssistantTaskEvent.sequence.asc(),
            )
            .all()
        )
        traces: dict[int, list[dict]] = {}
        for row in rows:
            item = historical_trace_event(row.event_type, row.data, row.sequence)
            if item is not None:
                traces.setdefault(message_by_task[int(row.task_run_id)], []).append(item)
        return traces

    def get_latest_context_snapshot(
        self, conversation_id: int
    ) -> AssistantContextSnapshot | None:
        return (
            self._session.query(AssistantContextSnapshot)
            .filter(AssistantContextSnapshot.conversation_id == conversation_id)
            .order_by(AssistantContextSnapshot.version.desc())
            .first()
        )

    def list_context_snapshots(
        self, conversation_id: int, limit: int = 20
    ) -> list[AssistantContextSnapshot]:
        return (
            self._session.query(AssistantContextSnapshot)
            .filter(AssistantContextSnapshot.conversation_id == conversation_id)
            .order_by(AssistantContextSnapshot.version.desc())
            .limit(limit)
            .all()
        )

    def save_context_snapshot(
        self,
        conversation_id: int,
        *,
        mode: ContextCompressionMode,
        summary: ContextSummary,
        covered_until_message_id: int | None,
        source_message_count: int,
        usage_before: ContextUsage,
        usage_after: ContextUsage,
    ) -> AssistantContextSnapshot:
        latest = self.get_latest_context_snapshot(conversation_id)
        snapshot = AssistantContextSnapshot(
            conversation_id=conversation_id,
            version=(latest.version + 1) if latest else 1,
            mode=mode.value,
            summary=summary.model_dump(mode="json"),
            covered_until_message_id=covered_until_message_id,
            source_message_count=source_message_count,
            usage_before=usage_before.model_dump(mode="json"),
            usage_after=usage_after.model_dump(mode="json"),
        )
        self._session.add(snapshot)
        self._session.commit()
        self._session.refresh(snapshot)
        return snapshot

    def add_message(self, conversation: ChatConversation, *, role: str, content: str) -> ChatMessage:
        message = ChatMessage(conversation_id=conversation.id, role=role, content=content)
        self._session.add(message)
        if role == "user" and not conversation.title:
            conversation.title = content[:20]
        conversation.updated_at = datetime.now(timezone.utc)
        self._session.commit()
        self._session.refresh(message)
        return message

    def rename_conversation(self, conversation: ChatConversation, title: str) -> ChatConversation:
        conversation.title = title
        conversation.title_source = 'manual'
        self._session.commit()
        self._session.refresh(conversation)
        return conversation

    def set_automatic_title(self, conversation_id: int, title: str, expected_title: str) -> bool:
        # A late model response must never overwrite a manual rename or a
        # title already generated by another worker.
        updated = self._session.query(ChatConversation).filter(
            ChatConversation.id == conversation_id,
            ChatConversation.title_source.in_(['provisional', 'legacy']),
            ChatConversation.title == expected_title,
        ).update({'title': title, 'title_source': 'automatic'}, synchronize_session=False)
        self._session.commit()
        return bool(updated)

    def delete_conversation(self, conversation: ChatConversation) -> None:
        from src.modules.notifications.service import NotificationService
        from src.platform.persistence.models import AssistantContextExport
        NotificationService(self._session).purge_assistant_conversation(conversation.id)
        self._session.query(AssistantContextExport).filter_by(conversation_id=conversation.id).delete(synchronize_session=False)
        task_ids = self._session.query(AssistantTaskRun.id).filter(AssistantTaskRun.conversation_id == conversation.id)
        self._session.query(AssistantTaskNotification).filter(AssistantTaskNotification.task_run_id.in_(task_ids)).delete(synchronize_session=False)
        self._session.query(ChatMessage).filter(ChatMessage.conversation_id == conversation.id).delete()
        self._session.query(AssistantContextSnapshot).filter(
            AssistantContextSnapshot.conversation_id == conversation.id
        ).delete()
        self._session.delete(conversation)
        self._session.commit()

    def create_task(
        self,
        *,
        conversation_id: int,
        user_message_id: int | None,
        context: dict,
    ) -> AssistantTaskRun:
        task = AssistantTaskRun(
            conversation_id=conversation_id,
            user_message_id=user_message_id,
            status=TaskStatus.QUEUED.value,
            context=context,
        )
        self._session.add(task)
        self._session.flush()
        self.append_task_event(
            task.id,
            TaskEventType.TASK_CREATED,
            data={"task_id": task.id, "conversation_id": conversation_id},
            commit=False,
        )
        self.append_task_event(
            task.id,
            TaskEventType.TASK_QUEUED,
            status=TaskStatus.QUEUED,
            data={"task_id": task.id},
            commit=False,
        )
        self._session.commit()
        self._session.refresh(task)
        return task

    def mark_task_running(self, task_run_id: int) -> AssistantTaskRun:
        task = self._require_task(task_run_id)
        if task.status == TaskStatus.RUNNING.value:
            return task
        if TaskStatus(task.status).is_terminal:
            return task
        self.claim_task(task_run_id)
        return self._require_task(task_run_id)

    def claim_task(self, task_run_id: int) -> bool:
        """Atomically claim queued or approval-resume work for one worker."""
        now = datetime.now(timezone.utc)
        claimed = (
            self._session.query(AssistantTaskRun)
            .filter(
                AssistantTaskRun.id == task_run_id,
                AssistantTaskRun.status.in_(
                    [TaskStatus.QUEUED.value, TaskStatus.WAITING_APPROVAL.value]
                ),
            )
            .update(
                {
                    "status": TaskStatus.RUNNING.value,
                    "started_at": now,
                    "finished_at": None,
                },
                synchronize_session=False,
            )
        )
        if claimed != 1:
            return False
        self._session.expire_all()
        self.append_task_event(
            task_run_id,
            TaskEventType.TASK_STARTED,
            status=TaskStatus.RUNNING,
            data={"task_id": task_run_id},
            commit=False,
        )
        self._session.commit()
        return True

    def append_task_event(
        self,
        task_run_id: int,
        event_type: TaskEventType,
        *,
        status: TaskStatus | None = None,
        step_index: int | None = None,
        data: dict | None = None,
        commit: bool = True,
    ) -> AssistantTaskEvent:
        """Append one replayable fact and advance the task snapshot cursor."""
        task = self._require_task(task_run_id)
        event_type = TaskEventType(event_type)
        # Lock the task row before reading MAX(sequence).  This serializes
        # event allocation for one task on both PostgreSQL and SQLite, while
        # keeping the per-task cursor stable for Last-Event-ID replay.
        self._session.query(AssistantTaskRun).filter(
            AssistantTaskRun.id == task_run_id
        ).update(
            {AssistantTaskRun.state_version: AssistantTaskRun.state_version},
            synchronize_session=False,
        )
        self._session.expire(task)
        task = self._require_task(task_run_id)
        next_sequence = (
            self._session.query(func.max(AssistantTaskEvent.sequence))
            .filter(AssistantTaskEvent.task_run_id == task_run_id)
            .scalar()
            or 0
        ) + 1
        protocol_event = TaskEvent(
            task_id=str(task_run_id),
            run_id=str(task_run_id),
            event_type=event_type,
            status=status,
            step_index=step_index,
            data=data or {},
        )
        row = AssistantTaskEvent(
            task_run_id=task_run_id,
            sequence=next_sequence,
            event_id=protocol_event.event_id,
            run_id=protocol_event.run_id,
            event_type=protocol_event.event_type.value,
            status=protocol_event.status.value if protocol_event.status else None,
            step_index=protocol_event.step_index,
            data=protocol_event.data,
            occurred_at=protocol_event.occurred_at,
        )
        self._session.add(row)
        task.last_event_id = str(next_sequence)
        task.state_version = int(task.state_version or 0) + 1
        if step_index is not None:
            task.current_step = max(int(task.current_step or 0), step_index)
        notification_kind = {
            TaskEventType.TASK_COMPLETED: "completed",
            TaskEventType.TASK_FAILED: "failed",
            TaskEventType.TASK_PAUSED: "awaiting_approval",
        }.get(event_type)
        from src.modules.notifications.service import NotificationService
        from src.modules.notifications.sources import assistant_event
        notifications = NotificationService(self._session)
        if event_type == TaskEventType.TASK_PAUSED and status != TaskStatus.WAITING_APPROVAL:
            notification_kind = None
        if event_type in {
            TaskEventType.TASK_STARTED, TaskEventType.TASK_RETRY_SCHEDULED,
            TaskEventType.TASK_COMPLETED, TaskEventType.TASK_FAILED,
            TaskEventType.TASK_CANCELLED, TaskEventType.TASK_PAUSED,
        }:
            notifications.resolve(subject_kind="assistant_task", subject_id=str(task_run_id),
                                  approvals_only=event_type != TaskEventType.TASK_RETRY_SCHEDULED)
        if notification_kind:
            assistant_event(self._session, task, next_sequence, notification_kind, occurred_at=row.occurred_at)
        if commit:
            self._session.commit()
            self._session.refresh(row)
        return row

    def list_task_events(
        self, task_run_id: int, *, after_sequence: int = 0, limit: int = 200
    ) -> list[AssistantTaskEvent]:
        self._require_task(task_run_id)
        return (
            self._session.query(AssistantTaskEvent)
            .filter(
                AssistantTaskEvent.task_run_id == task_run_id,
                AssistantTaskEvent.sequence > max(0, int(after_sequence)),
            )
            .order_by(AssistantTaskEvent.sequence.asc())
            .limit(max(1, min(int(limit), 1_000)))
            .all()
        )

    def get_active_tasks(self) -> list[dict]:
        """Task progress is independent from inbox/read state."""
        active = (
            self._session.query(AssistantTaskRun, ChatConversation)
            .join(ChatConversation, ChatConversation.id == AssistantTaskRun.conversation_id)
            .filter(AssistantTaskRun.status.in_([status.value for status in TaskStatus if not status.is_terminal]))
            .order_by(AssistantTaskRun.id.desc())
            .all()
        )
        return [{
            "id": task.id, "conversation_id": task.conversation_id,
            "title": conversation.title or "", "status": task.status,
            "current_step": int(task.current_step or 0),
            "started_at": self._utc_timestamp(task.started_at), "created_at": self._utc_timestamp(task.created_at),
        } for task, conversation in active]

    def get_activity(self) -> dict:
        from src.modules.notifications.service import NotificationService
        notifications = NotificationService(self._session)
        inbox = notifications.query(source="assistant").filter(NotificationEvent.resolved_at.is_(None))
        rows = inbox.order_by(NotificationReceipt.read_at.isnot(None), NotificationEvent.id.desc()).limit(30).all()
        available_ids = notifications.available_ids(rows)
        return {
            "active_tasks": self.get_active_tasks(),
            "notifications": [{
                "id": event.id, "task_id": int(event.subject_id),
                "conversation_id": event.actions[0]["conversation_id"], "title": event.display_snapshot.get("title", ""),
                "kind": event.event_type.removeprefix("assistant_"),
                "created_at": self._utc_timestamp(event.occurred_at), "read_at": self._utc_timestamp(receipt.read_at),
            } for event, receipt in rows if event.id in available_ids],
            "unread_count": inbox.filter(NotificationReceipt.read_at.is_(None)).count(),
            "notification_cursor": inbox.with_entities(func.max(NotificationEvent.id)).scalar() or 0,
        }

    def restore_waiting_notifications(self) -> None:
        """Surface approvals that were already waiting before inbox deployment."""
        waiting = self._session.query(AssistantTaskRun).filter(AssistantTaskRun.status == TaskStatus.WAITING_APPROVAL.value).all()
        for task in waiting:
            existing = self._session.query(NotificationEvent.id).filter(
                NotificationEvent.subject_kind == "assistant_task", NotificationEvent.subject_id == str(task.id),
                NotificationEvent.event_type == "assistant_awaiting_approval", NotificationEvent.resolved_at.is_(None),
            ).first()
            if existing is None:
                self.append_task_event(task.id, TaskEventType.TASK_PAUSED, status=TaskStatus.WAITING_APPROVAL, data={"reason": "approval_required"})

    def read_notifications(self, *, ids: list[int], through_id: int | None = None) -> int:
        from src.modules.notifications.schemas import NotificationSelection
        from src.modules.notifications.service import NotificationService
        if not ids and through_id is None:
            return 0
        return NotificationService(self._session).mark_read(NotificationSelection(ids=ids, through_id=through_id, source="assistant"))

    def is_task_cancelled(self, task_run_id: int) -> bool:
        task = self._require_task(task_run_id)
        return bool(task.cancel_requested or task.status == TaskStatus.CANCELLED.value)

    def cancel_task(self, task_run_id: int) -> AssistantTaskRun:
        task = self._require_task(task_run_id)
        if TaskStatus(task.status).is_terminal:
            return task
        task.cancel_requested = True
        task.status = TaskStatus.CANCELLED.value
        task.finished_at = datetime.now(timezone.utc)
        task.checkpoint = None
        task.checkpoint_id = ""
        now = datetime.now(timezone.utc)
        for approval in self.list_task_approvals(task_run_id):
            if approval.status == "pending":
                approval.status = "cancelled"
                approval.decided_at = now
                approval.decided_by = "system"
        self.append_task_event(
            task.id,
            TaskEventType.TASK_CANCELLED,
            status=TaskStatus.CANCELLED,
            commit=False,
            data={"reason": "user_requested", **self._task_metrics(task)},
        )
        self._session.commit()
        self._session.refresh(task)
        return task

    def retry_task(self, task_run_id: int) -> AssistantTaskRun:
        task = self._require_task(task_run_id)
        current = TaskStatus(task.status)
        if current not in {TaskStatus.FAILED, TaskStatus.CANCELLED}:
            return task
        # A failed task may already have completed a tool call. Replaying it
        # without a tool-level idempotency key could repeat a write side effect.
        if (
            self._session.query(AssistantToolInvocation.id)
            .filter(AssistantToolInvocation.task_run_id == task_run_id)
            .first()
            is not None
        ):
            return task
        task.status = TaskStatus.QUEUED.value
        task.cancel_requested = False
        task.retry_count = int(task.retry_count or 0) + 1
        task.error_code = None
        task.final_message_id = None
        task.started_at = None
        task.finished_at = None
        task.current_step = 0
        task.result_data = None
        self.append_task_event(
            task.id,
            TaskEventType.TASK_RETRY_SCHEDULED,
            status=TaskStatus.QUEUED,
            commit=False,
            data={"retry_count": task.retry_count},
        )
        self._session.commit()
        self._session.refresh(task)
        return task

    def record_tool_completed(
        self,
        task_run_id: int,
        *,
        call_id: str,
        tool_name: str,
        summary: str,
        ok: bool = True,
        duration_ms: int = 0,
        attempt_count: int = 1,
        error_code: str | None = None,
        result_data: dict | None = None,
        sources: list[dict] | None = None,
        observed_at: str | datetime | None = None,
    ) -> AssistantToolInvocation:
        invocation = (
            self._session.query(AssistantToolInvocation)
            .filter(
                AssistantToolInvocation.task_run_id == task_run_id,
                AssistantToolInvocation.call_id == call_id,
            )
            .first()
        )
        if invocation is None:
            invocation = AssistantToolInvocation(
                task_run_id=task_run_id,
                call_id=call_id,
                tool_name=tool_name,
            )
            self._session.add(invocation)
        invocation.tool_name = tool_name
        invocation.status = "completed" if ok else "failed"
        invocation.summary = summary
        invocation.duration_ms = max(0, int(duration_ms or 0))
        invocation.attempt_count = max(1, int(attempt_count or 1))
        invocation.error_code = error_code
        invocation.result_data = result_data or {}
        invocation.source_data = sources or []
        if isinstance(observed_at, str) and observed_at:
            try:
                invocation.observed_at = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
            except ValueError:
                invocation.observed_at = None
        elif isinstance(observed_at, datetime):
            invocation.observed_at = observed_at
        invocation.completed_at = datetime.now(timezone.utc)
        self.append_task_event(
            task_run_id,
            TaskEventType.TOOL_COMPLETED,
            status=TaskStatus.RUNNING,
            data={
                "call_id": call_id,
                "tool": tool_name,
                "name": tool_name,
                "ok": ok,
                "preview": summary,
                "summary": summary,
                "duration_ms": invocation.duration_ms,
                "attempt_count": invocation.attempt_count,
                "error_code": error_code,
                "sources": invocation.source_data,
                "observed_at": (
                    invocation.observed_at.isoformat() if invocation.observed_at else None
                ),
            },
            commit=False,
        )
        self._session.commit()
        self._session.refresh(invocation)
        return invocation

    def record_model_usage(self, task_run_id: int, data: dict) -> AssistantTaskRun:
        """Accumulate one model turn's provider or estimated usage."""
        task = self._require_task(task_run_id)
        input_tokens = max(0, int(data.get("input_tokens") or 0))
        output_tokens = max(0, int(data.get("output_tokens") or 0))
        total_tokens = max(
            0,
            int(data.get("total_tokens") or input_tokens + output_tokens),
        )
        task.input_tokens = int(task.input_tokens or 0) + input_tokens
        task.output_tokens = int(task.output_tokens or 0) + output_tokens
        task.total_tokens = int(task.total_tokens or 0) + total_tokens
        task.cached_input_tokens = int(task.cached_input_tokens or 0) + max(
            0, int(data.get("cached_input_tokens") or 0)
        )
        task.reasoning_output_tokens = int(task.reasoning_output_tokens or 0) + max(
            0, int(data.get("reasoning_output_tokens") or 0)
        )
        model = data.get("model")
        if model:
            task.model = str(model)
        source = str(data.get("source") or "unknown")
        current_source = task.usage_source or "unknown"
        task.usage_source = (
            source
            if current_source in {"", "unknown", source}
            else "mixed"
        )
        return task

    def record_tool_started(
        self,
        task_run_id: int,
        *,
        call_id: str,
        tool_name: str,
        arguments: dict | None = None,
    ) -> AssistantToolInvocation:
        """Persist the intent before a tool can produce an external side effect."""
        invocation = (
            self._session.query(AssistantToolInvocation)
            .filter(
                AssistantToolInvocation.task_run_id == task_run_id,
                AssistantToolInvocation.call_id == call_id,
            )
            .first()
        )
        if invocation is None:
            invocation = AssistantToolInvocation(
                task_run_id=task_run_id,
                call_id=call_id,
                tool_name=tool_name,
                status="started",
                arguments=arguments or {},
            )
            self._session.add(invocation)
        self.append_task_event(
            task_run_id,
            TaskEventType.TOOL_STARTED,
            status=TaskStatus.RUNNING,
            data={
                "call_id": call_id,
                "tool": tool_name,
                "name": tool_name,
                "arguments": arguments or {},
            },
            commit=False,
        )
        self._session.commit()
        self._session.refresh(invocation)
        return invocation

    def list_recent_tool_findings(
        self, conversation_id: int, limit: int = 12
    ) -> list[AssistantToolInvocation]:
        """Return completed tool facts for context reconstruction.

        Chat messages intentionally contain only the user-visible answer.  The
        durable invocation rows are the trusted source for deciding whether a
        previous tool action actually happened.
        """
        return (
            self._session.query(AssistantToolInvocation)
            .join(
                AssistantTaskRun,
                AssistantTaskRun.id == AssistantToolInvocation.task_run_id,
            )
            .filter(AssistantTaskRun.conversation_id == conversation_id)
            .filter(AssistantToolInvocation.status == "completed")
            .order_by(AssistantToolInvocation.created_at.desc())
            .limit(limit)
            .all()
        )

    def list_task_tool_invocations(self, task_run_id: int) -> list[AssistantToolInvocation]:
        return (
            self._session.query(AssistantToolInvocation)
            .filter(AssistantToolInvocation.task_run_id == task_run_id)
            .order_by(AssistantToolInvocation.created_at.asc())
            .all()
        )

    def save_checkpoint(
        self,
        task_run_id: int,
        checkpoint: AgentCheckpoint,
        *,
        reason: CheckpointReason = CheckpointReason.APPROVAL_REQUIRED,
        metadata: dict | None = None,
    ) -> CheckpointEnvelope:
        """Persist a versioned envelope around provider-neutral resume state."""
        task = self._require_task(task_run_id)
        envelope = CheckpointEnvelope.from_checkpoint(
            run_id=str(task_run_id),
            checkpoint=checkpoint,
            reason=reason,
            runtime_version=f"pan-agent-runtime@{PAN_AGENT_RUNTIME_VERSION}",
            metadata=metadata,
        )
        task.status = TaskStatus.WAITING_APPROVAL.value
        task.checkpoint = envelope.model_dump(mode="json")
        task.checkpoint_id = envelope.checkpoint_id
        task.current_step = checkpoint.step_index
        task.finished_at = None
        self.append_task_event(
            task.id,
            TaskEventType.CHECKPOINT_SAVED,
            status=TaskStatus.WAITING_APPROVAL,
            step_index=checkpoint.step_index,
            data={
                "checkpoint_id": envelope.checkpoint_id,
                "reason": envelope.reason.value,
            },
            commit=False,
        )
        self._session.commit()
        return envelope

    def get_task_checkpoint_envelope(self, task_run_id: int) -> CheckpointEnvelope | None:
        """Load a current or legacy checkpoint as the versioned envelope."""
        task = self._require_task(task_run_id)
        if task.checkpoint is None:
            return None
        raw = task.checkpoint
        if isinstance(raw, dict) and "state" in raw and "schema_version" in raw:
            return CheckpointEnvelope.model_validate(raw)
        # Migrate the in-memory representation of pre-P0 approval checkpoints.
        return CheckpointEnvelope.from_checkpoint(
            run_id=str(task_run_id),
            checkpoint=AgentCheckpoint.model_validate(raw),
            reason=CheckpointReason.APPROVAL_REQUIRED,
            runtime_version="legacy",
        )

    def get_task_checkpoint(self, task_run_id: int) -> AgentCheckpoint | None:
        envelope = self.get_task_checkpoint_envelope(task_run_id)
        if envelope is None:
            return None
        return envelope.to_checkpoint()

    def get_task_run(self, task_run_id: int) -> AssistantTaskRun:
        """Return the durable task metadata needed to rebuild a runtime request."""
        return self._require_task(task_run_id)

    def list_tasks_for_recovery(self) -> list[AssistantTaskRun]:
        """Return tasks whose previous process may have stopped mid-run."""
        return (
            self._session.query(AssistantTaskRun)
            .filter(
                AssistantTaskRun.status.in_(
                    [TaskStatus.QUEUED.value, TaskStatus.RUNNING.value]
                )
            )
            .order_by(AssistantTaskRun.created_at.asc())
            .all()
        )

    def create_approvals(
        self,
        task_run_id: int,
        pending_approvals: list[PendingApproval],
        *,
        expires_at: datetime,
        presentations: dict[str, dict] | None = None,
    ) -> list[AssistantToolApproval]:
        """Create or reuse the opaque records that own checkpoint calls.

        A partial approval resume writes a smaller checkpoint containing the
        still-pending calls. Reusing those rows keeps the browser's approval ID
        stable and makes checkpoint persistence idempotent across reconnects.
        """
        pending = list(pending_approvals)
        call_ids = [item.call_id for item in pending]
        existing = (
            self._session.query(AssistantToolApproval)
            .filter(
                AssistantToolApproval.task_run_id == task_run_id,
                AssistantToolApproval.call_id.in_(call_ids),
            )
            .all()
            if call_ids
            else []
        )
        existing_by_call = {row.call_id: row for row in existing}
        rows: list[AssistantToolApproval] = []
        for item in pending:
            row = existing_by_call.get(item.call_id)
            if row is None:
                row = AssistantToolApproval(
                    id=str(uuid4()),
                    task_run_id=task_run_id,
                    call_id=item.call_id,
                    tool_name=item.tool_name,
                    risk=item.risk.value,
                    arguments=item.arguments,
                    presentation=(presentations or {}).get(item.call_id, {}),
                    status="pending",
                    expires_at=expires_at,
                )
                self._session.add(row)
            rows.append(row)
        self._session.commit()
        for row in rows:
            self._session.refresh(row)
        return rows

    def get_pending_approval(self, approval_id: str) -> AssistantToolApproval | None:
        return (
            self._session.query(AssistantToolApproval)
            .filter(
                AssistantToolApproval.id == approval_id,
                AssistantToolApproval.status == "pending",
            )
            .first()
        )

    def list_task_approvals(self, task_run_id: int) -> list[AssistantToolApproval]:
        return (
            self._session.query(AssistantToolApproval)
            .filter(AssistantToolApproval.task_run_id == task_run_id)
            .order_by(AssistantToolApproval.created_at.asc())
            .all()
        )

    def decide_approval(
        self,
        approval_id: str,
        decision: ApprovalDecision,
        *,
        decided_by: str,
    ) -> tuple[AssistantToolApproval | None, bool]:
        """Consume a pending approval once; repeats return the durable record."""
        now = datetime.now(timezone.utc)
        accepted = (
            self._session.query(AssistantToolApproval)
            .filter(
                AssistantToolApproval.id == approval_id,
                AssistantToolApproval.status == "pending",
                AssistantToolApproval.expires_at > now,
            )
            .update(
                {
                    "status": decision.value,
                    "decided_at": now,
                    "decided_by": decided_by,
                },
                synchronize_session=False,
            )
            == 1
        )
        self._session.commit()
        approval = (
            self._session.query(AssistantToolApproval)
            .filter(AssistantToolApproval.id == approval_id)
            .first()
        )
        return approval, accepted

    def upsert_tool_permission(
        self,
        principal_scope: str,
        selector_kind: str,
        selector_value: str,
        mode: PermissionMode,
    ) -> AssistantToolPermission:
        """Store one exact tool or risk preference for a local principal."""
        if selector_kind not in {"tool", "risk"}:
            raise ValueError("selector_kind must be 'tool' or 'risk'")
        row = (
            self._session.query(AssistantToolPermission)
            .filter(
                AssistantToolPermission.principal_scope == principal_scope,
                AssistantToolPermission.selector_kind == selector_kind,
                AssistantToolPermission.selector_value == selector_value,
            )
            .first()
        )
        if row is None:
            row = AssistantToolPermission(
                principal_scope=principal_scope,
                selector_kind=selector_kind,
                selector_value=selector_value,
                mode=mode.value,
            )
            self._session.add(row)
        else:
            row.mode = mode.value
            row.updated_at = datetime.now(timezone.utc)
        self._session.commit()
        self._session.refresh(row)
        return row

    def permission_snapshot(self, principal_scope: str = "local") -> dict[tuple[str, str], PermissionMode]:
        """Read preferences once so an in-flight runtime sees a stable policy."""
        rows = (
            self._session.query(AssistantToolPermission)
            .filter(AssistantToolPermission.principal_scope == principal_scope)
            .all()
        )
        return {
            (row.selector_kind, row.selector_value): PermissionMode(row.mode)
            for row in rows
        }

    def list_tool_permissions(self, principal_scope: str = "local") -> list[AssistantToolPermission]:
        return (
            self._session.query(AssistantToolPermission)
            .filter(AssistantToolPermission.principal_scope == principal_scope)
            .order_by(AssistantToolPermission.selector_kind, AssistantToolPermission.selector_value)
            .all()
        )

    def resolve_permission(
        self,
        tool: ToolSpec,
        *,
        principal_scope: str = "local",
        snapshot: dict[tuple[str, str], PermissionMode] | None = None,
    ) -> ToolPermissionDecision:
        """Apply exact-tool overrides, risk defaults and non-bypassable floors."""
        if tool.risk is ToolRisk.DESTRUCTIVE:
            return ToolPermissionDecision.deny("破坏性工具默认禁止")

        decisions = snapshot if snapshot is not None else self.permission_snapshot(principal_scope)
        mode = decisions.get(("tool", tool.name))
        if mode is None:
            mode = decisions.get(("risk", tool.risk.value), self._default_permission_mode(tool.risk))

        if tool.confirmation_required and mode is PermissionMode.ALLOW:
            return ToolPermissionDecision.ask("该工具需要逐次确认")
        return ToolPermissionDecision(mode=mode)

    def finish_task(
        self,
        task_run_id: int,
        *,
        status: str,
        final_message_id: int | None,
        error_code: str | None = None,
        event_data: dict | None = None,
    ) -> None:
        task = self._require_task(task_run_id)
        if TaskStatus(task.status).is_terminal:
            return
        if status != "completed":
            # A failed/cancelled task must not leave actionable approval cards
            # behind.  Otherwise a browser reconnect can offer a decision for
            # a checkpoint that has already been discarded.
            now = datetime.now(timezone.utc)
            for approval in self.list_task_approvals(task_run_id):
                if approval.status == "pending":
                    approval.status = "cancelled"
                    approval.decided_at = now
                    approval.decided_by = "system"
        task.status = status
        task.final_message_id = final_message_id
        task.error_code = error_code
        task.checkpoint = None
        task.checkpoint_id = ""
        task.finished_at = datetime.now(timezone.utc)
        metrics = self._task_metrics(task)
        final_status = TaskStatus(status)
        event_type = (
            TaskEventType.TASK_COMPLETED
            if final_status is TaskStatus.COMPLETED
            else TaskEventType.TASK_CANCELLED
            if final_status is TaskStatus.CANCELLED
            else TaskEventType.TASK_FAILED
        )
        self.append_task_event(
            task.id,
            event_type,
            status=final_status,
            data={
                **(event_data or {}),
                **metrics,
                **({"error_code": error_code} if error_code else {}),
            },
            commit=False,
        )
        self._session.commit()

    def complete_task_with_message(
        self,
        task_run_id: int,
        conversation_id: int,
        content: str,
        *,
        result_data: dict | None = None,
    ) -> ChatMessage | None:
        """Commit the final message and task transition as one cancellation-safe unit."""
        task = self._require_task(task_run_id)
        if task.status != TaskStatus.RUNNING.value or task.cancel_requested:
            return None
        conversation = self.get_conversation(conversation_id)
        if conversation is None:
            raise LookupError(f"conversation {conversation_id} not found")
        completed_at = datetime.now(timezone.utc)
        conversation.updated_at = completed_at
        message = ChatMessage(
            conversation_id=conversation.id,
            role="assistant",
            content=content,
            created_at=completed_at,
        )
        self._session.add(message)
        self._session.flush()
        updated = (
            self._session.query(AssistantTaskRun)
            .filter(
                AssistantTaskRun.id == task_run_id,
                AssistantTaskRun.status == TaskStatus.RUNNING.value,
                AssistantTaskRun.cancel_requested.is_(False),
            )
            .update(
                {
                    "status": TaskStatus.COMPLETED.value,
                    "final_message_id": message.id,
                    "error_code": None,
                    "checkpoint": None,
                    "checkpoint_id": "",
                    "finished_at": completed_at,
                    "result_schema_version": int((result_data or {}).get("schema_version") or 1),
                    "result_data": result_data,
                },
                synchronize_session=False,
            )
        )
        if updated != 1:
            self._session.rollback()
            return None
        self._session.expire_all()
        completed_task = self._require_task(task_run_id)
        self.append_task_event(
            task_run_id,
            TaskEventType.TASK_COMPLETED,
            status=TaskStatus.COMPLETED,
            commit=False,
            data={
                "message_id": message.id,
                "content": content,
                "created_at": completed_at.isoformat(),
                "result": result_data,
                **self._task_metrics(completed_task),
            },
        )
        self._session.commit()
        self._session.refresh(message)
        return message

    def get_task_snapshot(self, task_run_id: int) -> dict:
        task = self._require_task(task_run_id)
        attempt_event_id = (
            self._session.query(func.max(AssistantTaskEvent.sequence))
            .filter(
                AssistantTaskEvent.task_run_id == task_run_id,
                AssistantTaskEvent.event_type == TaskEventType.TASK_RETRY_SCHEDULED.value,
            )
            .scalar()
            or 0
        )
        trace_rows = (
            self._session.query(AssistantTaskEvent)
            .filter(
                AssistantTaskEvent.task_run_id == task_run_id,
                AssistantTaskEvent.sequence > attempt_event_id,
                AssistantTaskEvent.event_type != TaskEventType.ANSWER_TOKEN.value,
            )
            .order_by(AssistantTaskEvent.sequence.desc())
            .limit(80)
            .all()
        )
        trace = [
            item
            for row in reversed(trace_rows)
            if (item := historical_trace_event(row.event_type, row.data, row.sequence)) is not None
        ][-40:]
        tools = (
            self._session.query(AssistantToolInvocation)
            .filter(AssistantToolInvocation.task_run_id == task_run_id)
            .order_by(AssistantToolInvocation.created_at.asc())
            .all()
        )
        return {
            "id": task.id,
            "conversation_id": task.conversation_id,
            "status": task.status,
            "state_version": task.state_version,
            "current_step": task.current_step,
            "last_event_id": task.last_event_id or "",
            "attempt_event_id": str(attempt_event_id),
            "trace": trace,
            "checkpoint_id": task.checkpoint_id or "",
            "cancel_requested": bool(task.cancel_requested),
            "retry_count": int(task.retry_count or 0),
            "context": task.context or {},
            "error_code": task.error_code,
            "can_retry": TaskStatus(task.status) in {TaskStatus.FAILED, TaskStatus.CANCELLED} and not tools,
            "retry_blocked_reason": (
                "tools_already_started" if tools
                else "not_terminal" if TaskStatus(task.status) not in {TaskStatus.FAILED, TaskStatus.CANCELLED}
                else None
            ),
            "created_at": self._utc_timestamp(task.created_at),
            "started_at": self._utc_timestamp(task.started_at),
            "finished_at": self._utc_timestamp(task.finished_at),
            "user_message_id": task.user_message_id,
            "result": task.result_data,
            "model": task.model,
            "duration_ms": self._task_duration_ms(task),
            "usage": {
                "input_tokens": int(task.input_tokens or 0),
                "output_tokens": int(task.output_tokens or 0),
                "total_tokens": int(task.total_tokens or 0),
                "cached_input_tokens": int(task.cached_input_tokens or 0),
                "reasoning_output_tokens": int(task.reasoning_output_tokens or 0),
                "source": task.usage_source or "unknown",
                "model": task.model,
            },
            "pending_approvals": [
                {
                    "id": approval.id,
                    "call_id": approval.call_id,
                    "tool_name": approval.tool_name,
                    "risk": approval.risk,
                    "arguments": approval.arguments or {},
                    "presentation": approval.presentation or {},
                    "expires_at": approval.expires_at,
                }
                for approval in (
                    approval
                    for approval in self.list_task_approvals(task_run_id)
                    if approval.status == "pending"
                )
            ],
            "tools": [
                {
                    "call_id": tool.call_id,
                    "tool": tool.tool_name,
                    "status": tool.status,
                    "summary": tool.summary,
                    "duration_ms": int(tool.duration_ms or 0),
                    "attempt_count": int(tool.attempt_count or 1),
                    "error_code": tool.error_code,
                    "sources": tool.source_data or [],
                    "observed_at": tool.observed_at,
                }
                for tool in tools
            ],
        }

    def _require_task(self, task_run_id: int) -> AssistantTaskRun:
        task = self._session.query(AssistantTaskRun).filter(AssistantTaskRun.id == task_run_id).first()
        if task is None:
            raise LookupError("助手任务不存在")
        return task

    @staticmethod
    def _utc_timestamp(value: datetime | None) -> datetime | None:
        return value.replace(tzinfo=timezone.utc) if value and value.tzinfo is None else value

    @staticmethod
    def _task_duration_ms(task: AssistantTaskRun) -> int:
        if task.started_at is None:
            return 0
        finished_at = task.finished_at or datetime.now(timezone.utc)
        started_at = task.started_at
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=timezone.utc)
        if finished_at.tzinfo is None:
            finished_at = finished_at.replace(tzinfo=timezone.utc)
        return max(0, round((finished_at - started_at).total_seconds() * 1000))

    def _task_metrics(self, task: AssistantTaskRun) -> dict:
        return {
            "duration_ms": self._task_duration_ms(task),
            "input_tokens": int(task.input_tokens or 0),
            "output_tokens": int(task.output_tokens or 0),
            "total_tokens": int(task.total_tokens or 0),
            "cached_input_tokens": int(task.cached_input_tokens or 0),
            "reasoning_output_tokens": int(task.reasoning_output_tokens or 0),
            "usage_source": task.usage_source or "unknown",
            "model": task.model,
        }

    @staticmethod
    def _default_permission_mode(risk: ToolRisk) -> PermissionMode:
        if risk is ToolRisk.READ:
            return PermissionMode.ALLOW
        if risk in {ToolRisk.WRITE, ToolRisk.EXTERNAL}:
            return PermissionMode.ASK
        return PermissionMode.DENY
