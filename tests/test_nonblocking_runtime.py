"""Regression checks for slow providers, SQLite contention, and scoped prompts."""
import asyncio
import logging
import threading
from datetime import date

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from src.platform.persistence.database import Base
from src.platform.persistence.models import AnalysisHistory
from src.platform.marketdata.models import MarketCode
from src.platform.runtime.config import StockConfig, AppConfig, Settings


async def while_blocked(coroutine, entered, release):
    task = asyncio.create_task(coroutine)
    try:
        assert await asyncio.to_thread(entered.wait, 2), 'worker was not entered'
        await asyncio.sleep(0)
        # A synchronous call on the event loop would have returned only after
        # the provider's timeout; unrelated work must run while it is blocked.
        assert not task.done(), 'slow work blocked the event loop'
    finally:
        release.set()
    return await task


def blocking_result(entered, release, value):
    def call(*args, **kwargs):
        entered.set()
        release.wait(2)
        return value
    return call


def test_datasource_kline_does_not_block_unrelated_work(monkeypatch):
    from src.modules.market.data_collector import DataCollectorManager
    from src.platform.marketdata.collectors.kline_collector import KlineCollector
    entered, release = threading.Event(), threading.Event()
    monkeypatch.setattr(KlineCollector, 'get_kline_summary', blocking_result(entered, release, {'last_close': 5.84}))
    result = asyncio.run(while_blocked(DataCollectorManager().collect_kline('601238'), entered, release))
    assert result.success and result.data['last_close'] == 5.84


def test_context_builder_runs_blocking_work_in_worker(monkeypatch):
    from src.modules.research.context_builder import ContextBuilder
    entered, release = threading.Event(), threading.Event()
    builder = ContextBuilder()
    monkeypatch.setattr(builder, '_build_symbol_contexts', blocking_result(entered, release, {'symbols': {}}))
    result = asyncio.run(while_blocked(builder.build_symbol_contexts(agent_name='test', context=None, packs={}), entered, release))
    assert result == {'symbols': {}}


def test_signal_pack_technical_provider_does_not_block_loop(monkeypatch):
    from src.modules.research.signals.signal_pack import SignalPackBuilder
    from src.platform.marketdata.collectors.kline_collector import KlineCollector
    entered, release = threading.Event(), threading.Event()
    builder = SignalPackBuilder()
    monkeypatch.setattr(builder, '_source_policy', lambda kind, **kw: ([('tencent', {})], kind != 'kline'))
    monkeypatch.setattr(KlineCollector, 'get_kline_summary', blocking_result(entered, release, {'last_close': 5.84}))
    result = asyncio.run(while_blocked(builder.build_for_symbols(
        symbols=[('601238', MarketCode.CN, '广汽集团')], include_news=False, news_hours=12, portfolio=None,
    ), entered, release))
    assert result['601238'].technical['last_close'] == 5.84


def test_error_logging_and_stats_stay_responsive_while_db_is_blocked(monkeypatch):
    from src.platform.observability import log_handler as module
    entered, release = threading.Event(), threading.Event()
    saved = []
    class Database:
        def bulk_insert_mappings(self, model, rows):
            entered.set(); release.wait(2); saved.extend(rows)
        def commit(self): pass
        def close(self): pass
    monkeypatch.setattr(module, 'SessionLocal', Database)
    monkeypatch.setattr(module, '_ACTIVE_HANDLER', None)
    handler = module.DBLogHandler()
    try:
        handler.emit(logging.LogRecord('test', logging.ERROR, '', 0, 'first', (), None))
        assert entered.wait(1)
        handler.emit(logging.LogRecord('test', logging.INFO, '', 0, 'second', (), None))
        assert module.get_log_handler_stats()['pending_entries'] == 1
    finally:
        release.set(); handler.close()
    assert [row['message'] for row in saved] == ['first', 'second']


def test_retention_failure_never_reinserts_committed_logs(monkeypatch):
    from src.platform.observability import log_handler as module
    saved = []
    class Database:
        def bulk_insert_mappings(self, model, rows): saved.extend(rows)
        def commit(self): pass
        def close(self): pass
    monkeypatch.setattr(module, 'SessionLocal', Database)
    monkeypatch.setattr(module, '_ACTIVE_HANDLER', None)
    handler = module.DBLogHandler()
    handler._flush_count = module.CLEANUP_EVERY_FLUSHES - 1
    monkeypatch.setattr(handler, '_cleanup', lambda db: (_ for _ in ()).throw(RuntimeError('cleanup locked')))
    handler.emit(logging.LogRecord('test', logging.INFO, '', 0, 'once', (), None))
    handler.close()
    assert len(saved) == 1 and not handler._buffer and handler._flush_errors == 1


