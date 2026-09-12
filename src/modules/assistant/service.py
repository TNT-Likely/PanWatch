"""Assistant use cases independent from FastAPI request handling."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from pan_agent import (
    AgentCheckpoint,
    AgentRuntime,
    ApprovalDecision,
    PermissionMode,
    RunRequest,
    ToolCall,
    ToolPermissionDecision,
    ToolRisk,
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


class AssistantApprovalConflictError(RuntimeError):
    """A decision was already consumed or no longer matches its checkpoint."""


class AssistantApprovalExpiredError(AssistantApprovalConflictError):
    """A pending approval reached its durable expiry time."""


@dataclass(frozen=True)
class AssistantApprovalResolution:
    """All data the HTTP boundary needs before it can safely call resume()."""

    task: object
    checkpoint: AgentCheckpoint | None
    decisions: dict[str, ApprovalDecision]


class PanWatchToolPolicy:
    """A per-run, trusted snapshot of PanWatch's local tool preferences."""

    def __init__(self, repository: AssistantRepository, permissions: dict) -> None:
        self._repository = repository
        self._permissions = permissions

    def is_tool_visible(self, request: RunRequest, tool: ToolSpec) -> bool:
        allowed_tool_names = request.context.get("allowed_tool_names")
        if allowed_tool_names is not None and tool.name not in set(allowed_tool_names):
            return False
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

    def create_conversation(
        self, command: CreateConversationCommand
    ) -> ConversationDTO:
        conversation = self._repository.create_conversation(**command.model_dump())
        return self._conversation_dto(conversation)

    def list_conversations(self, limit: int = 30) -> list[ConversationDTO]:
        return [
            self._conversation_dto(row)
            for row in self._repository.list_conversations(limit)
        ]

    def get_conversation(self, conversation_id: int) -> ConversationDetailDTO:
        conversation = self._require_conversation(conversation_id)
        return ConversationDetailDTO(
            conversation=self._conversation_dto(conversation),
            messages=[
                self._message_dto(row)
                for row in self._repository.list_messages(conversation_id)
            ],
        )

    def record_user_message(self, conversation_id: int, content: str) -> MessageDTO:
        conversation = self._require_conversation(conversation_id)
        return self._message_dto(
            self._repository.add_message(conversation, role="user", content=content)
        )

    def delete_conversation(self, conversation_id: int) -> None:
        self._repository.delete_conversation(
            self._require_conversation(conversation_id)
        )

    def get_task_snapshot(self, task_run_id: int) -> dict:
        try:
            return self._repository.get_task_snapshot(task_run_id)
        except LookupError as exc:
            raise AssistantNotFoundError(str(exc)) from exc

    def pause_task(self, task_id: int, result) -> list:
        """Persist a waiting runtime before exposing any approval to a browser."""
        if result.checkpoint is None or not result.pending_approvals:
            raise ValueError(
                "waiting runtime result must include checkpoint and pending approvals"
            )
        self._repository.save_checkpoint(task_id, result.checkpoint)
        return self._repository.create_approvals(
            task_id,
            result.pending_approvals,
            expires_at=datetime.now(timezone.utc) + timedelta(minutes=10),
            presentations={
                pending.call_id: self._approval_presentation(pending)
                for pending in result.pending_approvals
            },
        )

    def resolve_approval_decision(
        self,
        approval_id: str,
        decision: ApprovalDecision,
    ) -> AssistantApprovalResolution:
        """Consume one decision and return a resume plan for that card.

        The runtime deliberately resumes immediately with the decided subset;
        any other cards remain pending in the persisted checkpoint.
        """
        approval, accepted = self._repository.decide_approval(
            approval_id,
            decision,
            decided_by="local",
        )
        if approval is None:
            raise AssistantNotFoundError("审批不存在")
        if not accepted:
            if approval.status == "pending" and self._is_expired(approval.expires_at):
                raise AssistantApprovalExpiredError("审批已过期")
            raise AssistantApprovalConflictError("审批已处理")

        task = self._repository.get_task_run(approval.task_run_id)
        checkpoint = self._repository.get_task_checkpoint(task.id)
        if checkpoint is None:
            raise AssistantApprovalConflictError("审批任务没有可恢复检查点")
        approvals = self._repository.list_task_approvals(task.id)
        expected_ids = {pending.call_id for pending in checkpoint.pending_approvals}
        decisions = {
            row.call_id: ApprovalDecision(row.status)
            for row in approvals
            if row.call_id in expected_ids and row.status in {"approved", "rejected"}
        }
        if not decisions:
            raise AssistantApprovalConflictError("审批批次没有可执行的决定")
        return AssistantApprovalResolution(
            task=task, checkpoint=checkpoint, decisions=decisions
        )

    def build_runtime(self, failover_client) -> AgentRuntime:
        """Compose host adapters into the business-agnostic PanAgent runtime."""
        return AgentRuntime(
            FailoverModelAdapter(failover_client),
            build_panwatch_tool_registry(self._repository.session),
            policy=self.build_tool_policy(),
        )

    def build_tool_policy(self) -> PanWatchToolPolicy:
        """Freeze a user's preferences for the lifetime of one runtime run."""
        return PanWatchToolPolicy(
            self._repository, self._repository.permission_snapshot()
        )

    def get_tool_permissions(self) -> dict:
        """Return default risk policy plus registered-tool overrides for settings."""
        snapshot = self._repository.permission_snapshot()
        defaults = []
        for risk in ToolRisk:
            # Destructive operations have a non-overridable safety floor even
            # if an older database happens to contain an unsafe preference.
            mode = (
                self._default_mode_for_risk(risk)
                if risk is ToolRisk.DESTRUCTIVE
                else snapshot.get(
                    ("risk", risk.value),
                    self._default_mode_for_risk(risk),
                )
            )
            defaults.append({"risk": risk.value, "mode": mode.value})
        tools = [
            {
                "name": tool.name,
                "title": tool.title,
                "risk": tool.risk.value,
                "mode": self._repository.resolve_permission(
                    tool, snapshot=snapshot
                ).mode.value,
                "confirmation_required": tool.confirmation_required,
            }
            for tool in build_panwatch_tool_registry(
                self._repository.session
            ).registered_tools()
        ]
        return {
            "defaults": defaults,
            "tools": tools,
            "overrides": [
                {
                    "selector_kind": row.selector_kind,
                    "selector_value": row.selector_value,
                    "mode": row.mode,
                }
                for row in self._repository.list_tool_permissions()
            ],
        }

    def update_tool_permission(
        self,
        *,
        selector_kind: str,
        selector_value: str,
        mode: PermissionMode,
        risk: ToolRisk | None,
    ) -> dict:
        """Persist a UI preference while enforcing the same policy floors."""
        resolved_risk = risk
        if selector_kind == "risk":
            resolved_risk = ToolRisk(selector_value)
        elif selector_kind == "tool" and resolved_risk is None:
            registered = {
                tool.name: tool
                for tool in build_panwatch_tool_registry(
                    self._repository.session
                ).registered_tools()
            }
            if selector_value not in registered:
                raise ValueError("未知工具必须携带风险类别")
            resolved_risk = registered[selector_value].risk
        elif selector_kind != "tool":
            raise ValueError("不支持的权限选择器")

        if resolved_risk is ToolRisk.DESTRUCTIVE and mode is not PermissionMode.DENY:
            raise ValueError("破坏性工具只能设为禁止")
        self._repository.upsert_tool_permission(
            "local", selector_kind, selector_value, mode
        )
        return self.get_tool_permissions()

    def build_failover_client(self):
        model = (
            self._repository.session.query(AIModel)
            .filter(AIModel.is_default == True)
            .first()
        )
        if not model:
            model = self._repository.session.query(AIModel).first()
        provider = (
            self._repository.session.query(AIService)
            .filter(AIService.id == model.service_id)
            .first()
            if model
            else None
        )
        return build_failover_client(model, provider, db=self._repository.session)

    def create_task(self, conversation_id: int, user_message_id: int):
        self._require_conversation(conversation_id)
        return self._repository.create_task(
            conversation_id=conversation_id, user_message_id=user_message_id, context={}
        )

    def record_tool_completion(self, task_id: int, data: dict) -> None:
        self._repository.record_tool_completed(
            task_id,
            call_id=data.get("call_id", ""),
            tool_name=data.get("tool", ""),
            summary=data.get("summary", ""),
        )

    def record_assistant_message(
        self, conversation_id: int, content: str
    ) -> MessageDTO:
        return self._message_dto(
            self._repository.add_message(
                self._require_conversation(conversation_id),
                role="assistant",
                content=content,
            )
        )

    def finish_task(self, task_id: int, result, final_message_id: int) -> None:
        self._repository.finish_task(
            task_id,
            status=result.status.value,
            final_message_id=final_message_id,
            error_code=result.error_code,
        )

    def fail_task(self, task_id: int, error_code: str) -> None:
        """Close a task that could not yield a usable assistant answer."""
        self._repository.finish_task(
            task_id,
            status="failed",
            final_message_id=None,
            error_code=error_code,
        )

    @staticmethod
    def _approval_presentation(pending) -> dict[str, str]:
        """Translate host tool arguments into the text a human needs to approve."""
        arguments = pending.arguments

        if pending.tool_name == "update_price_alert":
            rule_id = arguments.get("rule_id", "?")
            changes: list[str] = []
            if "target_price" in arguments:
                try:
                    display_price = f"{float(arguments['target_price']):g}"
                except (TypeError, ValueError):
                    display_price = str(arguments["target_price"])
                if "direction" in arguments:
                    direction = "≥" if arguments.get("direction") == "above" else "≤"
                    changes.append(f"目标价 {direction} {display_price}")
                else:
                    changes.append(f"目标价改为 {display_price}（方向保持不变）")
            elif "direction" in arguments:
                direction = "≥" if arguments.get("direction") == "above" else "≤"
                changes.append(f"方向改为 {direction}")
            if "enabled" in arguments:
                changes.append("启用" if arguments["enabled"] else "停用")
            if "name" in arguments:
                changes.append(f"名称改为 {arguments['name']}")
            if "cooldown_minutes" in arguments:
                changes.append(f"冷却 {arguments['cooldown_minutes']} 分钟")
            if "max_triggers_per_day" in arguments:
                changes.append(f"每日最多触发 {arguments['max_triggers_per_day']} 次")
            if "repeat_mode" in arguments:
                changes.append(f"重复模式改为 {arguments['repeat_mode']}")
            summary = "；".join(changes) or "更新规则"
            return {
                "tool_title": "修改价格提醒",
                "summary": f"修改价格提醒 #{rule_id}：{summary}。",
            }

        if pending.tool_name == "delete_price_alert":
            rule_id = arguments.get("rule_id", "?")
            return {
                "tool_title": "删除价格提醒",
                "summary": f"删除价格提醒 #{rule_id} 及其历史命中记录。",
            }

        if pending.tool_name != "create_price_alert":
            return {
                "tool_title": "需要授权的操作",
                "summary": f"将调用 {pending.tool_name}。",
            }

        market = str(arguments.get("market") or "CN").upper()
        symbol = str(arguments.get("symbol") or "").upper()
        direction = "≥" if arguments.get("direction") == "above" else "≤"
        target_price = arguments.get("target_price")
        cooldown_minutes = arguments.get("cooldown_minutes", 30)
        try:
            display_price = f"{float(target_price):g}"
        except (TypeError, ValueError):
            display_price = str(target_price or "未知价格")
        try:
            display_cooldown = f"{int(cooldown_minutes)}"
        except (TypeError, ValueError):
            display_cooldown = "30"
        return {
            "tool_title": "创建价格提醒",
            "summary": (
                f"为 {market}:{symbol} 创建价格 {direction} {display_price} 的盘中提醒，"
                f"冷却 {display_cooldown} 分钟。"
            ),
        }

    @staticmethod
    def _is_expired(expires_at: datetime) -> bool:
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
        return expires_at <= datetime.now(timezone.utc)

    @staticmethod
    def _default_mode_for_risk(risk: ToolRisk) -> PermissionMode:
        if risk is ToolRisk.READ:
            return PermissionMode.ALLOW
        if risk in {ToolRisk.WRITE, ToolRisk.EXTERNAL}:
            return PermissionMode.ASK
        return PermissionMode.DENY

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
        return MessageDTO(
            id=message.id,
            role=message.role,
            content=message.content,
            created_at=message.created_at,
        )
