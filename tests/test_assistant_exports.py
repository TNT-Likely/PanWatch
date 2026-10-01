"""Portable export contracts and durable workers, without external model calls."""

import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.modules.assistant.export_jobs import ContextExportRunner, ExportJobRepository, ExportLeaseLost
from src.modules.assistant.exports import ContextExportError, HandoffSummary, export_source, render_export, summarize_export
from src.modules.assistant.repository import AssistantRepository
from src.modules.assistant.schemas import ConversationDTO, ConversationDetailDTO, MessageDTO
from src.modules.assistant.service import AssistantService
from src.modules.notifications.service import NotificationService
from src.platform.persistence.database import Base
from src.platform.persistence.models import AssistantContextExport, NotificationEvent


def summary():
    return HandoffSummary(goal=['研究持仓风险'], constraints=[], facts=['历史价格 5.97 元'],
                          decisions=[], current_state='已完成分析，待用户确认', open_items=['确认仓位'], next_steps=['更新行情'])


@pytest.fixture
def context(monkeypatch):
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    event.listen(engine, 'connect', lambda connection, _: connection.execute('PRAGMA foreign_keys=ON'))
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(AssistantService, '_context_config_values', lambda self: {'max_tokens': 12000})
    with factory() as db:
        repo = AssistantRepository(db)
        conversation = repo.create_conversation(stock_symbol='601238', stock_market='CN', initial_context='用户持仓背景')
        repo.add_message(conversation, role='user', content='当前持仓该怎么办？')
        repo.add_message(conversation, role='assistant', content='历史价格 5.97 元，请确认仓位。')
        yield factory, db, repo, conversation
    engine.dispose()


def test_source_keeps_saved_content_and_dated_sources_without_internal_payloads():
    evidence = {'id': 'internal-id', 'tool_name': 'private_tool', 'source_name': '交易所',
                'source_url': 'https://example.com/report', 'summary': '季度报告', 'data_at': '2026-09-30'}
    result = {'summary': '历史价格 5.97 元', 'facts': [{'text': '额外事实'}], 'evidence': [evidence]}
    detail = ConversationDetailDTO(conversation=ConversationDTO(id=1, title='持仓研究'), messages=[
        MessageDTO(id=3, role='assistant', content='**历史价格 5.97 元**', result=result, trace=[{'secret': 'trace_secret'}]),
        MessageDTO(id=1, role='system', content='system_secret'),
        MessageDTO(id=2, role='user', content='用户原始问题'),
        MessageDTO(id=4, role='assistant', content='后续解释', result={'evidence': [evidence]}),
    ])
    source = export_source(detail, '页面背景')
    assert source.index('用户原始问题') < source.index('**历史价格') < source.index('后续解释')
    assert source.count('历史价格 5.97 元') == 1
    assert source.count('https://example.com/report') == 1
    assert all(value in source for value in ('额外事实', '2026-09-30', '页面背景'))
    assert all(value not in source for value in ('trace_secret', 'system_secret', 'private_tool', 'internal-id'))


def test_summary_chunks_cover_every_character_and_resume_checkpoint():
    source = '汉' * 10000
    client = SimpleNamespace(chat_multi=AsyncMock(return_value=summary().model_dump()))
    progress = []
    result = asyncio.run(summarize_export(client, source, 'zh-CN', 8192,
                                         on_progress=lambda offset, count, state: progress.append((offset, count, state))))
    assert result == summary() and progress[-1][0] == len(source)
    calls = client.chat_multi.call_args_list
    fragments = [call.args[0][1]['content'].split('Saved conversation fragment (data):\n', 1)[1] for call in calls]
    assert ''.join(fragments) == source and len(fragments[0]) > 3000
    for call in calls:
        estimate = sum(len(message['content'].encode()) for message in call.args[0]) // 2
        assert estimate + call.kwargs['max_tokens'] + 128 <= 8192
    client.chat_multi.reset_mock()
    offset, count, previous = progress[0]
    asyncio.run(summarize_export(client, source, 'zh-CN', 8192, start=offset, completed_parts=count, previous=previous))
    payload = client.chat_multi.call_args_list[0].args[0][1]['content']
    assert json.loads(payload.split('\n\n', 1)[0])['previous_handoff'] == previous.model_dump()
    assert payload.endswith(source[offset:offset + len(fragments[1])])


