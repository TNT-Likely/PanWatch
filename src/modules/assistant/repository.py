"""Persistence operations for assistant conversations and messages."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from .models import ChatConversation, ChatMessage


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

