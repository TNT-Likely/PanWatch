"""Production composition with scripted provider responses, explicitly NOT live-model QA."""
import asyncio
import json
from datetime import UTC, datetime, timedelta

from pan_agent import ModelMessage, RunRequest, RunStatus, RunLimits
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.modules.assistant import tools
from src.modules.assistant.repository import AssistantRepository
from src.modules.assistant.service import AssistantService
from src.modules.assistant.task_runner import DurableRuntimeEventSink
from src.modules.assistant.watch_request import inspect_watch_request
from src.platform.persistence.database import Base
from src.platform.persistence.models import NotifyChannel, PriceAlertRule, Stock


class ScriptedProvider:
    model = "scripted-contract-model"

    def __init__(self, turns):
        self.turns = iter(turns)
        self.exposures = []

    async def chat_stream(self, messages, **kwargs):
        self.exposures.append({t['function']['name'] for t in kwargs.get('tools',[])})
        name, arguments = next(self.turns)
        if name:
            assert name in self.exposures[-1], f"{name} was not actually discovered"
        yield "message", {"content": "根据实际工具结果完成。" if name is None else "", "model": self.model, "tool_calls": [{"id":f"c-{len(self.exposures)}", "name":name,"arguments":json.dumps(arguments)}] if name else [], "usage":{"prompt_tokens":100,"completion_tokens":20}}