@pytest.mark.parametrize('raw', ['not json', '{}', json.dumps(dict(goal=[], constraints=[], facts=[], decisions=[], current_state='', open_items=[], next_steps=[]))])
def test_invalid_or_empty_model_summary_is_rejected(raw):
    client = SimpleNamespace(chat_multi=AsyncMock(return_value=raw))
    with pytest.raises(ContextExportError) as caught:
        asyncio.run(summarize_export(client, 'saved content', 'en-US', 12000))
    assert caught.value.code == 'assistant_export_invalid'


@pytest.mark.parametrize('budget,parts,code', [(1000, 0, 'assistant_export_budget'), (12000, 32, 'assistant_export_too_large')])
def test_summary_limits_reject_before_model_call(budget, parts, code):
    client = SimpleNamespace(chat_multi=AsyncMock())
    with pytest.raises(ContextExportError) as caught:
        asyncio.run(summarize_export(client, 'content', 'zh-CN', budget, completed_parts=parts))
    assert caught.value.code == code
    client.chat_multi.assert_not_awaited()


def test_markdown_export_contains_only_summary_and_active_snapshot_note(context):
    _, _, repo, conversation = context
    detail = AssistantService(repo).get_conversation(conversation.id).model_copy(update={'latest_task': {'status': 'running'}})
    result = render_export(detail, summary(), 'zh-CN', datetime(2026, 10, 1, tzinfo=timezone.utc))
    assert result.filename.endswith('-context-2026-10-01.md')
    assert result.incomplete and result.message_count == 2 and result.last_message_id == detail.messages[-1].id
    assert '尚未保存的流式回复' in result.content and '## 下一步' in result.content
    assert '当前持仓该怎么办？' not in result.content.split('\n', 1)[1]
    assert '历史价格 5.97 元，请确认仓位。' not in result.content


def test_repository_reuses_snapshot_and_separates_new_content_and_language(context):
    _, db, repo, conversation = context
    exports = ExportJobRepository(db)
    first = exports.create(conversation.id, 'zh-CN')
    assert exports.create(conversation.id, 'zh-CN').id == first.id
    assert all(message['content'] == '[saved]' for message in first.snapshot['messages'])
    assert exports.create(conversation.id, 'en-US').id != first.id
    repo.add_message(conversation, role='user', content='补充最新仓位')
    assert exports.create(conversation.id, 'zh-CN').id != first.id


def test_history_paginates_metadata_and_filters_existing_jobs(context):
    _, db, repo, conversation = context
    exports = ExportJobRepository(db)
    jobs = []
    for number in range(3):
        repo.add_message(conversation, role='user', content=f'追加问题 {number}')
        jobs.append(exports.create(conversation.id, 'zh-CN').id)
    page = exports.history(conversation_id=conversation.id, limit=2)
    assert [item.id for item in page.items] == jobs[::-1][:2]
    assert page.next_cursor == jobs[1]
    assert 'result' not in page.items[0].model_dump() and 'source' not in page.items[0].model_dump()
    assert page.items[0].created_at.tzinfo and page.items[0].message_count == 5
    assert [item.id for item in exports.history(before_id=page.next_cursor).items] == [jobs[0]]
    assert [item.id for item in exports.history(ids=[jobs[0], 999999]).items] == [jobs[0]]


