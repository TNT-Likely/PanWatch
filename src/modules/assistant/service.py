"""Assistant use cases independent from FastAPI request handling."""

from __future__ import annotations

from .repository import AssistantRepository
from .schemas import (
    ConversationDetailDTO,
    ConversationDTO,
    CreateConversationCommand,
    MessageDTO,
)


class AssistantNotFoundError(LookupError):
    pass


class AssistantService:
    def __init__(self, repository: AssistantRepository) -> None:
        self._repository = repository

    def create_conversation(self, command: CreateConversationCommand) -> ConversationDTO:
        conversation = self._repository.create_conversation(**command.model_dump())
        return self._conversation_dto(conversation)

    def list_conversations(self, limit: int = 30) -> list[ConversationDTO]:
        return [self._conversation_dto(row) for row in self._repository.list_conversations(limit)]

    def get_conversation(self, conversation_id: int) -> ConversationDetailDTO:
        conversation = self._require_conversation(conversation_id)
        return ConversationDetailDTO(
            conversation=self._conversation_dto(conversation),
            messages=[self._message_dto(row) for row in self._repository.list_messages(conversation_id)],
        )

    def record_user_message(self, conversation_id: int, content: str) -> MessageDTO:
        conversation = self._require_conversation(conversation_id)
        return self._message_dto(self._repository.add_message(conversation, role="user", content=content))

    def delete_conversation(self, conversation_id: int) -> None:
        self._repository.delete_conversation(self._require_conversation(conversation_id))

    def get_task_snapshot(self, task_run_id: int) -> dict:
        try:
            return self._repository.get_task_snapshot(task_run_id)
        except LookupError as exc:
            raise AssistantNotFoundError(str(exc)) from exc

    def _require_conversation(self, conversation_id: int):
        conversation = self._repository.get_conversation(conversation_id)
        if not conversation:
            raise AssistantNotFoundError("对话不存在")
        return conversation

    @staticmethod
    def _conversation_dto(conversation) -> ConversationDTO:
        return ConversationDTO(
            id=conversation.id,
            title=conversation.title or "",
            stock_symbol=conversation.stock_symbol,
            stock_market=conversation.stock_market,
            created_at=conversation.created_at,
        )

    @staticmethod
    def _message_dto(message) -> MessageDTO:
        return MessageDTO(id=message.id, role=message.role, content=message.content, created_at=message.created_at)