def setup():
    engine = create_engine("sqlite://",connect_args={"check_same_thread":False},poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    session.add_all([Stock(symbol="600519",name="QA stock",market="CN"), NotifyChannel(name="QA 飞书",type="feishu",enabled=True)])
    session.commit()
    repository = AssistantRepository(session)
    conversation = repository.create_conversation(stock_symbol="600519",stock_market="CN",initial_context=None)
    task = repository.create_task(conversation_id=conversation.id,user_message_id=None,context={})
    repository.claim_task(task.id)
    return engine,session,AssistantService(repository),task


def test_production_runtime_discovery_approval_db_readback_and_single_consumption(monkeypatch):
    engine, db, service, task = setup()
    monkeypatch.setattr(tools, 'md_quote_rows', lambda *_:[{"symbol":"600519","market":"CN","name":"QA stock","current_price":100}])
    text = "CN:600519 价格突破200且量比>2，两周有效，发飞书"
    plan = inspect_watch_request(text)
    args = {"symbol":"600519","market":"CN","condition_group":{"op":"and","items":[{"type":"price","op":">","value":200},{"type":"volume_ratio","op":">","value":2}]},"expire_at":plan["horizon"]["expire_at"],"notify_channel_ids":[1],"cooldown_minutes":0}
    client = ScriptedProvider([('check_watch_request',{}),('tool_search',{"query":"get_notification_channels"}),('get_notification_channels',{}),('tool_search',{"query":"create_price_alert"}),('create_price_alert',args),(None,{})])
    runtime = service.build_runtime(client)
    req = RunRequest(run_id=str(task.id),messages=[ModelMessage(role="user",content=text)],context={"watch_request":plan},limits=RunLimits(max_steps=12))
    sink = DurableRuntimeEventSink(service,task.id)
    async def execute():
        paused = await runtime.run(req,sink)
        assert paused.status is RunStatus.WAITING_FOR_APPROVAL, (paused.error_code, client.exposures)
        assert db.query(PriceAlertRule).count() == 0
        approvals = service.pause_task(task.id,paused)
        assert "量比" in approvals[0].presentation['summary'] and "QA 飞书" in approvals[0].presentation['summary']
        from pan_agent import ApprovalDecision
        resolution = service.resolve_approval_decision(approvals[0].id,ApprovalDecision.APPROVED)
        finished = await runtime.resume(req,resolution.checkpoint,resolution.decisions,sink)
        assert finished.status is RunStatus.COMPLETED
        await sink.flush()
        return approvals[0]
    approval = asyncio.run(execute())
    assert db.query(PriceAlertRule).one().condition_group == args['condition_group']
    assert db.query(PriceAlertRule).one().cooldown_minutes == 0
    records = service._repository.list_task_tool_invocations(task.id)
    assert any(r.tool_name == 'tool_search' for r in records)
    created = next(r for r in records if r.tool_name == 'create_price_alert')
    assert created.result_data['notify_channel_ids'] == [1]
    from src.modules.assistant.service import AssistantApprovalConflictError
    from pan_agent import ApprovalDecision
    import pytest
    with pytest.raises(AssistantApprovalConflictError):
        service.resolve_approval_decision(approval.id,ApprovalDecision.APPROVED)
    assert db.query(PriceAlertRule).count() == 1
    db.close();engine.dispose()


def test_production_policy_blocks_semantic_downgrade_before_approval():
    engine,db,service,task=setup()
    text="CN:600519 收盘站上20日均线时提醒我"
    client=ScriptedProvider([('tool_search',{"query":"create_price_alert"}),('create_price_alert',{"symbol":"600519","direction":"above","target_price":200}),(None,{})])
    req=RunRequest(run_id=str(task.id),messages=[ModelMessage(role='user',content=text)],context={"watch_request":inspect_watch_request(text)})
    result=asyncio.run(service.build_runtime(client).run(req,DurableRuntimeEventSink(service,task.id)))
    assert not result.pending_approvals
    assert db.query(PriceAlertRule).count()==0
    assert result.status is RunStatus.COMPLETED, (result.error_code, client.exposures)
    events=service._repository.list_task_events(task.id)
    assert any(e.data.get('error_code') == 'permission_denied' for e in events)
    db.close();engine.dispose()


def test_retry_policy_blocks_model_substitution_using_host_resolved_history():
    engine, db, service, _ = setup()
    original = "帮我设置 CN:600519 收盘站上20日均线且连续3天满足时提醒，不接受盘中替代。"
    message = service.record_user_message(1, original)
    service.create_task(1, message.id)
    retry = service.record_user_message(1, "再试一下")
    task = service.create_task(1, retry.id)
    client = ScriptedProvider([('check_watch_request', {}), ('tool_search', {"query":"create_price_alert"}), ('create_price_alert', {"symbol":"600519", "direction":"above", "target_price":200}), (None, {})])
    # Compressed model context may contain only the retry; host plan is intact.
    req = RunRequest(run_id=str(task.id), messages=[ModelMessage(role='user', content='再试一下')], context=task.context)
    result = asyncio.run(service.build_runtime(client).run(req, DurableRuntimeEventSink(service, task.id)))
    assert result.status is RunStatus.COMPLETED and not result.pending_approvals
    assert db.query(PriceAlertRule).count() == 0
    calls = service._repository.list_task_tool_invocations(task.id)
    checked = next(c for c in calls if c.tool_name == 'check_watch_request')
    assert checked.result_data['original_text'] == original
    assert set(checked.result_data['unsupported_conditions']) == {'bar_close_confirmation','moving_average_trigger','consecutive_sessions'}
    assert any(e.data.get('error_code') == 'permission_denied' for e in service._repository.list_task_events(task.id))
    db.close(); engine.dispose()


def test_older_retry_approval_is_rechecked_before_execution():
    from pan_agent import ApprovalDecision
    engine, db, service, _ = setup()
    message = service.record_user_message(1, "CN:600519 收盘站上20日均线且连续3天满足时提醒")
    service.create_task(1, message.id)
    retry = service.record_user_message(1, "再试一下")
    old_plan = inspect_watch_request("再试一下")
    task = service._repository.create_task(conversation_id=1, user_message_id=retry.id, context={"watch_request":old_plan})
    client = ScriptedProvider([('tool_search', {"query":"create_price_alert"}), ('create_price_alert', {"symbol":"600519", "direction":"above", "target_price":999999}), (None, {})])
    runtime = service.build_runtime(client)
    req = RunRequest(run_id=str(task.id), messages=[ModelMessage(role='user', content='再试一下')], context=task.context)
    sink = DurableRuntimeEventSink(service, task.id)

    async def execute():
        paused = await runtime.run(req, sink)
        assert paused.status is RunStatus.WAITING_FOR_APPROVAL
        approvals = service.pause_task(task.id, paused)
        resolution = service.resolve_approval_decision(approvals[0].id, ApprovalDecision.APPROVED)
        req.context['watch_request'] = service.refresh_legacy_watch_retry(task, old_plan)
        finished = await runtime.resume(req, resolution.checkpoint, resolution.decisions, sink)
        await sink.flush()
        assert finished.status is RunStatus.PARTIAL
        assert finished.error_code == 'unsupported_watch_request'

    asyncio.run(execute())
    assert db.query(PriceAlertRule).count() == 0
    assert any(e.data.get('error_code') == 'unsupported_watch_request' for e in service._repository.list_task_events(task.id))
    db.close(); engine.dispose()


def test_diagnosis_virtual_tool_survives_later_active_research_exposure():
    engine,db,service,task=setup()
    client=ScriptedProvider([(None,{})])
    req=RunRequest(run_id=str(task.id),messages=[ModelMessage(role='user',content='全面诊断我的持仓')])
    asyncio.run(service.build_runtime(client).run(req,DurableRuntimeEventSink(service,task.id)))
    assert 'portfolio_diagnosis' in client.exposures[0]
    db.close();engine.dispose()