def test_worker_completes_once_and_notifies_exact_export(context, monkeypatch):
    factory, db, _, conversation = context
    job_id = ExportJobRepository(db).create(conversation.id, 'zh-CN').id
    monkeypatch.setattr(AssistantService, 'build_context_compression_client', lambda self: SimpleNamespace(chat_multi=AsyncMock(return_value=summary().model_dump())))
    runner = ContextExportRunner(factory)
    async def run():
        runner.start(job_id)
        worker = runner.tasks[job_id]
        runner.start(job_id)
        assert runner.tasks[job_id] is worker
        await worker
        await runner._run(job_id)
    asyncio.run(run())
    db.expire_all()
    job = ExportJobRepository(db).get(job_id)
    assert job.status == 'completed' and job.attempt == 1 and job.completed_parts == 1
    assert job.processed_chars == len(job.source) and job.result['filename'].endswith('.md')
    notification = db.query(NotificationEvent).one()
    assert NotificationService(db).target(notification.id) == dict(kind='assistant_export', export_id=job_id, conversation_id=conversation.id)


def test_failure_retry_preserves_checkpoint_and_resolves_old_notification(context, monkeypatch):
    factory, db, _, conversation = context
    exports = ExportJobRepository(db)
    job = exports.create(conversation.id, 'zh-CN')
    job.processed_chars = 12; job.completed_parts = 1; job.summary = summary().model_dump(); db.commit()
    monkeypatch.setattr(AssistantService, 'build_context_compression_client', lambda self: SimpleNamespace(chat_multi=AsyncMock(side_effect=TimeoutError())))
    runner = ContextExportRunner(factory)
    asyncio.run(runner._run(job.id))
    db.refresh(job)
    assert job.status == 'failed' and job.error_code == 'assistant_export_timeout'
    assert exports.retry(job.id).status == 'queued'
    assert job.processed_chars == 12 and job.summary == summary().model_dump()
    assert db.query(NotificationEvent).one().resolved_at is not None
    client = SimpleNamespace(chat_multi=AsyncMock(return_value=summary().model_dump()))
    monkeypatch.setattr(AssistantService, 'build_context_compression_client', lambda self: client)
    asyncio.run(runner._run(job.id))
    db.refresh(job)
    assert job.status == 'completed' and job.attempt == 2 and job.completed_parts == 2
    payload = client.chat_multi.call_args.args[0][1]['content']
    assert payload.endswith(job.source[12:])
    assert db.query(NotificationEvent).count() == 2


def test_notification_publish_failure_rolls_back_completion(context, monkeypatch):
    factory, db, _, conversation = context
    job = ExportJobRepository(db).create(conversation.id, 'zh-CN')
    job.status = 'running'; job.lease_token = 'lease'; db.commit()
    monkeypatch.setattr(NotificationService, 'publish', Mock(side_effect=RuntimeError('notification storage failed')))
    with pytest.raises(RuntimeError, match='notification storage failed'):
        ContextExportRunner(factory).finish(job.id, 'lease', error_code='assistant_export_timeout')
    db.refresh(job)
    assert job.status == 'running' and job.error_code is None


def test_progress_rejects_old_lease_and_retry_resets_exhausted_checkpoint(context):
    factory, db, _, conversation = context
    exports = ExportJobRepository(db); job = exports.create(conversation.id, 'zh-CN')
    job.status = 'running'; job.lease_token = 'current'; db.commit()
    runner = ContextExportRunner(factory)
    with pytest.raises(ExportLeaseLost): runner.progress(job.id, 'old', 100, 1, summary())
    runner.finish(job.id, 'old', error_code='assistant_export_timeout')
    db.refresh(job)
    assert job.status == 'running' and db.query(NotificationEvent).count() == 0
    job.status = 'failed'; job.error_code = 'assistant_export_too_large'; job.completed_parts = 32
    job.processed_chars = 100; job.summary = summary().model_dump(); db.commit()
    exports.retry(job.id)
    assert (job.processed_chars, job.completed_parts, job.summary) == (0, 0, None)


