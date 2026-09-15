"""Single-process durable runner for assistant tasks.

The runner is intentionally small and host-owned.  P2 can replace its
``asyncio`` task registry with a queue adapter without changing task/event
contracts or the PanAgent runtime.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable

from pan_agent import (
    EventType,
    ModelMessage,
    RunLimits,
    RunRequest,
    RunResult,
    RunStatus,
    RuntimeEvent,
)

from src.platform.persistence.database import SessionLocal
from src.platform.tasking.contracts import TaskEventType, TaskStatus

from .prompt import build_assistant_messages
from .repository import AssistantRepository
from .service import AssistantService

logger = logging.getLogger(__name__)

ASSISTANT_RUN_TIMEOUT_SECONDS = 180
ASSISTANT_TOOL_TIMEOUT_SECONDS = 15
ASSISTANT_MAX_STEPS = 12
ASSISTANT_MAX_TOOL_CALLS = 24
ANSWER_TOKEN_BATCH_CHARS = 128
ANSWER_TOKEN_BATCH_INTERVAL_SECONDS = 0.1

_ERROR_MESSAGES = {
    "run_timeout": "助手响应超时，请稍后重试。",
    "empty_answer": "助手暂时不可用，请稍后重试。",
    "transport_failed": "助手任务执行失败，请稍后重试。",
    "worker_cancelled": "助手任务已停止。",
}


class TaskCancelledError(RuntimeError):
    """Raised at a safe event boundary after a task cancellation request."""


class DurableRuntimeEventSink:
    """Translate runtime facts into durable task events."""

    def __init__(self, service: AssistantService, task_id: int, context_result=None) -> None:
        self._service = service
        self._task_id = task_id
        self._context_result = context_result
        self._pending_answer_tokens: list[str] = []
        self._pending_answer_chars = 0
        self._last_answer_flush_at = time.monotonic()

    async def publish(self, event: RuntimeEvent) -> None:
        data = dict(event.data)
        repository = self._service._repository
        if event.type is EventType.ANSWER_TOKEN:
            token = str(data.get("token") or "")
            if token:
                self._pending_answer_tokens.append(token)
                self._pending_answer_chars += len(token)
            if (
                self._pending_answer_chars >= ANSWER_TOKEN_BATCH_CHARS
                or time.monotonic() - self._last_answer_flush_at
                >= ANSWER_TOKEN_BATCH_INTERVAL_SECONDS
            ):
                await self.flush()
            await asyncio.sleep(0)
            return

        if self._service._repository.is_task_cancelled(self._task_id):
            raise TaskCancelledError("task cancelled")

        await self.flush()
        if event.type is EventType.RUN_CREATED:
            return
        if event.type is EventType.STEP_UPDATED:
            repository.append_task_event(
                self._task_id,
                TaskEventType.STEP_PROGRESS,
                status=TaskStatus.RUNNING,
                step_index=data.get("step"),
                data=data,
            )
            return
        if event.type is EventType.EXTENSION_EVENT:
            repository.append_task_event(
                self._task_id,
                TaskEventType.EXTENSION_EVENT,
                status=TaskStatus.RUNNING,
                data=data,
            )
            return
        if event.type is EventType.TOOL_STARTED:
            self._service.record_tool_started(self._task_id, data)
            return
        if event.type is EventType.TOOL_COMPLETED:
            self._service.record_tool_completion(self._task_id, data)
            return
        if event.type is EventType.APPROVAL_REQUIRED:
            return

    async def flush(self, *, check_cancelled: bool = True) -> None:
        """Persist buffered answer text without reordering surrounding events."""
        if not self._pending_answer_tokens:
            return
        if check_cancelled and self._service._repository.is_task_cancelled(self._task_id):
            raise TaskCancelledError("task cancelled")

        text = "".join(self._pending_answer_tokens)
        self._service._repository.append_task_event(
            self._task_id,
            TaskEventType.ANSWER_TOKEN,
            status=TaskStatus.RUNNING,
            data={"text": text},
        )
        self._pending_answer_tokens.clear()
        self._pending_answer_chars = 0
        self._last_answer_flush_at = time.monotonic()


class AssistantTaskRunner:
    """Run durable assistant tasks in the current process."""

    def __init__(self, session_factory: Callable = SessionLocal) -> None:
        self._session_factory = session_factory
        self._tasks: dict[int, asyncio.Task] = {}

    def start_message(self, task_id: int, conversation_id: int) -> None:
        self._start(
            task_id,
            self._run_message(task_id, conversation_id),
        )

    def start_resume(self, task_id: int, conversation_id: int, checkpoint, decisions: dict) -> None:
        self._start(
            task_id,
            self._run_resume(task_id, conversation_id, checkpoint, decisions),
        )

    def cancel(self, task_id: int) -> bool:
        worker = self._tasks.get(task_id)
        if worker is None or worker.done():
            return False
        worker.cancel()
        return True

    async def recover_pending(self) -> None:
        """Requeue safe queued work and fail stale in-flight work explicitly."""
        db = self._session_factory()
        try:
            repository = AssistantRepository(db)
            tasks = repository.list_tasks_for_recovery()
            for task in tasks:
                if task.status == TaskStatus.QUEUED.value:
                    self.start_message(task.id, task.conversation_id)
                    continue
                repository.finish_task(
                    task.id,
                    status=TaskStatus.FAILED.value,
                    final_message_id=None,
                    error_code="worker_restarted",
                    event_data={
                        "code": "worker_restarted",
                        "message": "任务所在 worker 已重启，任务未能从安全检查点恢复。",
                    },
                )
        finally:
            db.close()

    def _start(self, task_id: int, coroutine) -> None:
        existing = self._tasks.get(task_id)
        if existing is not None and not existing.done():
            coroutine.close()
            return
        worker = asyncio.create_task(coroutine)
        self._tasks[task_id] = worker

        def cleanup(completed: asyncio.Task) -> None:
            if self._tasks.get(task_id) is completed:
                self._tasks.pop(task_id, None)
            try:
                completed.result()
            except asyncio.CancelledError:
                pass
            except Exception:
                logger.exception("Assistant task worker crashed: task_id=%s", task_id)

        worker.add_done_callback(cleanup)

    async def _run_message(self, task_id: int, conversation_id: int) -> None:
        db = self._session_factory()
        service = AssistantService(AssistantRepository(db))
        sink: DurableRuntimeEventSink | None = None
        try:
            if not service._repository.claim_task(task_id):
                return
            if service._repository.is_task_cancelled(task_id):
                return
            context_result = await service.prepare_context(conversation_id)
            if context_result is not None:
                service._repository.append_task_event(
                    task_id,
                    TaskEventType.CONTEXT_PREPARED,
                    status=TaskStatus.RUNNING,
                    data={
                        "compressed": context_result.compressed,
                        "compression_status": context_result.compression_status,
                        "mode": context_result.mode.value,
                        "usage_before": context_result.usage_before.model_dump(mode="json"),
                        "usage_after": context_result.usage_after.model_dump(mode="json"),
                        "compressed_message_count": context_result.compressed_message_count,
                    },
                )
            runtime = service.build_runtime(service.build_failover_client())
            messages = (
                context_result.messages
                if context_result is not None
                else build_assistant_messages(
                    [
                        ModelMessage(role=item.role, content=item.content)
                        for item in service.get_conversation(conversation_id).messages
                    ]
                )
            )
            request = RunRequest(
                run_id=str(task_id),
                messages=messages,
                context=(
                    {
                        "context_usage": context_result.usage_after.model_dump(mode="json"),
                        "context_compressed": context_result.compressed,
                    }
                    if context_result is not None
                    else {}
                ),
                limits=RunLimits(
                    max_steps=ASSISTANT_MAX_STEPS,
                    max_tool_calls=ASSISTANT_MAX_TOOL_CALLS,
                    run_timeout_seconds=ASSISTANT_RUN_TIMEOUT_SECONDS,
                    tool_timeout_seconds=ASSISTANT_TOOL_TIMEOUT_SECONDS,
                ),
            )
            sink = DurableRuntimeEventSink(service, task_id, context_result)
            result = await asyncio.wait_for(
                runtime.run(
                    request,
                    sink,
                ),
                timeout=ASSISTANT_RUN_TIMEOUT_SECONDS,
            )
            await sink.flush()
            await self._finish_result(service, task_id, conversation_id, result)
        except TaskCancelledError:
            await self._flush_sink(sink)
            self._cancel_if_needed(service, task_id)
        except asyncio.TimeoutError:
            await self._flush_sink(sink)
            self._fail(service, task_id, "run_timeout")
        except asyncio.CancelledError:
            await self._flush_sink(sink)
            if not service._repository.is_task_cancelled(task_id):
                self._fail(service, task_id, "worker_cancelled")
            raise
        except Exception:
            await self._flush_sink(sink)
            logger.exception("Assistant task failed: task_id=%s", task_id)
            self._fail(service, task_id, "transport_failed")
        finally:
            db.close()

    async def _run_resume(
        self, task_id: int, conversation_id: int, checkpoint, decisions: dict
    ) -> None:
        db = self._session_factory()
        service = AssistantService(AssistantRepository(db))
        sink: DurableRuntimeEventSink | None = None
        try:
            if not service._repository.claim_task(task_id):
                return
            if service._repository.is_task_cancelled(task_id):
                return
            runtime = service.build_runtime(service.build_failover_client())
            request = RunRequest(
                run_id=str(task_id),
                messages=checkpoint.messages,
                limits=RunLimits(
                    max_steps=ASSISTANT_MAX_STEPS,
                    max_tool_calls=ASSISTANT_MAX_TOOL_CALLS,
                    run_timeout_seconds=ASSISTANT_RUN_TIMEOUT_SECONDS,
                    tool_timeout_seconds=ASSISTANT_TOOL_TIMEOUT_SECONDS,
                ),
            )
            sink = DurableRuntimeEventSink(service, task_id)
            result = await asyncio.wait_for(
                runtime.resume(
                    request,
                    checkpoint,
                    decisions,
                    sink,
                ),
                timeout=ASSISTANT_RUN_TIMEOUT_SECONDS,
            )
            await sink.flush()
            await self._finish_result(service, task_id, conversation_id, result)
        except TaskCancelledError:
            await self._flush_sink(sink)
            self._cancel_if_needed(service, task_id)
        except asyncio.TimeoutError:
            await self._flush_sink(sink)
            self._fail(service, task_id, "run_timeout")
        except asyncio.CancelledError:
            await self._flush_sink(sink)
            if not service._repository.is_task_cancelled(task_id):
                self._fail(service, task_id, "worker_cancelled")
            raise
        except Exception:
            await self._flush_sink(sink)
            logger.exception("Assistant approval resume failed: task_id=%s", task_id)
            self._fail(service, task_id, "transport_failed")
        finally:
            db.close()

    async def _finish_result(
        self, service: AssistantService, task_id: int, conversation_id: int, result: RunResult
    ) -> None:
        if result.status is RunStatus.WAITING_FOR_APPROVAL:
            approvals = service.pause_task(task_id, result)
            for approval in approvals:
                service._repository.append_task_event(
                    task_id,
                    TaskEventType.APPROVAL_REQUIRED,
                    status=TaskStatus.WAITING_APPROVAL,
                    data={
                        "approval_id": approval.id,
                        "call_id": approval.call_id,
                        "name": approval.tool_name,
                        "risk": approval.risk,
                        "arguments": approval.arguments or {},
                        "expires_at": approval.expires_at.isoformat()
                        if approval.expires_at
                        else "",
                    },
                )
            service._repository.append_task_event(
                task_id,
                TaskEventType.TASK_PAUSED,
                status=TaskStatus.WAITING_APPROVAL,
                data={"reason": "approval_required"},
            )
            return
        if result.status is not RunStatus.COMPLETED or not result.answer.strip():
            self._fail(service, task_id, result.error_code or "empty_answer")
            return
        service.complete_task_with_message(task_id, conversation_id, result.answer)

    def _fail(self, service: AssistantService, task_id: int, error_code: str) -> None:
        service._repository.finish_task(
            task_id,
            status=TaskStatus.FAILED.value,
            final_message_id=None,
            error_code=error_code,
            event_data={
                "code": error_code,
                "message": _ERROR_MESSAGES.get(error_code, _ERROR_MESSAGES["transport_failed"]),
            },
        )

    async def _flush_sink(self, sink: DurableRuntimeEventSink | None) -> None:
        if sink is None:
            return
        try:
            await sink.flush(check_cancelled=False)
        except Exception:
            logger.exception("Failed to flush assistant answer tokens: task_id=%s", sink._task_id)

    def _cancel_if_needed(self, service: AssistantService, task_id: int) -> None:
        if not service._repository.is_task_cancelled(task_id):
            service._repository.cancel_task(task_id)


assistant_task_runner = AssistantTaskRunner()