def test_assistant_waiting_for_sqlite_write_does_not_block_loop(tmp_path):
    from src.modules.assistant.task_runner import AssistantTaskRunner
    engine = create_engine(f"sqlite:///{tmp_path / 'writer.db'}", connect_args={'timeout': 2})
    factory = sessionmaker(bind=engine)
    with engine.begin() as db:
        db.execute(text('CREATE TABLE marker (id INTEGER PRIMARY KEY)'))
    holder = engine.connect()
    holder.exec_driver_sql('BEGIN IMMEDIATE')
    entered = threading.Event()
    runner = AssistantTaskRunner(session_factory=factory)
    def persist(service):
        entered.set()
        service._repository.session.execute(text('INSERT INTO marker VALUES (1)'))
        service._repository.session.commit()
    async def run():
        pending = asyncio.create_task(runner._write(persist))
        assert await asyncio.to_thread(entered.wait, 1)
        await asyncio.sleep(0.02)
        assert not pending.done()
        pending.cancel()
        await asyncio.sleep(0)
        assert not pending.done(), 'cancellation must wait for the transaction before cleanup'
        holder.rollback()
        with pytest.raises(asyncio.CancelledError): await pending
    try:
        asyncio.run(run())
        with engine.connect() as db: assert db.execute(text('SELECT COUNT(*) FROM marker')).scalar() == 1
    finally:
        holder.close(); engine.dispose()


@pytest.mark.parametrize("operation", ["task", "alert"])
def test_assistant_controls_and_tools_keep_loop_responsive_during_db_contention(tmp_path, monkeypatch, operation):
    from pan_agent import ModelMessage, RunRequest
    from src.modules.assistant import api, tools
    from src.modules.assistant.repository import AssistantRepository
    from src.modules.assistant.service import AssistantService
    from src.platform.persistence.models import PriceAlertRule, Stock

    engine = create_engine(f"sqlite:///{tmp_path / 'controls.db'}", connect_args={'timeout': 2})
    Base.metadata.create_all(engine)
    entered = threading.Event()
    started = []
    monkeypatch.setattr(api.assistant_task_runner, 'start_message', lambda *args: started.append(args))
    with sessionmaker(bind=engine)() as db:
        repository = AssistantRepository(db)
        conversation = repository.create_conversation(stock_symbol=None, stock_market=None, initial_context=None)
        conversation_id = conversation.id
        db.add(Stock(symbol='601238', name='广汽集团', market='CN'))
        db.commit()
        service = AssistantService(repository)
        registry = tools.build_panwatch_tool_registry(db)
        if operation == 'task':
            original = AssistantService.record_user_message
            def record(worker, *args):
                assert worker._repository.session is not db
                entered.set()
                return original(worker, *args)
            monkeypatch.setattr(AssistantService, 'record_user_message', record)
            coroutine = api.create_assistant_task(conversation_id, api.SendAssistantMessageCommand(content='分析市场'), service)
        else:
            original = tools.create_alert_rule
            def create(worker_db, **kwargs):
                assert worker_db is not db
                entered.set()
                return original(worker_db, **kwargs)
            monkeypatch.setattr(tools, 'create_alert_rule', create)
            coroutine = registry.execute('create_price_alert', RunRequest(
                run_id='contention', messages=[ModelMessage(role='user', content='创建提醒')],
            ), {'symbol': '601238', 'market': 'CN', 'direction': 'above', 'target_price': 6})

        holder = engine.connect()
        holder.exec_driver_sql('BEGIN IMMEDIATE')
        async def run():
            pending = asyncio.create_task(coroutine)
            try:
                assert await asyncio.to_thread(entered.wait, 1)
                await asyncio.sleep(0.02)
                assert not pending.done(), 'SQLite contention blocked unrelated work'
            finally:
                holder.rollback()
            return await pending
        try:
            result = asyncio.run(run())
            if operation == 'task':
                assert result['status'] == 'queued' and started == [(result['task_id'], conversation_id)]
            else:
                assert result.ok and db.query(PriceAlertRule).count() == 1
        finally:
            holder.close()
    engine.dispose()


