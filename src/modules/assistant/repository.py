"""Persistence operations for assistant conversations and messages."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from pan_agent import (
    AgentCheckpoint,
    ApprovalDecision,
    PendingApproval,
    PermissionMode,
    ToolPermissionDecision,
    ToolRisk,
    ToolSpec,
)
from sqlalchemy.orm import Session

# 助手表由共享持久化平台注册；repository 是其唯一的模块内访问边界，
# 不需要再经由一个只做 re-export 的 ``assistant.models`` 转发层。
from src.platform.persistence.models import (
    AssistantTaskRun,
    AssistantToolApproval,
    AssistantToolInvocation,
    AssistantToolPermission,
    ChatConversation,
    ChatMessage,
)


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
            status="running",
            context=context,
            started_at=datetime.now(timezone.utc),
        )
        self._session.add(task)
        self._session.commit()
        self._session.refresh(task)
        return task

    def record_tool_completed(self, task_run_id: int, *, call_id: str, tool_name: str, summary: str) -> AssistantToolInvocation:
        invocation = AssistantToolInvocation(
            task_run_id=task_run_id,
            call_id=call_id,
            tool_name=tool_name,
            status="completed",
            summary=summary,
            completed_at=datetime.now(timezone.utc),
        )
        self._session.add(invocation)
        self._session.commit()
        self._session.refresh(invocation)
        return invocation

    def save_checkpoint(self, task_run_id: int, checkpoint: AgentCheckpoint) -> None:
        """Persist enough provider-neutral state to resume after human input."""
        task = self._require_task(task_run_id)
        task.status = "awaiting_approval"
        task.checkpoint = checkpoint.model_dump(mode="json")
        task.finished_at = None
        self._session.commit()

    def get_task_checkpoint(self, task_run_id: int) -> AgentCheckpoint | None:
        task = self._require_task(task_run_id)
        if task.checkpoint is None:
            return None
        return AgentCheckpoint.model_validate(task.checkpoint)

    def get_task_run(self, task_run_id: int) -> AssistantTaskRun:
        """Return the durable task metadata needed to rebuild a runtime request."""
        return self._require_task(task_run_id)

    def create_approvals(
        self,
        task_run_id: int,
        pending_approvals: list[PendingApproval],
        *,
        expires_at: datetime,
        presentations: dict[str, dict] | None = None,
    ) -> list[AssistantToolApproval]:
        """Create the opaque approval records that own a checkpoint's calls."""
        rows = [
            AssistantToolApproval(
                id=str(uuid4()),
                task_run_id=task_run_id,
                call_id=pending.call_id,
                tool_name=pending.tool_name,
                risk=pending.risk.value,
                arguments=pending.arguments,
                presentation=(presentations or {}).get(pending.call_id, {}),
                status="pending",
                expires_at=expires_at,
            )
            for pending in pending_approvals
        ]
        self._session.add_all(rows)
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
    ) -> None:
        task = self._require_task(task_run_id)
        task.status = status
        task.final_message_id = final_message_id
        task.error_code = error_code
        task.checkpoint = None
        task.finished_at = datetime.now(timezone.utc)
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
