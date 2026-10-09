"""Assistant use cases independent from FastAPI request handling."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from pan_agent import (
    AgentCheckpoint,
    AgentRuntime,
    ApprovalDecision,
    ContextBudget,
    ContextCompressionMode,
    ContextEngine,
    ContextSummary,
    ContextUsage,
    ModelMessage,
    PermissionMode,
    RunRequest,
    ToolCall,
    ToolPermissionDecision,
    ToolRisk,
    ToolSpec,
)
from pan_agent_tool_research import ToolResearchPlugin, ToolResearchService, ToolSelectionPolicy
from pan_agent_token_meter import HeuristicTokenMeter

from src.platform.ai.ai_failover import (
    build_failover_client,
    get_configured_failover_client,
)
from src.platform.persistence.models import (
    AIModel,
    AIService,
    AppSettings,
    Position,
    Stock,
    StockSuggestion,
)
from src.platform.runtime.config import Settings
from src.platform.persistence.worker import run_db_operation

from .context_schemas import (
    AssistantConfigDTO,
    AssistantConfigUpdate,
    AssistantModelOption,
    ContextCompressionDTO,
    ContextDetailDTO,
    ContextSnapshotDTO,
)
from .context_summarizer import FailoverContextSummarizer
from .llm_adapter import FailoverModelAdapter
from .prompt import build_assistant_messages
from .watch_request import alert_capability_error, condition_summary, is_watch_retry, resolve_watch_request
from .portfolio_diagnosis import PortfolioDiagnosisExtension
from .repository import AssistantRepository
from .result_builder import build_deterministic_assistant_result
from .schemas import (
    ConversationDetailDTO,
    AssistantActivityDTO,
    ConversationDTO,
    CreateConversationCommand,
    RenameConversationCommand,
    MessageDTO,
)
from .result_schemas import AssistantResult
from .tool_descriptors import localized_tool_descriptors
from .tool_adapters import execute_tool
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
        allowed_writes = request.context.get("allowed_write_tool_names")
        if tool.risk is not ToolRisk.READ and allowed_writes is not None and tool.name not in allowed_writes:
            return False
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
        error = alert_capability_error(_request, tool.name, _call.arguments)
        if error:
            return ToolPermissionDecision.deny(error)
        plan = _request.context.get("watch_request") or {}
        if tool.name == "create_price_alert" and plan.get("requested_channels"):
            from src.platform.persistence.models import NotifyChannel
            ids = _call.arguments.get("notify_channel_ids") or []
            channels = self._repository.session.query(NotifyChannel).filter(NotifyChannel.id.in_(ids), NotifyChannel.enabled == True).all()
            types = {c.type.lower() for c in channels}
            if any(kind not in types for kind in plan["requested_channels"]):
                return ToolPermissionDecision.deny("The selected channels do not match the requested delivery types.")
        return self._decision(tool)

    def _decision(self, tool: ToolSpec) -> ToolPermissionDecision:
        return self._repository.resolve_permission(tool, snapshot=self._permissions)


class AssistantService:
    def __init__(self, repository: AssistantRepository, settings: Settings | None = None) -> None:
        self._repository = repository
        self._settings = settings or Settings()

    async def in_worker(self, operation):
        return await run_db_operation(
            self._repository.session.get_bind(),
            lambda db: operation(AssistantService(AssistantRepository(db), self._settings)),
        )

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

    def rename_conversation(self, conversation_id: int, command: RenameConversationCommand) -> ConversationDTO:
        return self._conversation_dto(self._repository.rename_conversation(
            self._require_conversation(conversation_id), command.title))

    async def generate_conversation_title(self, conversation_id: int, *, persist=None) -> bool:
        from .titles import summarize_title
        conversation = self._require_conversation(conversation_id)
        if conversation.title_source not in ('provisional', 'legacy'):
            return False
        rows = self._repository.list_messages(conversation_id)
        question = next((row.content for row in rows if row.role == 'user'), '')
        answer = next((row.content for row in rows if row.role == 'assistant'), '')
        if not question or not answer:
            return False
        expected_title = conversation.title or ''
        client = self.build_context_compression_client()
        # Release the read transaction before waiting for the model.
        self._repository.session.rollback()
        title = await summarize_title(client, question, answer)
        if not title:
            return False
        return await (persist or self.in_worker)(
            lambda worker: worker._repository.set_automatic_title(conversation_id, title, expected_title)
        )

    def get_suggested_questions(self, symbol: str, market: str = "CN") -> list[str]:
        """Build deterministic prompts from the current local stock context."""
        questions: list[str] = []
        latest_suggestion = (
            self._repository.session.query(StockSuggestion)
            .filter(
                StockSuggestion.stock_symbol == symbol,
                StockSuggestion.stock_market == market,
            )
            .order_by(StockSuggestion.created_at.desc())
            .first()
        )
        if latest_suggestion:
            action = (latest_suggestion.action or "").lower()
            label = latest_suggestion.action_label or latest_suggestion.action or ""
            if action in ("buy", "add"):
                questions.append(f"最新的「{label}」信号可靠吗？入场时机如何？")
            elif action in ("sell", "reduce"):
                questions.append(f"最新给出了「{label}」建议，现在该操作吗？")
            elif action == "alert":
                questions.append("最近的异动提醒是什么情况？需要关注吗？")

        has_position = (
            self._repository.session.query(Position)
            .join(Stock, Position.stock_id == Stock.id)
            .filter(Stock.symbol == symbol, Stock.market == market)
            .first()
        ) is not None
        questions.append(
            "当前持仓该继续持有还是考虑减仓？"
            if has_position
            else "现在适合建仓吗？"
        )
        questions.extend([
            "分析近期走势和关键支撑压力位",
            "有什么值得关注的消息或事件？",
        ])
        return questions[:5]

    def get_conversation(self, conversation_id: int) -> ConversationDetailDTO:
        conversation = self._require_conversation(conversation_id)
        results = self._repository.message_results(conversation_id)
        traces = self._repository.message_traces(conversation_id)
        tasks = self._repository.message_task_runs(conversation_id)
        invocations = self._repository.tool_invocations_for_tasks(
            [int(task.id) for task in tasks.values()]
        )
        messages = self._repository.list_messages(conversation_id)
        language = self._report_language()
        for message in messages:
            task = tasks.get(int(message.id))
            if message.role != "assistant" or int(message.id) in results or task is None:
                continue
            task_invocations = invocations.get(int(task.id), [])
            if task_invocations:
                results[int(message.id)] = build_deterministic_assistant_result(
                    task_id=int(task.id),
                    answer=message.content,
                    invocations=task_invocations,
                    language=language,
                ).model_dump(mode="json")
        return ConversationDetailDTO(
            conversation=self._conversation_dto(conversation),
            latest_task=self._repository.latest_task_snapshot(conversation_id),
            messages=[
                self._message_dto(
                    row,
                    result=results.get(row.id),
                    trace=traces.get(row.id),
                )
                for row in messages
            ],
        )

    def get_context_detail(
        self, conversation_id: int, *, compression_result=None
    ) -> ContextDetailDTO:
        conversation = self._require_conversation(conversation_id)
        messages = self._context_messages(conversation_id)
        budget = self._context_budget()
        latest = self._repository.get_latest_context_snapshot(conversation_id)
        summary = ContextSummary.model_validate(latest.summary) if latest else None
        usage = ContextEngine(
            token_meter=HeuristicTokenMeter(),
            model=self._context_model_name(),
        ).measure(
            messages,
            summary=summary,
            page_context=conversation.initial_context,
            tool_schemas=self._context_tool_schemas(),
            compact_history=latest is not None,
            budget=budget,
        )
        return ContextDetailDTO(
            conversation_id=conversation_id,
            usage=usage,
            snapshot=self._snapshot_dto(latest) if latest else None,
            last_compression=(
                self._compression_dto(compression_result)
                if compression_result is not None
                else self._snapshot_compression_dto(latest)
            ),
            status=usage.state,
        )

    def get_assistant_config(self) -> AssistantConfigDTO:
        values = self._context_config_values()
        models = [
            AssistantModelOption(
                id=model.id,
                name=model.name or model.model,
                model=model.model,
                service_name=service.name,
            )
            for model, service in (
                self._repository.session.query(AIModel, AIService)
                .join(AIService, AIService.id == AIModel.service_id)
                .order_by(AIModel.id.asc())
                .all()
            )
        ]
        return AssistantConfigDTO(**values, models=models)

    def update_assistant_config(self, command: AssistantConfigUpdate) -> AssistantConfigDTO:
        if command.compression_model_id is not None:
            model = (
                self._repository.session.query(AIModel)
                .filter(AIModel.id == command.compression_model_id)
                .first()
            )
            if model is None:
                raise ValueError("压缩模型不存在")

        values = command.model_dump()
        values["compression_model_id"] = (
            str(command.compression_model_id)
            if command.compression_model_id is not None
            else ""
        )
        for field, value in values.items():
            key = f"assistant_{field}"
            row = (
                self._repository.session.query(AppSettings)
                .filter(AppSettings.key == key)
                .first()
            )
            if row is None:
                row = AppSettings(key=key, value=str(value), description="助手配置")
                self._repository.session.add(row)
            else:
                row.value = str(value)
        self._repository.session.commit()
        return self.get_assistant_config()

    async def compress_context(
        self,
        conversation_id: int,
        *,
        mode: ContextCompressionMode = ContextCompressionMode.BALANCED,
    ):
        return await self.prepare_context(
            conversation_id,
            mode=mode,
            force_compress=True,
        )

    async def prepare_context(
        self,
        conversation_id: int,
        *,
        mode: ContextCompressionMode = ContextCompressionMode.BALANCED,
        force_compress: bool = False,
        persist=None,
    ):
        conversation = self._require_conversation(conversation_id)
        rows = self._repository.list_messages(conversation_id)
        messages = self._context_messages(conversation_id, rows=rows)
        latest = self._repository.get_latest_context_snapshot(conversation_id)
        existing_summary = ContextSummary.model_validate(latest.summary) if latest else None
        engine = ContextEngine(
            self.build_context_summarizer(),
            token_meter=HeuristicTokenMeter(),
            model=self._context_model_name(),
        )
        result = await engine.prepare(
            messages,
            existing_summary=existing_summary,
            existing_summary_message_count=latest.source_message_count if latest else 0,
            mode=mode,
            force_compress=force_compress,
            page_context=conversation.initial_context,
            tool_schemas=self._context_tool_schemas(),
            budget=self._context_budget(),
        )
        if result.compressed and result.summary is not None:
            non_system_rows = rows
            old_count = result.covered_message_count
            covered_until = (
                non_system_rows[old_count - 1].id if old_count > 0 else None
            )
            def save(worker):
                return worker._repository.save_context_snapshot(
                    conversation_id,
                    mode=mode,
                    summary=result.summary,
                    covered_until_message_id=covered_until,
                    source_message_count=old_count,
                    usage_before=result.usage_before,
                    usage_after=result.usage_after,
                )
            await (persist or self.in_worker)(save)
        return result

    def _context_model_name(self) -> str | None:
        model = (
            self._repository.session.query(AIModel)
            .filter(AIModel.is_default == True)
            .first()
        )
        return model.model if model is not None else None

    def _context_messages(self, conversation_id: int, *, rows=None) -> list[ModelMessage]:
        messages = build_assistant_messages(
            [
                ModelMessage(role=row.role, content=row.content)
                for row in (rows if rows is not None else self._repository.list_messages(conversation_id))
            ]
        )
        findings = self._repository.list_recent_tool_findings(conversation_id)
        if findings:
            lines = [
                "可信工具执行记录（只以这些记录作为工具已执行的证据；历史助手文本的完成声明不作为工具证据）："
            ]
            for finding in reversed(findings):
                lines.append(f"- {finding.tool_name}: {finding.summary}")
            lines.append("如果当前请求要求继续执行操作，必须重新调用工具并等待成功结果。")
            messages.append(ModelMessage(role="system", content="\n".join(lines)))
        from src.platform.language import resolve_report_language

        report_language = resolve_report_language(self._repository.session)
        instruction = (
                "用户当前界面语言为 English。请用英文撰写自然语言回复和报告；"
                "保留股票代码、专有名词、来源原文及用户指定的引用文字。工具调用参数和结构化字段按原有约定，"
                "do not infer or change the market, currency, or time zone from this preference."
                if report_language == "en-US"
                else "用户当前界面语言为简体中文。请用简体中文撰写自然语言回复和报告；"
                "保留股票代码、专有名词、来源原文及用户指定的引用文字。工具调用参数和结构化字段按原有约定，"
                "不要因此推断或更改市场、币种或时区。"
        )
        messages.append(ModelMessage(role="system", content=instruction))
        return messages

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

    def get_activity(self) -> AssistantActivityDTO:
        return AssistantActivityDTO.model_validate(self._repository.get_activity())

    def read_notifications(self, *, ids: list[int], through_id: int | None = None) -> int:
        return self._repository.read_notifications(ids=ids, through_id=through_id)

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
        tools = build_panwatch_tool_registry(self._repository.session)
        return AgentRuntime(
            FailoverModelAdapter(failover_client),
            tools,
            policy=self.build_tool_policy(),
            extensions=(
                [
                    PortfolioDiagnosisExtension(
                        self._repository.session,
                        failover_client,
                        registry=tools,
                    ),
                    ToolResearchPlugin(
                        ToolResearchService(
                            tools,
                            selection_policy=ToolSelectionPolicy(max_write_tools=3),
                            descriptors=list(
                                localized_tool_descriptors(
                                    self._report_language()
                                )
                            ),
                        ),
                        mode="active",
                        include_write_tools=True,
                    )
                ]
                if self._settings.tool_research_enabled
                else []
            ),
        )

    def build_tool_policy(self) -> PanWatchToolPolicy:
        """Freeze a user's preferences for the lifetime of one runtime run."""
        return PanWatchToolPolicy(
            self._repository, self._repository.permission_snapshot()
        )

    def _report_language(self) -> str:
        from src.platform.language import resolve_report_language

        return resolve_report_language(self._repository.session)

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

    def build_context_compression_client(self):
        """Resolve the optional summary model through the shared failover path."""
        return get_configured_failover_client(
            self._repository.session,
            self._context_config_values()["compression_model_id"],
        )

    def build_context_summarizer(self) -> FailoverContextSummarizer:
        config = self._context_config_values()
        return FailoverContextSummarizer(
            self.build_context_compression_client(),
            temperature=config["compression_temperature"],
            max_summary_tokens=config["summary_max_tokens"],
        )

    def _context_budget(self) -> ContextBudget:
        config = self._context_config_values()
        return ContextBudget(
            max_tokens=config["max_tokens"],
            soft_limit_tokens=config["soft_limit_tokens"],
            hard_limit_tokens=config["hard_limit_tokens"],
            keep_recent_messages=config["keep_recent_messages"],
            summary_max_tokens=config["summary_max_tokens"],
        )

    def _context_config_values(self) -> dict[str, object]:
        defaults = {
            "compression_model_id": getattr(self._settings, "context_compression_model_id", None),
            "compression_temperature": getattr(self._settings, "context_compression_temperature", 0.1),
            "summary_max_tokens": getattr(self._settings, "context_summary_max_tokens", 800),
            "max_tokens": getattr(self._settings, "context_max_tokens", 12_000),
            "soft_limit_tokens": getattr(self._settings, "context_soft_limit_tokens", 8_400),
            "hard_limit_tokens": getattr(self._settings, "context_hard_limit_tokens", 10_200),
            "keep_recent_messages": getattr(self._settings, "context_keep_recent_messages", 8),
        }
        casts = {
            "compression_model_id": int,
            "compression_temperature": float,
            "summary_max_tokens": int,
            "max_tokens": int,
            "soft_limit_tokens": int,
            "hard_limit_tokens": int,
            "keep_recent_messages": int,
        }
        for field, cast in casts.items():
            row = (
                self._repository.session.query(AppSettings)
                .filter(AppSettings.key == f"assistant_{field}")
                .first()
            )
            if row is None or row.value in (None, ""):
                continue
            try:
                defaults[field] = cast(row.value)
            except (TypeError, ValueError):
                continue
        return AssistantConfigUpdate(**defaults).model_dump()

    def _context_tool_schemas(self) -> list[dict]:
        """Estimate the definitions registered for the assistant model input."""
        return [
            tool.openai_schema()
            for tool in build_panwatch_tool_registry(self._repository.session).registered_tools()
        ]

    @staticmethod
    def _snapshot_dto(snapshot) -> ContextSnapshotDTO:
        return ContextSnapshotDTO(
            version=snapshot.version,
            mode=ContextCompressionMode(snapshot.mode),
            summary=ContextSummary.model_validate(snapshot.summary or {}),
            covered_until_message_id=snapshot.covered_until_message_id,
            source_message_count=snapshot.source_message_count,
            usage_before=ContextUsage.model_validate(snapshot.usage_before or {}),
            usage_after=ContextUsage.model_validate(snapshot.usage_after or {}),
            created_at=snapshot.created_at,
        )

    @staticmethod
    def _compression_dto(result) -> ContextCompressionDTO:
        before = result.usage_before.total_tokens
        after = result.usage_after.total_tokens
        saved = max(0, before - after)
        return ContextCompressionDTO(
            status=result.compression_status,
            mode=result.mode,
            usage_before=result.usage_before,
            usage_after=result.usage_after,
            saved_tokens=saved,
            saved_percent=round(saved / max(before, 1) * 100),
            compressed_message_count=result.compressed_message_count,
        )

    @classmethod
    def _snapshot_compression_dto(cls, snapshot) -> ContextCompressionDTO | None:
        if snapshot is None or not snapshot.usage_before or not snapshot.usage_after:
            return None
        before = ContextUsage.model_validate(snapshot.usage_before)
        after = ContextUsage.model_validate(snapshot.usage_after)
        saved = max(0, before.total_tokens - after.total_tokens)
        return ContextCompressionDTO(
            status="compressed",
            mode=ContextCompressionMode(snapshot.mode),
            usage_before=before,
            usage_after=after,
            saved_tokens=saved,
            saved_percent=round(saved / max(before.total_tokens, 1) * 100),
            compressed_message_count=snapshot.source_message_count,
        )

    def create_task(self, conversation_id: int, user_message_id: int):
        plan = self.watch_request_for_message(conversation_id, user_message_id)
        message = self._repository.get_message(user_message_id)
        content = message.content if message else ""
        return self._repository.create_task(
            conversation_id=conversation_id,
            user_message_id=user_message_id,
            context={**self._write_action_context(content), "watch_request": plan},
        )

    def watch_request_for_message(self, conversation_id: int, user_message_id: int) -> dict:
        conversation = self._require_conversation(conversation_id)
        message = self._repository.get_message(user_message_id)
        content = message.content if message else ""
        history = [row for row in self._repository.list_messages(conversation_id) if row.id < user_message_id]
        plan = resolve_watch_request(content, history, context={"stock_symbol": conversation.stock_symbol, "stock_market": conversation.stock_market})
        source_id = plan.get("referenced_user_message_id")
        if source_id:
            saved = self._repository.watch_request_for_message(conversation_id, source_id)
            if saved and saved.get("original_text") == plan["original_text"] and saved.get("horizon"):
                # Recheck current capabilities but never extend a saved expiry.
                plan["horizon"] = saved.get("horizon")
        return plan

    def refresh_legacy_watch_retry(self, task_run, plan: dict) -> dict:
        if task_run.user_message_id and is_watch_retry(plan.get("original_text", "")) and not plan.get("request_source"):
            return self.watch_request_for_message(task_run.conversation_id, task_run.user_message_id)
        return plan

    @staticmethod
    def _write_action_context(content: str) -> dict:
        """Constrain explicit write requests to one approval-protected tool."""
        normalized = content.strip().lower()
        alert_request = any(term in normalized for term in ("提醒", "预警", "alert"))
        if not alert_request:
            return {}
        create_request = any(
            term in normalized
            for term in ("创建", "设置", "新增", "提醒我", "create", "set ")
        )
        has_exact_target = re.search(
            r"(?<![a-z0-9.])(?:cn|hk|us):[a-z0-9.]+(?![a-z0-9.])",
            normalized,
        )
        content_without_target = (
            f"{normalized[:has_exact_target.start()]} {normalized[has_exact_target.end():]}"
            if has_exact_target
            else normalized
        )
        has_price = re.search(
            r"(?<![a-z0-9.])\d+(?:\.\d+)?(?![a-z0-9.])",
            content_without_target,
        )
        if not create_request or not has_exact_target or not has_price:
            return {}
        return {
            "tool_choice": "required",
            "allowed_tool_names": ["check_watch_request", "get_notification_channels", "get_price_alerts", "search_stocks", "get_stock_quote", "create_price_alert", "tool_search"],
            "allowed_write_tool_names": ["create_price_alert"],
            "action_source": "explicit_user_request",
        }

    def record_tool_completion(self, task_id: int, data: dict) -> None:
        self._repository.record_tool_completed(
            task_id,
            call_id=data.get("call_id", ""),
            tool_name=data.get("tool", ""),
            summary=data.get("summary", ""),
            ok=bool(data.get("ok", False)),
            duration_ms=int(data.get("duration_ms") or 0),
            attempt_count=int(data.get("attempt_count") or 1),
            error_code=data.get("error_code"),
            result_data=data.get("data") or {},
            sources=data.get("sources") or [],
            observed_at=data.get("observed_at"),
        )

    def record_tool_started(self, task_id: int, data: dict) -> None:
        self._repository.record_tool_started(
            task_id,
            call_id=data.get("call_id", ""),
            tool_name=data.get("tool", ""),
            arguments=data.get("arguments") or {},
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

    def complete_task_with_message(
        self,
        task_id: int,
        conversation_id: int,
        content: str,
        *,
        result: AssistantResult | None = None,
    ) -> MessageDTO | None:
        message = self._repository.complete_task_with_message(
            task_id,
            conversation_id,
            content,
            result_data=result.model_dump(mode="json") if result else None,
        )
        return (
            self._message_dto(
                message,
                result=result.model_dump(mode="json") if result else None,
            )
            if message is not None
            else None
        )

    def finish_task(self, task_id: int, result, final_message_id: int) -> None:
        self._repository.finish_task(
            task_id,
            status=result.status.value,
            final_message_id=final_message_id,
            error_code=result.error_code,
            event_data={
                "message_id": final_message_id,
                "content": result.answer or "",
            },
        )

    def fail_task(self, task_id: int, error_code: str) -> None:
        """Close a task that could not yield a usable assistant answer."""
        from src.platform.ai.errors import descriptor_for_code

        descriptor = descriptor_for_code(error_code)
        self._repository.finish_task(
            task_id,
            status="failed",
            final_message_id=None,
            error_code=error_code,
            event_data={
                "code": error_code,
                "message": descriptor.message if error_code.startswith("ai_") else error_code,
                "retryable": descriptor.retryable if error_code.startswith("ai_") else True,
            },
        )

    def cancel_task(self, task_id: int) -> dict:
        try:
            self._repository.cancel_task(task_id)
            return self._repository.get_task_snapshot(task_id)
        except LookupError as exc:
            raise AssistantNotFoundError(str(exc)) from exc

    def retry_task(self, task_id: int) -> dict:
        try:
            task = self._repository.retry_task(task_id)
            return self._repository.get_task_snapshot(task.id)
        except LookupError as exc:
            raise AssistantNotFoundError(str(exc)) from exc

    def _approval_presentation(self, pending) -> dict[str, str]:
        """Translate host tool arguments into the text a human needs to approve."""
        from src.platform.language import resolve_report_language

        english = resolve_report_language(self._repository.session) == "en-US"
        arguments = pending.arguments

        if pending.tool_name in {"create_price_alert", "update_price_alert"}:
            from src.platform.persistence.models import NotifyChannel
            create = pending.tool_name == "create_price_alert"
            values = ({"cooldown_minutes": 30, "max_triggers_per_day": 3, "repeat_mode": "repeat", "market_hours_mode": "trading_only", "expire_at": None, "notify_channel_ids": [], **arguments} if create else arguments)
            parts = []
            if values.get("condition_group"):
                parts.append(condition_summary(values["condition_group"], english))
            elif "direction" in values or "target_price" in values:
                direction = {"above": "≥", "below": "≤"}.get(values.get("direction"), "unchanged" if english else "方向不变")
                price = values.get("target_price", "unchanged" if english else "目标不变")
                parts.append(f"{'Price' if english or create else '目标价'} {direction} {price}" if english or not create else f"价格 {direction} {price}")
            if "name" in values:
                parts.append(f"{'Name' if english else '名称'}: {values['name']}")
            if "enabled" in values:
                parts.append(("Enable" if values["enabled"] else "Disable") if english else ("启用" if values["enabled"] else "停用"))
            if "cooldown_minutes" in values:
                parts.append(f"Cooldown {values['cooldown_minutes']} minutes" if english else f"冷却 {values['cooldown_minutes']} 分钟")
            if "max_triggers_per_day" in values:
                maximum = values["max_triggers_per_day"]
                parts.append((f"At most {maximum} triggers per day" if english else f"每日最多触发 {maximum} 次") if maximum else ("Unlimited daily triggers" if english else "每日不限次数"))
            if "repeat_mode" in values:
                once = values["repeat_mode"] == "once"
                parts.append(("Trigger once" if once else "Repeat triggers") if english else ("仅触发一次" if once else "重复提醒"))
            if "market_hours_mode" in values:
                trading = values["market_hours_mode"] == "trading_only"
                parts.append(("Trading hours only" if trading else "All hours") if english else ("仅交易时段" if trading else "全天检测"))
            if "expire_at" in values:
                expiry = values["expire_at"] or ("Unlimited" if english else "无限期")
                parts.append(f"{'Expiry' if english else '到期'}: {expiry}")
            if "notify_channel_ids" in values:
                ids = values["notify_channel_ids"] or []
                query = self._repository.session.query(NotifyChannel)
                channels = query.filter(NotifyChannel.id.in_(ids)).all() if ids else query.filter(NotifyChannel.enabled == True, NotifyChannel.is_default == True).all()
                channel_text = ", ".join(f"{c.name} ({c.type}, #{c.id})" for c in channels)
                if not channel_text:
                    channel_text = "No enabled default channels; delivery unavailable" if english else "无启用的默认渠道，无法投递"
                parts.append(f"{'Channels' if english else '通知渠道'}: {channel_text}")
            target = f"{arguments.get('market') or 'CN'}:{arguments.get('symbol', '')}" if create else f"#{arguments.get('rule_id', '?')}"
            title = ("Create price alert" if create else "Update price alert") if english else ("创建价格提醒" if create else "修改价格提醒")
            return {"tool_title": title, "summary": f"{title} {target}" + (": " if english else "：") + ("; " if english else "；").join(parts) + ("." if english else "。")}

        if pending.tool_name == "delete_price_alert":
            rule_id = arguments.get("rule_id", "?")
            return {
                "tool_title": "Delete price alert" if english else "删除价格提醒",
                "summary": f"Delete price alert #{rule_id} and its trigger history." if english else f"删除价格提醒 #{rule_id} 及其历史命中记录。",
            }

        return {
            "tool_title": "Action requiring approval" if english else "需要授权的操作",
            "summary": f"Run {pending.tool_name}." if english else f"将调用 {pending.tool_name}。",
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
            title_source=conversation.title_source,
            stock_symbol=conversation.stock_symbol,
            stock_market=conversation.stock_market,
            created_at=conversation.created_at,
        )

    @staticmethod
    def _message_dto(
        message,
        *,
        result: dict | None = None,
        trace: list[dict] | None = None,
    ) -> MessageDTO:
        return MessageDTO(
            id=message.id,
            role=message.role,
            content=message.content,
            created_at=message.created_at,
            result=AssistantResult.model_validate(result) if result else None,
            trace=trace,
        )
