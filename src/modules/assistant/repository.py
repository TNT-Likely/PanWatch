"""Persistence operations for assistant conversations and messages."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from .models import AssistantTaskRun, AssistantToolInvocation, ChatConversation, ChatMessage


class AssistantRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

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

    def finish_task(
        self,
        task_run_id: int,
        *,
        status: str,
        final_message_id: int | None,
        error_code: str | None = None,
    ) -> None:
        task = self._session.query(AssistantTaskRun).filter(AssistantTaskRun.id == task_run_id).first()
        if task is None:
            raise LookupError("助手任务不存在")
        task.status = status
        task.final_message_id = final_message_id
        task.error_code = error_code
        task.finished_at = datetime.now(timezone.utc)
        self._session.commit()

    def get_task_snapshot(self, task_run_id: int) -> dict:
        task = self._session.query(AssistantTaskRun).filter(AssistantTaskRun.id == task_run_id).first()
        if task is None:
            raise LookupError("助手任务不存在")
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
