"""Assistant use cases independent from FastAPI request handling."""

from __future__ import annotations

from pan_agent import (
    AgentRuntime,
    RunRequest,
    ToolCall,
    ToolPermissionDecision,
    ToolSpec,
)

from src.platform.ai.ai_failover import build_failover_client
from src.platform.persistence.models import AIModel, AIService

from .llm_adapter import FailoverModelAdapter
from .repository import AssistantRepository
from .schemas import (
    ConversationDetailDTO,
    ConversationDTO,
    CreateConversationCommand,
    MessageDTO,
)
from .tools import build_panwatch_tool_registry


class AssistantNotFoundError(LookupError):
    pass


class PanWatchToolPolicy:
    """A per-run, trusted snapshot of PanWatch's local tool preferences."""

    def __init__(self, repository: AssistantRepository, permissions: dict) -> None:
        self._repository = repository
        self._permissions = permissions

    def is_tool_visible(self, request: RunRequest, tool: ToolSpec) -> bool:
        return self._decision(tool).mode.value != "deny"

    async def decide(
        self,
        _request: RunRequest,
        tool: ToolSpec,
        _call: ToolCall,
    ) -> ToolPermissionDecision:
        return self._decision(tool)

    def _decision(self, tool: ToolSpec) -> ToolPermissionDecision:
        return self._repository.resolve_permission(tool, snapshot=self._permissions)


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

    def build_runtime(self, failover_client) -> AgentRuntime:
        """Compose host adapters into the business-agnostic PanAgent runtime."""
        return AgentRuntime(
            FailoverModelAdapter(failover_client),
            build_panwatch_tool_registry(self._repository.session),
            policy=self.build_tool_policy(),
        )

    def build_tool_policy(self) -> PanWatchToolPolicy:
        """Freeze a user's preferences for the lifetime of one runtime run."""
        return PanWatchToolPolicy(self._repository, self._repository.permission_snapshot())

    def build_failover_client(self):
        model = self._repository.session.query(AIModel).filter(AIModel.is_default == True).first()
        if not model:
            model = self._repository.session.query(AIModel).first()
        provider = self._repository.session.query(AIService).filter(AIService.id == model.service_id).first() if model else None
        return build_failover_client(model, provider, db=self._repository.session)

    def create_task(self, conversation_id: int, user_message_id: int):
        self._require_conversation(conversation_id)
        return self._repository.create_task(conversation_id=conversation_id, user_message_id=user_message_id, context={})

    def record_tool_completion(self, task_id: int, data: dict) -> None:
        self._repository.record_tool_completed(task_id, call_id=data.get("call_id", ""), tool_name=data.get("tool", ""), summary=data.get("summary", ""))

    def record_assistant_message(self, conversation_id: int, content: str) -> MessageDTO:
        return self._message_dto(self._repository.add_message(self._require_conversation(conversation_id), role="assistant", content=content))

    def finish_task(self, task_id: int, result, final_message_id: int) -> None:
        self._repository.finish_task(task_id, status=result.status.value, final_message_id=final_message_id, error_code=result.error_code)

    def fail_task(self, task_id: int, error_code: str) -> None:
        """Close a task that could not yield a usable assistant answer."""
        self._repository.finish_task(
            task_id,
            status="failed",
            final_message_id=None,
            error_code=error_code,
        )

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
