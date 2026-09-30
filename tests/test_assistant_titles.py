"""Conversation naming is optional and cannot overwrite a user's title."""
import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.platform.persistence.database import Base
from src.platform.persistence.models import ChatConversation
from src.platform.persistence.migrations import _m131_assistant_conversation_titles
from src.modules.assistant.repository import AssistantRepository
from src.modules.assistant.service import AssistantService
from src.modules.assistant.schemas import RenameConversationCommand
from src.modules.assistant.titles import summarize_title


@pytest.fixture
def context():
    engine = create_engine('sqlite://', connect_args={'check_same_thread': False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    with factory() as db:
        repo = AssistantRepository(db)
        conv = repo.create_conversation(stock_symbol=None, stock_market=None, initial_context=None)
        repo.add_message(conv, role='user', content='当前持仓该继续持有还是考虑减仓？')
        repo.add_message(conv, role='assistant', content='根据仓位和风险，分情况说明。')
        yield factory, db, repo, conv
    engine.dispose()


def test_automatic_title_is_generated_once_and_uses_bounded_context(context, monkeypatch):
    _, db, repo, conv = context
    client = SimpleNamespace(chat_multi=AsyncMock(return_value='{"title":"持仓风险与减仓策略"}'))
    monkeypatch.setattr(AssistantService, 'build_context_compression_client', lambda self: client)
    service = AssistantService(repo)
    assert asyncio.run(service.generate_conversation_title(conv.id))
    db.refresh(conv)
    assert conv.title == '持仓风险与减仓策略' and conv.title_source == 'automatic'
    assert not asyncio.run(service.generate_conversation_title(conv.id))
    assert client.chat_multi.await_count == 1
    assert client.chat_multi.call_args.kwargs['max_tokens'] == 100
    assert 'question' in client.chat_multi.call_args.args[0][1]['content']


def test_manual_rename_is_persisted_and_skips_model(context, monkeypatch):
    _, db, repo, conv = context
    client = SimpleNamespace(chat_multi=AsyncMock())
    monkeypatch.setattr(AssistantService, 'build_context_compression_client', lambda self: client)
    service = AssistantService(repo)
    updated = service.rename_conversation(conv.id, RenameConversationCommand(title='  我的   持仓计划  '))
    assert updated.title == '我的 持仓计划' and updated.title_source == 'manual'
    assert not asyncio.run(service.generate_conversation_title(conv.id))
    client.chat_multi.assert_not_awaited()
    repo.add_message(conv, role='user', content='继续分析')
    db.refresh(conv)
    assert conv.title == '我的 持仓计划'


def test_notifications_use_current_conversation_title(context):
    from src.modules.notifications.service import NotificationService
    _, db, repo, conv = context
    task = repo.create_task(conversation_id=conv.id, user_message_id=None, context={})
    repo.claim_task(task.id)
    repo.complete_task_with_message(task.id, conv.id, '完成')
    repo.rename_conversation(conv, '重命名后的会话')
    assert NotificationService(db).list()['items'][0]['title'] == '重命名后的会话'


def test_manual_rename_during_generation_wins(context, monkeypatch):
    factory, db, repo, conv = context
    conv_id = conv.id
    async def response(*args, **kwargs):
        with factory() as other:
            AssistantService(AssistantRepository(other)).rename_conversation(conv_id, RenameConversationCommand(title='用户设置的名字'))
        return '{"title":"自动总结"}'
    monkeypatch.setattr(AssistantService, 'build_context_compression_client', lambda self: SimpleNamespace(chat_multi=response))
    assert not asyncio.run(AssistantService(repo).generate_conversation_title(conv_id))
    db.refresh(conv)
    assert conv.title == '用户设置的名字' and conv.title_source == 'manual'


def test_deleted_conversation_is_not_recreated_by_title(context, monkeypatch):
    factory, db, repo, conv = context
    conv_id = conv.id
    async def response(*args, **kwargs):
        with factory() as other:
            other.query(ChatConversation).filter_by(id=conv_id).delete(); other.commit()
        return '{"title":"自动总结"}'
    monkeypatch.setattr(AssistantService, 'build_context_compression_client', lambda self: SimpleNamespace(chat_multi=response))
    assert not asyncio.run(AssistantService(repo).generate_conversation_title(conv_id))
    assert db.query(ChatConversation).count() == 0


@pytest.mark.parametrize('raw', ['not json', '{"title":""}', '{"title":42}', '{"title":"' + 'x' * 81 + '"}', '[]'])
def test_bad_model_title_preserves_fallback(context, monkeypatch, raw):
    _, db, repo, conv = context
    original = conv.title
    monkeypatch.setattr(AssistantService, 'build_context_compression_client', lambda self: SimpleNamespace(chat_multi=AsyncMock(return_value=raw)))
    assert not asyncio.run(AssistantService(repo).generate_conversation_title(conv.id))
    db.refresh(conv)
    assert conv.title == original and conv.title_source == 'provisional'


def test_title_model_failure_does_not_change_answer(context, monkeypatch):
    from src.modules.assistant.task_runner import AssistantTaskRunner
    factory, db, repo, conv = context
    original = conv.title
    monkeypatch.setattr(AssistantService, 'build_context_compression_client', lambda self: SimpleNamespace(chat_multi=AsyncMock(side_effect=RuntimeError('offline'))))
    asyncio.run(AssistantTaskRunner(factory)._generate_title(conv.id))
    db.refresh(conv)
    assert conv.title == original
    assert repo.list_messages(conv.id)[-1].content == '根据仓位和风险，分情况说明。'


def test_title_summary_truncates_input_and_accepts_json_fence():
    client = SimpleNamespace(chat_multi=AsyncMock(return_value='```json\n{"title":"Portfolio risk review"}\n```'))
    assert asyncio.run(summarize_title(client, 'a' * 4000, 'b' * 8000)) == 'Portfolio risk review'
    assert len(client.chat_multi.call_args.args[0][1]['content']) < 4200


def test_rename_http_validation_and_missing_conversation(context):
    from src.modules.assistant.api import router, get_assistant_service
    _, _, repo, conv = context
    app = FastAPI(); app.include_router(router, prefix='/assistant')
    app.dependency_overrides[get_assistant_service] = lambda: AssistantService(repo)
    with TestClient(app) as client:
        response = client.patch(f'/assistant/conversations/{conv.id}', json={'title': '新标题'})
        assert response.status_code == 200 and response.json()['title_source'] == 'manual'
        for title in ('', '   ', 'x' * 81):
            assert client.patch(f'/assistant/conversations/{conv.id}', json={'title': title}).status_code == 422
        assert client.patch('/assistant/conversations/999999', json={'title': '不存在'}).status_code == 404


def test_title_migration_preserves_legacy_title_and_is_idempotent():
    engine = create_engine('sqlite://')
    with engine.begin() as conn:
        conn.execute(text('CREATE TABLE chat_conversations (id INTEGER PRIMARY KEY, title TEXT)'))
        conn.execute(text("INSERT INTO chat_conversations VALUES (1, '旧标题')"))
        _m131_assistant_conversation_titles(conn); _m131_assistant_conversation_titles(conn)
        assert conn.execute(text('SELECT title, title_source FROM chat_conversations')).one() == ('旧标题', 'legacy')
    engine.dispose()