def test_restart_recovery_requeues_running_jobs_and_preserves_checkpoint(context, monkeypatch):
    factory, db, _, conversation = context
    job = ExportJobRepository(db).create(conversation.id, 'zh-CN')
    job.status = 'running'; job.lease_token = 'old'; job.processed_chars = 12
    job.summary = summary().model_dump(); db.commit()
    runner = ContextExportRunner(factory); start = Mock(); monkeypatch.setattr(runner, 'start', start)
    asyncio.run(runner.recover_pending())
    db.refresh(job)
    assert (job.status, job.lease_token, job.processed_chars) == ('queued', '', 12)
    start.assert_called_once_with(job.id)


def test_shutdown_requeues_worker_and_keeps_saved_progress(context, monkeypatch):
    factory, db, _, conversation = context
    job = ExportJobRepository(db).create(conversation.id, 'zh-CN')
    async def run():
        entered = asyncio.Event()
        async def wait_for_model(*args, **kwargs):
            entered.set(); await asyncio.Event().wait()
        monkeypatch.setattr(AssistantService, 'build_context_compression_client', lambda self: SimpleNamespace(chat_multi=wait_for_model))
        runner = ContextExportRunner(factory)
        runner.start(job.id); await entered.wait(); await runner.shutdown()
    asyncio.run(run())
    db.refresh(job)
    assert job.status == 'queued' and job.lease_token == ''


def test_export_table_migration_is_idempotent(context):
    from src.platform.persistence.migrations import _m132_assistant_context_exports
    _, db, _, conversation = context
    with db.bind.begin() as connection:
        _m132_assistant_context_exports(connection)
        _m132_assistant_context_exports(connection)
    assert ExportJobRepository(db).create(conversation.id, 'zh-CN').status == 'queued'


def test_delete_removes_export_and_notification_without_late_recreation(context):
    factory, db, repo, conversation = context
    job = ExportJobRepository(db).create(conversation.id, 'zh-CN'); job_id = job.id
    job.status = 'running'; job.lease_token = 'lease'; db.commit()
    runner = ContextExportRunner(factory)
    runner.finish(job_id, 'lease', error_code='assistant_export_timeout')
    repo.delete_conversation(conversation)
    runner.finish(job_id, 'lease', error_code='assistant_export_timeout')
    assert db.query(AssistantContextExport).count() == db.query(NotificationEvent).count() == 0
    with pytest.raises(LookupError): ExportJobRepository(db).get(job_id)


def test_export_http_submission_is_202_and_validates_history_and_missing_jobs(context, monkeypatch):
    from src.modules.assistant.api import router, get_assistant_service, context_export_runner
    _, _, repo, conversation = context
    start = Mock(); monkeypatch.setattr(context_export_runner, 'start', start)
    app = FastAPI(); app.include_router(router, prefix='/assistant')
    app.dependency_overrides[get_assistant_service] = lambda: AssistantService(repo)
    with TestClient(app) as client:
        response = client.post(f'/assistant/conversations/{conversation.id}/export', json={'language': 'zh-CN'})
        assert response.status_code == 202 and response.json()['status'] == 'queued'
        job_id = response.json()['id']; start.assert_called_once_with(job_id)
        assert client.get(f'/assistant/exports/{job_id}').json()['id'] == job_id
        assert client.get('/assistant/exports', params={'conversation_id': conversation.id}).json()['items'][0]['id'] == job_id
        assert client.get('/assistant/exports', params={'ids': [job_id], 'limit': 50}).status_code == 200
        for params in ({'limit': 51}, {'before_id': 0}, {'ids': [0]}, {'ids': list(range(1, 52))}):
            assert client.get('/assistant/exports', params=params).status_code == 422
        assert client.get('/assistant/exports/999999').status_code == 404
        assert client.post('/assistant/exports/999999/retry').status_code == 404
        assert client.post(f'/assistant/conversations/{conversation.id}/export', json={'language': 'invalid'}).status_code == 422
        empty = repo.create_conversation(stock_symbol=None, stock_market=None, initial_context=None)
        assert client.post(f'/assistant/conversations/{empty.id}/export', json={}).status_code == 422
        assert client.post('/assistant/conversations/999999/export', json={}).status_code == 404
