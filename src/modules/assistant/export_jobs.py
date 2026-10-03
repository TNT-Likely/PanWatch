"""Durable, single-process workers for read-only context export jobs."""

from __future__ import annotations

import asyncio
import hashlib
import logging
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import func
from sqlalchemy.dialects.sqlite import insert

from src.modules.notifications.service import NotificationService
from src.platform.ai.errors import classify_ai_service_error
from src.platform.persistence.database import SessionLocal
from src.platform.persistence.models import AssistantContextExport, ChatConversation
from src.platform.tasking.contracts import TaskStatus

from .exports import (
    ContextExportError, ContextExportHistoryDTO, ContextExportJobDTO,
    ContextExportJobInfoDTO, HandoffSummary,
    export_source, render_export, summarize_export,
)
from .repository import AssistantRepository
from .schemas import ConversationDetailDTO
from .service import AssistantService

logger = logging.getLogger(__name__)
EXPORT_JOB_TIMEOUT_SECONDS = 900


def utc_now():
    return datetime.now(timezone.utc)


class ExportLeaseLost(RuntimeError):
    pass


class ExportJobRepository:
    def __init__(self, db):
        self.db = db

    def get(self, job_id: int):
        job = self.db.get(AssistantContextExport, job_id)
        if job is None or self.db.get(ChatConversation, job.conversation_id) is None:
            raise LookupError('Export is unavailable')
        return job

    @staticmethod
    def info(job, total_chars: int):
        def aware(value):
            return value.replace(tzinfo=timezone.utc) if value and value.tzinfo is None else value
        snapshot = job.snapshot or {}
        return ContextExportJobInfoDTO(
            id=job.id, conversation_id=job.conversation_id, status=job.status,
            title=(snapshot.get('conversation') or {}).get('title') or '',
            language=job.language, created_at=aware(job.created_at),
            started_at=aware(job.started_at), finished_at=aware(job.finished_at),
            message_count=len(snapshot.get('messages') or []),
            processed_chars=job.processed_chars, total_chars=total_chars,
            completed_parts=job.completed_parts, error_code=job.error_code,
        )

    @classmethod
    def dto(cls, job):
        return ContextExportJobDTO(**cls.info(job, len(job.source)).model_dump(), result=job.result)

    def history(self, *, conversation_id: int | None = None, before_id: int | None = None,
                ids: list[int] | None = None, limit: int = 20):
        model = AssistantContextExport
        # History never loads the saved transcript or Markdown bodies.
        query = self.db.query(
            model.id, model.conversation_id, model.language, model.status, model.snapshot,
            model.processed_chars, model.completed_parts, model.error_code,
            model.created_at, model.started_at, model.finished_at,
            func.length(model.source).label('total_chars'),
        ).join(ChatConversation, ChatConversation.id == model.conversation_id)
        if conversation_id is not None:
            query = query.filter(model.conversation_id == conversation_id)
        if before_id is not None:
            query = query.filter(model.id < before_id)
        if ids is not None:
            query = query.filter(model.id.in_(ids))
        rows = query.order_by(model.id.desc()).limit(limit + 1).all()
        return ContextExportHistoryDTO(
            items=[self.info(row, row.total_chars) for row in rows[:limit]],
            next_cursor=rows[limit - 1].id if len(rows) > limit else None,
        )

    def create(self, conversation_id: int, language: str):
        service = AssistantService(AssistantRepository(self.db))
        conversation = service._require_conversation(conversation_id)
        detail = service.get_conversation(conversation_id)
        visible = [message for message in detail.messages if message.role in ('user', 'assistant') and message.content.strip()]
        if not visible:
            raise ContextExportError('assistant_export_empty', '会话暂无可总结的内容。')
        budget = int(service._context_config_values()['max_tokens'])
        source = export_source(detail, conversation.initial_context)
        fingerprint = hashlib.sha256(f'v3:{language}:{source}'.encode()).hexdigest()
        # Rendering metadata needs IDs and roles, not another copy of messages.
        snapshot = detail.model_copy(update={
            'latest_task': {'status': (detail.latest_task or {}).get('status')},
            'messages': [message.model_copy(update={
                'content': '[saved]', 'trace': None, 'result': None,
            }) for message in visible],
        }).model_dump(mode='json')
        self.db.execute(insert(AssistantContextExport).values(
            conversation_id=conversation_id, fingerprint=fingerprint, language=language,
            status=TaskStatus.QUEUED.value, source=source, snapshot=snapshot,
            context_budget=budget, created_at=utc_now(),
        ).on_conflict_do_nothing(index_elements=['conversation_id', 'fingerprint']))
        self.db.commit()
        return self.db.query(AssistantContextExport).filter_by(
            conversation_id=conversation_id, fingerprint=fingerprint,
        ).one()

    def retry(self, job_id: int):
        job = self.get(job_id)
        budget = int(AssistantService(AssistantRepository(self.db))._context_config_values()['max_tokens'])
        reset_progress = job.error_code == 'assistant_export_too_large' and job.completed_parts >= 32
        values = {'status': TaskStatus.QUEUED.value, 'error_code': None,
                  'lease_token': '', 'finished_at': None, 'context_budget': budget}
        if reset_progress:
            values.update(processed_chars=0, completed_parts=0, summary=None)
        changed = self.db.query(AssistantContextExport).filter_by(
            id=job_id, status=TaskStatus.FAILED.value,
        ).update(values, synchronize_session=False)
        if changed:
            NotificationService(self.db).resolve(subject_kind='assistant_export', subject_id=str(job_id))
        self.db.commit()
        self.db.refresh(job)
        return job