def test_price_alert_gate_is_read_only_and_day_reset_is_committed_in_worker(tmp_path, monkeypatch):
    from datetime import datetime, timezone
    from src.modules.market import price_alert_engine as module
    from src.platform.scheduling import trading_calendar
    from src.platform.persistence.models import PriceAlertRule, Stock

    engine = create_engine(f"sqlite:///{tmp_path / 'alerts.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(module, 'SessionLocal', factory)
    monkeypatch.setattr(trading_calendar, 'is_trading_day', lambda *args: True)
    now = datetime(2026, 10, 9, 2, tzinfo=timezone.utc)
    with factory() as db:
        stock = Stock(symbol='601238', name='广汽集团', market='CN')
        db.add(stock)
        db.flush()
        rule = PriceAlertRule(stock_id=stock.id, name='测试提醒', enabled=True,
                              condition_group={'op': 'and', 'items': []},
                              market_hours_mode='always', trigger_date='2026-10-08',
                              trigger_count_today=3, max_triggers_per_day=3, cooldown_minutes=0)
        db.add(rule)
        db.commit()
        engine_service = module.PriceAlertEngine()
        assert engine_service._can_trigger(rule, now) == (True, 'ok')
        assert not db.dirty, 'a read gate must not cause main-thread autoflush or reset a worker count'
        hit_id = engine_service._persist_hit(rule.id, now, {'quote': {'current_price': 6}}, 6)
        assert hit_id is not None
        db.expire_all()
        assert rule.trigger_date == '2026-10-09' and rule.trigger_count_today == 1
    engine.dispose()


@pytest.fixture
def history_db(tmp_path, monkeypatch):
    from src.modules.research import analysis_history
    engine = create_engine(f"sqlite:///{tmp_path / 'history.db'}")
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    monkeypatch.setattr(analysis_history, 'SessionLocal', factory)
    with factory() as db: yield db
    engine.dispose()


def test_history_scope_excludes_other_stocks_and_preserves_report_date(history_db):
    from src.modules.research.analysis_history import get_scoped_analysis_context
    history_db.add(AnalysisHistory(agent_name='daily_report', stock_symbol='*', analysis_date='2026-09-30',
        content='广汽集团。赛力斯今日小涨。', raw_data={'symbols': ['601238', '601127'], 'suggestions': {
            '601238': {'action_label': '观望', 'reason': '广汽集团观察支撑'},
            '601127': {'action_label': '持有', 'reason': '赛力斯修复'},
        }}))
    history_db.commit()
    result = get_scoped_analysis_context('daily_report', [StockConfig('601238','广汽集团',MarketCode.CN)], before_date=date(2026,10,3))
    assert '广汽集团观察支撑' in result.content and '2026-09-30' in result.content
    assert '赛力斯' not in result.content and '601127' not in result.content


def test_legacy_unscoped_report_and_wrong_market_are_not_reused(history_db):
    from src.modules.research.analysis_history import get_scoped_analysis_context
    history_db.add_all([
        AnalysisHistory(agent_name='daily_report', stock_symbol='*', analysis_date='2026-09-30', content='赛力斯历史报告'),
        AnalysisHistory(agent_name='daily_report', stock_symbol='LI', analysis_date='2026-09-30', content='Wrong market', raw_data={'market':'CN'}),
    ])
    history_db.commit()
    assert get_scoped_analysis_context('daily_report', [StockConfig('601238','广汽集团',MarketCode.CN)], before_date=date(2026,10,3)) is None
    assert get_scoped_analysis_context('daily_report', [StockConfig('LI','理想汽车',MarketCode.US)], before_date=date(2026,10,3)) is None


def test_premarket_prompt_ignores_even_supplied_postmarket_report():
    from src.modules.automation.base import AgentContext
    from src.modules.automation.premarket_outlook import PremarketOutlookAgent
    config = AppConfig(settings=Settings(), watchlist=[StockConfig('601238','广汽集团',MarketCode.CN)])
    context = AgentContext(ai_client=None, notifier=None, config=config)
    system, prompt = PremarketOutlookAgent().build_prompt({'yesterday_analysis':'赛力斯（601127）盘后日报'}, context)
    assert '赛力斯' not in prompt and '601127' not in prompt and '盘后分析回顾' not in prompt
    assert '广汽集团' in prompt and '昨日盘后分析' not in system


def test_feedback_migration_drops_only_manual_tables_and_is_idempotent(tmp_path):
    from src.platform.persistence.migrations import _m134_remove_manual_feedback
    engine = create_engine(f"sqlite:///{tmp_path / 'migration.db'}")
    with engine.begin() as db:
        for table in ('suggestion_feedback','entry_candidate_feedback','agent_prediction_outcomes','entry_candidate_outcomes'):
            db.execute(text(f'CREATE TABLE {table} (id INTEGER PRIMARY KEY, value TEXT)'))
            db.execute(text(f"INSERT INTO {table} VALUES (1, 'retained')"))
        db.execute(text('CREATE INDEX old_feedback_idx ON suggestion_feedback(value)'))
        _m134_remove_manual_feedback(db); _m134_remove_manual_feedback(db)
        assert not {'suggestion_feedback','entry_candidate_feedback'} & set(inspect(db).get_table_names())
        for table in ('agent_prediction_outcomes','entry_candidate_outcomes'):
            assert db.execute(text(f'SELECT value FROM {table}')).scalar() == 'retained'
        assert not db.execute(text("SELECT name FROM sqlite_master WHERE name='old_feedback_idx'")).first()
    engine.dispose()


def test_raw_kline_batch_deduplicates_and_fetches_concurrently(monkeypatch):
    from src.modules.market.api import klines
    entered = set()
    lock = threading.Lock()
    both = threading.Event()
    waits = []
    class Collector:
        def __init__(self, market): self.market = market
        def get_klines(self, symbol, days):
            with lock:
                entered.add((self.market.value, symbol, days))
                if len(entered) == 2: both.set()
            waits.append(both.wait(1))
            return []
    monkeypatch.setattr(klines, 'KlineCollector', Collector)
    request = klines.KlineBatchRequest(items=[
        klines.KlineItem(symbol='A', market='US', days=60),
        klines.KlineItem(symbol='B', market='US', days=90),
        klines.KlineItem(symbol='A', market='US', days=60),
    ])
    rows = klines.get_klines_batch(request)
    assert all(waits) and len(entered) == 2
    assert [(row['symbol'], row['days']) for row in rows] == [('A',60),('B',90),('A',60)]


def test_stock_list_fallback_is_single_flight_without_waiting_on_shutdown(monkeypatch):
    from src.platform.marketdata import stock_list
    entered, release = threading.Event(), threading.Event()
    monkeypatch.setattr(stock_list, '_AKSHARE_FUTURE', None)
    monkeypatch.setattr(stock_list, '_fetch_from_akshare', blocking_result(entered, release, ['result']))
    future = stock_list._akshare_fallback()
    try:
        assert entered.wait(1)
        with pytest.raises(TimeoutError): future.result(timeout=0.01)
        assert stock_list._akshare_fallback() is future
    finally:
        release.set()
    assert future.result(timeout=1) == ['result']


@pytest.mark.parametrize('path', ['marketdata.http', 'src.platform.marketdata.collectors.market_http'])
def test_market_http_retry_timeouts_share_a_single_budget(monkeypatch, path):
    import importlib
    module = importlib.import_module(path)
    clock = [100.0]
    timeouts = []
    monkeypatch.setattr(module.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(module.time, 'sleep', lambda delay: clock.__setitem__(0, clock[0] + delay))
    class Client:
        def __init__(self, **kw): self.timeout = kw['timeout']; timeouts.append(self.timeout)
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def get(self, *args, **kwargs):
            clock[0] += self.timeout
            raise TimeoutError('slow upstream')
    monkeypatch.setattr(module.httpx, 'Client', Client)
    assert module.market_get('http://example.test', host_key='test', retries=9, timeout=10,
                             total_timeout=12, backoff=0, jitter=0) is None
    assert timeouts == [10,2] and clock[0] == 112


def test_answer_batch_is_not_replayed_after_cancellation_during_commit(monkeypatch):
    from src.modules.assistant.task_runner import AssistantTaskRunner, DurableRuntimeEventSink
    from src.modules.assistant.service import AssistantService
    from src.modules.assistant.repository import AssistantRepository
    from src.platform.tasking.contracts import TaskEventType
    from tests.test_assistant_task_events import _repository
    engine, db, repo, task = _repository()
    entered, release = threading.Event(), threading.Event()
    original = AssistantRepository.append_task_event
    def append(self, task_id, event_type, **kwargs):
        if event_type == TaskEventType.ANSWER_TOKEN:
            entered.set(); release.wait(2)
        return original(self, task_id, event_type, **kwargs)
    monkeypatch.setattr(AssistantRepository, 'append_task_event', append)
    runner = AssistantTaskRunner(session_factory=sessionmaker(bind=engine))
    sink = DurableRuntimeEventSink(AssistantService(repo), task.id, persist=runner._write)
    sink._pending_answer_tokens = ['partial answer']; sink._pending_answer_chars = 14
    async def run():
        pending = asyncio.create_task(sink.flush())
        assert await asyncio.to_thread(entered.wait, 1)
        pending.cancel(); await asyncio.sleep(0); release.set()
        with pytest.raises(asyncio.CancelledError): await pending
        assert not sink._pending_answer_tokens
        await sink.flush(check_cancelled=False)
    try:
        asyncio.run(run())
        assert len([e for e in repo.list_task_events(task.id) if e.event_type == TaskEventType.ANSWER_TOKEN.value]) == 1
    finally:
        release.set(); db.close(); engine.dispose()
