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
    AssistantTaskRun,
    AssistantToolApproval,
    AssistantToolInvocation,
    AssistantToolPermission,
    ChatConversation,
    ChatMessage,
)
from src.platform.tasking.contracts import TaskEvent, TaskEventType, TaskStatus


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

    def delete_conversation(self, conversation: ChatConversation) -> None:
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
        task.status = TaskStatus.RUNNING.value
        task.started_at = task.started_at or datetime.now(timezone.utc)
        task.finished_at = None
        self.append_task_event(
            task.id,
            TaskEventType.TASK_STARTED,
            status=TaskStatus.RUNNING,
            data={"task_id": task.id},
            commit=False,
        )
        self._session.commit()
        self._session.refresh(task)
        return task

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
            data={"reason": "user_requested"},
        )
        self._session.commit()
        self._session.refresh(task)
        return task

    def retry_task(self, task_run_id: int) -> AssistantTaskRun:
        task = self._require_task(task_run_id)
        current = TaskStatus(task.status)
        if not current.is_terminal:
            return task
        task.status = TaskStatus.QUEUED.value
        task.cancel_requested = False
        task.retry_count = int(task.retry_count or 0) + 1
        task.error_code = None
        task.final_message_id = None
        task.finished_at = None
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
    ) -> AssistantToolInvocation:
        invocation = AssistantToolInvocation(
            task_run_id=task_run_id,
            call_id=call_id,
            tool_name=tool_name,
            status="completed" if ok else "failed",
            summary=summary,
            completed_at=datetime.now(timezone.utc),
        )
        self._session.add(invocation)
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
        if task.status == TaskStatus.CANCELLED.value and status == TaskStatus.COMPLETED.value:
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
                **({"error_code": error_code} if error_code else {}),
            },
            commit=False,
        )
        self._session.commit()

    def get_task_snapshot(self, task_run_id: int) -> dict:
        task = self._require_task(task_run_id)
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
            "checkpoint_id": task.checkpoint_id or "",
            "cancel_requested": bool(task.cancel_requested),
            "retry_count": int(task.retry_count or 0),
            "context": task.context or {},
            "error_code": task.error_code,
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
    def _default_permission_mode(risk: ToolRisk) -> PermissionMode:
        if risk is ToolRisk.READ:
            return PermissionMode.ALLOW
        if risk in {ToolRisk.WRITE, ToolRisk.EXTERNAL}:
            return PermissionMode.ASK
        return PermissionMode.DENY