class ContextExportRunner:
    def __init__(self, session_factory=SessionLocal):
        self.session_factory = session_factory
        self.tasks: dict[int, asyncio.Task] = {}
        self.slots = asyncio.Semaphore(2)

    def start(self, job_id: int):
        if job_id in self.tasks and not self.tasks[job_id].done():
            return
        task = asyncio.create_task(self._run(job_id))
        self.tasks[job_id] = task

        def done(completed):
            if self.tasks.get(job_id) is completed:
                self.tasks.pop(job_id, None)
            if not completed.cancelled() and completed.exception():
                logger.error('Context export worker failed: job_id=%s', job_id, exc_info=completed.exception())
        task.add_done_callback(done)

    def _recover_pending(self):
        with self.session_factory() as db:
            # Safe to resume: no tools or external business writes are executed.
            db.query(AssistantContextExport).filter_by(status='running').update(
                {'status': 'queued', 'lease_token': ''}, synchronize_session=False)
            db.commit()
            jobs = [row[0] for row in db.query(AssistantContextExport.id).filter_by(status='queued')]
        return jobs

    async def recover_pending(self):
        for job_id in await self._work(self._recover_pending):
            self.start(job_id)

    async def shutdown(self):
        workers = list(self.tasks.values())
        for worker in workers:
            worker.cancel()
        await asyncio.gather(*workers, return_exceptions=True)

    def progress(self, job_id, token, offset, count, summary):
        with self.session_factory() as db:
            changed = db.query(AssistantContextExport).filter_by(
                id=job_id, status='running', lease_token=token,
            ).update({'processed_chars': offset, 'completed_parts': count,
                      'summary': summary.model_dump()}, synchronize_session=False)
            if not changed:
                raise ExportLeaseLost()
            db.commit()
        logger.info('Context export progress: job_id=%s completed_parts=%s processed_chars=%s', job_id, count, offset)

    def finish(self, job_id, token, *, result=None, error_code=None):
        with self.session_factory() as db:
            job = db.get(AssistantContextExport, job_id)
            if job is None or db.get(ChatConversation, job.conversation_id) is None:
                return
            status = 'failed' if error_code else 'completed'
            changed = db.query(AssistantContextExport).filter_by(
                id=job_id, status='running', lease_token=token,
            ).update({'status': status, 'result': result.model_dump(mode='json') if result else None,
                      'error_code': error_code, 'finished_at': utc_now()}, synchronize_session=False)
            if not changed:
                return
            conversation = db.get(ChatConversation, job.conversation_id)
            NotificationService(db).publish(
                source='assistant', event_type=f'assistant_export_{status}',
                dedupe_key=f'assistant_export:{job_id}:{job.attempt}:{status}',
                subject_kind='assistant_export', subject_id=str(job_id),
                title=conversation.title, group_key=f'assistant_export:{job_id}',
                severity='warning' if error_code else 'info',
                action={'kind': 'assistant_export', 'export_id': job_id, 'conversation_id': job.conversation_id},
            )
            db.commit()

    def requeue(self, job_id, token):
        with self.session_factory() as db:
            db.query(AssistantContextExport).filter_by(id=job_id, status='running', lease_token=token).update(
                {'status': 'queued', 'lease_token': ''}, synchronize_session=False)
            db.commit()

    def _claim(self, job_id, token):
        with self.session_factory() as db:
            changed = db.query(AssistantContextExport).filter_by(id=job_id, status='queued').update({
                'status': 'running', 'lease_token': token, 'started_at': utc_now(),
                'attempt': AssistantContextExport.attempt + 1,
            }, synchronize_session=False)
            db.commit()
            if not changed:
                return None
            job = ExportJobRepository(db).get(job_id)
            source, language, budget = job.source, job.language, job.context_budget
            detail = ConversationDetailDTO.model_validate(job.snapshot)
            created = job.created_at.replace(tzinfo=timezone.utc)
            offset, count = job.processed_chars, job.completed_parts
            previous = HandoffSummary.model_validate(job.summary) if job.summary else None
            client = AssistantService(AssistantRepository(db)).build_context_compression_client()
        return source, language, budget, detail, created, offset, count, previous, client

    async def _work(self, function, *args, **kwargs):
        pending = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
        try:
            return await asyncio.shield(pending)
        except asyncio.CancelledError:
            await pending
            raise

    async def _run(self, job_id):
        async with self.slots:
            token = uuid4().hex
            try:
                claimed = await self._work(self._claim, job_id, token)
                if claimed is None:
                    return
                source, language, budget, detail, created, offset, count, previous, client = claimed
                async def persist_progress(offset, count, summary):
                    await self._work(self.progress, job_id, token, offset, count, summary)
                logger.info('Context export started: job_id=%s chars=%s completed_parts=%s', job_id, len(source), count)
                async with asyncio.timeout(EXPORT_JOB_TIMEOUT_SECONDS):
                    summary = await summarize_export(
                        client, source, language, budget, start=offset,
                        previous=previous, completed_parts=count,
                        on_progress=persist_progress,
                    )
                await self._work(self.finish, job_id, token, result=render_export(detail, summary, language, created))
                logger.info('Context export completed: job_id=%s', job_id)
            except asyncio.CancelledError:
                await self._work(self.requeue, job_id, token)
                raise
            except (ExportLeaseLost, LookupError):
                pass
            except Exception as exc:
                code = ('assistant_export_timeout' if isinstance(exc, TimeoutError) else
                        exc.code if isinstance(exc, ContextExportError) else classify_ai_service_error(exc).code)
                logger.warning('Context export failed: job_id=%s code=%s', job_id, code)
                await self._work(self.finish, job_id, token, error_code=code)


context_export_runner = ContextExportRunner()
