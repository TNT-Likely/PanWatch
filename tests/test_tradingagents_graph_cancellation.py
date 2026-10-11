"""Offline terminal-state probes through analyze and the installed SDK graph.

Only provider/model responses and observation I/O are substituted. Graph nodes,
callbacks, identity HTTP parsing, report and memory persistence stay real.
"""
from __future__ import annotations

import asyncio
import json
import socket
from importlib import import_module
from threading import Event, Lock
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
import requests
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from tradingagents.graph import trading_graph as tg

from src.modules.automation.tradingagents import agent as agent_module
from src.modules.automation.tradingagents import toolkit_adapter as ta
from src.modules.automation.tradingagents.observability import PanWatchProgressHandler

MODERN = not hasattr(tg.TradingAgentsGraph, "_get_provider_kwargs")


async def reached(event):
    assert await asyncio.to_thread(event.wait, 10), "worker did not reach its expected boundary"


@pytest.fixture
def execution_probe(monkeypatch, tmp_path):
    def no_socket(*args, **kwargs):
        pytest.fail("offline graph probe attempted an external connection")
    monkeypatch.setattr(socket.socket, "connect", no_socket)
    monkeypatch.setattr(socket.socket, "connect_ex", no_socket)
    monkeypatch.setattr(socket, "create_connection", no_socket)
    monkeypatch.setattr("src.platform.persistence.database.DB_PATH", str(tmp_path / "panwatch.db"))
    monkeypatch.setattr(PanWatchProgressHandler, "_emit", lambda *a, **k: None)
    monkeypatch.setattr(ta, "_emit_toolkit_log", lambda *a, **k: None)
    monkeypatch.setattr(tg.TradingAgentsGraph, "resolve_instrument_context", lambda *a, **k: "Offline exact identity")
    probe = SimpleNamespace(
        entered=Event(), release=Event(), stage="first_model", models=[], http=[],
        clients=[], runs=[], reports=[], root=tmp_path / "tradingagents", lock=Lock(), parallel=Event(),
    )

    def block():
        probe.entered.set()
        assert probe.release.wait(10), "test did not release its in-flight operation"

    class Model(BaseChatModel):
        @property
        def _llm_type(self):
            return "offline-cancellation-probe"

        def bind_tools(self, tools, **kwargs):
            return self

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            trace = ta._CURRENT_TRACE_ID.get()
            event = ta._CANCEL_EVENT.get()
            with probe.lock:
                probe.models.append((trace, event.is_set()))
                count = sum(item[0] == trace for item in probe.models)
                if trace == "cancel-run" and count == 3:
                    probe.parallel.set()
            if trace == "cancel-run" and (
                probe.stage == "first_model" and count == 1
                or probe.stage == "parallel_models"
                # Both supported graphs make the final free-text PM call after
                # the structured-output fixture has returned no parsed result.
                or probe.stage == "final_model" and count == 12
            ):
                block()
            return ChatResult(generations=[ChatGeneration(message=AIMessage(
                content="Rating: Hold\nOffline deterministic report.",
            ))])

    def client(**kwargs):
        probe.clients.append(kwargs)
        return SimpleNamespace(get_llm=lambda: Model(callbacks=kwargs.get("callbacks")))
    factory = import_module("tradingagents.llm_clients.factory") if MODERN else tg
    monkeypatch.setattr(factory, "create_llm_client", client)

    if MODERN:
        identity = import_module("tradingagents.extensions.ashare_identity")
        monkeypatch.setattr(identity, "_CACHE", type(identity._CACHE)())

        def send(session, request, **kwargs):
            endpoint = urlsplit(request.url)._replace(query="", fragment="").geturl()
            probe.http.append((endpoint, ta._CANCEL_EVENT.get().is_set()))
            endpoints = {"identity_sse": identity.SSE_URL, "identity_szse": identity.SZSE_URL,
                         "identity_english": identity.YAHOO_SEARCH_URL, "identity_bse": identity.BSE_URL}
            if endpoint == endpoints.get(probe.stage):
                block()
            if endpoint == identity.SSE_URL:
                payload = {"result": ([{"A_STOCK_CODE": "600519", "SEC_TYPE": "主板A",
                                         "SECURITY_ABBR_A_CN": "测试公司"}]
                                        if probe.stage == "identity_sse" else [])}
            elif endpoint == identity.SZSE_URL:
                payload = [{"metadata": {"tabkey": "tab1", "name": "A股列表"},
                            "data": ([{"agdm": "000001", "agjc": "测试公司"}]
                                     if probe.stage != "identity_bse" else [])}]
            elif endpoint == identity.YAHOO_SEARCH_URL:
                payload = {"quotes": [{"symbol": "000001.SZ", "quoteType": "EQUITY", "exchange": "SHZ", "longname": "Fixture Company"}]}
            elif endpoint == identity.BSE_URL:
                payload = {"jbzl": [{"SECURITY_CODE": "920001", "STR_CODEA": "920001", "SECUCODE": "920001.BJ",
                                     "TRADE_MARKET": "北京证券交易所", "SECURITY_TYPE": "北京证券交易所A股",
                                     "SECURITY_NAME_ABBR": "测试公司"}]}
            else:
                pytest.fail(f"unexpected identity endpoint: {endpoint}")
            response = requests.Response()
            response.status_code = 200
            response.url = request.url
            response._content = json.dumps(payload).encode()
            response._content_consumed = True
            return response
        monkeypatch.setattr(requests.sessions.Session, "send", send)

    chain_end = PanWatchProgressHandler.on_chain_end
    def final_chain(self, outputs, **kwargs):
        # Pause on the final decision's graph return, before the cancellation
        # callback and the SDK's out-of-graph completion persistence run.
        if (probe.stage == "final_chain" and ta._CURRENT_TRACE_ID.get() == "cancel-run"
                and isinstance(outputs, dict) and outputs.get("final_trade_decision")):
            block()
        return chain_end(self, outputs, **kwargs)
    monkeypatch.setattr(PanWatchProgressHandler, "on_chain_end", final_chain)
    log_state = tg.TradingAgentsGraph._log_state
    def write_report(self, *args, **kwargs):
        probe.reports.append((ta._CURRENT_TRACE_ID.get(), ta._CANCEL_EVENT.get().is_set()))
        result = log_state(self, *args, **kwargs)
        if probe.stage == "after_report" and ta._CURRENT_TRACE_ID.get() == "cancel-run":
            block()
        return result
    monkeypatch.setattr(tg.TradingAgentsGraph, "_log_state", write_report)

    def start(trace="cancel-run", *, timeout=False, symbol=None, analysts=None):
        symbol = symbol or {"identity_sse": "600519", "identity_szse": "000001",
                            "identity_english": "000001", "identity_bse": "920001"}.get(probe.stage, "AAPL")
        run = SimpleNamespace(done=Event(), errors=[], handler=PanWatchProgressHandler(trace))
        agent = agent_module.TradingAgentsAgent(analyst_types=analysts or ["news"], cache_ttl_hours=0)
        assert agent._available, agent._import_error
        if timeout:
            agent.timeout_minutes = 0.02
        context = SimpleNamespace(
            _force_refresh=True, _progress_handler=run.handler, report_language="en-US", model_label="fixture",
            ai_client=SimpleNamespace(api_key=None, model="fixture", base_url="https://example.invalid/v1"),
        )
        data = {"stock": SimpleNamespace(symbol=symbol, name="Fixture", asset_type="stock", market=SimpleNamespace(value="CN" if symbol.isdigit() else "US"))}
        original = agent._run_tradingagents_sync
        def worker(**kwargs):
            try:
                return original(**kwargs)
            except BaseException as exc:
                run.errors.append(exc)
                raise
            finally:
                run.done.set()
        monkeypatch.setattr(agent, "_run_tradingagents_sync", worker)
        run.analyze = lambda: agent.analyze(context, data)
        probe.runs.append(run)
        return run
    probe.start = start
    yield probe
    probe.release.set()
    assert ta._patch_refcount == 0


@pytest.mark.parametrize("terminal", ["cancel", "timeout"])
@pytest.mark.parametrize("storage", ["filesystem", "sqlite"])
@pytest.mark.parametrize("stage", ["first_model", "final_model", "final_chain", "after_report",
                                    "identity_sse", "identity_szse", "identity_english", "identity_bse"])
def test_analyze_terminal_state_stops_models_identity_and_completion(execution_probe, monkeypatch, stage, terminal, storage):
    if stage.startswith("identity_") and not MODERN:
        pytest.skip("official SDK has no mainland identity enrichment")
    if storage == "sqlite" and not MODERN:
        pytest.skip("official SDK has no SQLite archive backend")
    probe = execution_probe
    probe.stage = stage
    build_config = agent_module.build_ta_llm_config
    monkeypatch.setattr(agent_module, "build_ta_llm_config", lambda *a, **k: {
        **build_config(*a, **k), "storage_backend": storage,
    })
    run = probe.start(timeout=terminal == "timeout")
    # A terminal run must never blanket-delete an existing checkpoint/artifact.
    checkpoint = probe.root / "cache" / "existing-checkpoint"
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_text("keep pre-cancellation data")

    async def scenario():
        task = asyncio.create_task(run.analyze())
        try:
            await reached(probe.entered)
            saved_sites = list(ta._patch_saved_sites)
            if terminal == "cancel":
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                with pytest.raises(RuntimeError, match="分析超时"):
                    await task
            assert run.handler.cancel_event.is_set()
            assert not run.done.is_set()
            probe.release.set()
            await reached(run.done)
            assert len(run.errors) == 1 and isinstance(run.errors[0], ta.TradingAgentsCancelled)
            assert ta._patch_refcount == 0 and ta._patch_saved_sites == []
            for module, name, original in saved_sites:
                assert getattr(module, name) is original
        finally:
            probe.release.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())
    assert not any(cancelled for _, cancelled in probe.models)
    assert not any(cancelled for _, cancelled in probe.http)
    assert not any(cancelled for _, cancelled in probe.reports)
    assert len(probe.models) == {"first_model": 1, "final_model": 12,
                                 "final_chain": 12, "after_report": 12}.get(stage, 0)
    assert len(probe.http) == {"identity_sse": 1, "identity_szse": 2,
                               "identity_english": 3, "identity_bse": 3}.get(stage, 0)
    if storage == "filesystem":
        reports = list((probe.root / "results").rglob("full_states_log_*.json"))
        assert len(reports) == (1 if stage == "after_report" else 0)
    elif stage.startswith("identity_"):
        assert not (probe.root / "results" / "runs.sqlite3").exists()
    else:
        import sqlite3
        with sqlite3.connect(probe.root / "results" / "runs.sqlite3") as conn:
            assert conn.execute("SELECT status FROM runs").fetchall() == [("failed",)]
            count = conn.execute("SELECT count(*) FROM artifacts").fetchone()[0]
            assert (count > 0) == (stage == "after_report")
    assert not (probe.root / "memory" / "trading_memory.md").exists()
    assert checkpoint.read_text() == "keep pre-cancellation data"


@pytest.mark.parametrize("cancel_first", [False, True])
def test_concurrent_survivor_completes_and_persists_its_own_report(execution_probe, monkeypatch, cancel_first):
    probe = execution_probe
    monkeypatch.setattr(agent_module.TradingAgentsAgent, "_collect_toolkit_diagnostic", lambda *a: {})
    monkeypatch.setattr(agent_module, "save_analysis", lambda *a, **k: None)
    monkeypatch.setattr("src.modules.automation.suggestion_pool.save_suggestion", lambda *a, **k: None)
    canceled = probe.start()
    survivor = probe.start("survivor-run", symbol="MSFT")

    async def scenario():
        first = asyncio.create_task(canceled.analyze())
        await reached(probe.entered)
        second = asyncio.create_task(survivor.analyze())
        try:
            if cancel_first:
                first.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await first
                probe.release.set()
                await reached(canceled.done)
            result = await asyncio.wait_for(second, 10)
            assert result.raw_data
            if not cancel_first:
                first.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await first
                probe.release.set()
                await reached(canceled.done)
            assert not survivor.handler.cancel_event.is_set()
            assert not survivor.errors
            assert len(canceled.errors) == 1 and isinstance(canceled.errors[0], ta.TradingAgentsCancelled)
        finally:
            probe.release.set()
            for task in (first, second):
                if not task.done():
                    task.cancel()
            await asyncio.gather(first, second, return_exceptions=True)
    asyncio.run(scenario())
    assert not any(cancelled for _, cancelled in probe.models)
    assert sum(trace == "cancel-run" for trace, _ in probe.models) == 1
    assert sum(trace == "survivor-run" for trace, _ in probe.models) == 12
    assert probe.reports == [("survivor-run", False)]
    report = list((probe.root / "results").rglob("full_states_log_*.json"))
    assert len(report) == 1 and "MSFT" in str(report[0])
    assert json.loads(report[0].read_text())["company_of_interest"] == "MSFT"
    memory = (probe.root / "memory" / "trading_memory.md").read_text()
    assert "MSFT" in memory and "AAPL" not in memory


@pytest.mark.skipif(not MODERN, reason="official graph runs analysts sequentially")
@pytest.mark.parametrize("terminal", ["cancel", "timeout"])
def test_parallel_analysts_finish_only_their_inflight_calls(execution_probe, terminal):
    probe = execution_probe
    probe.stage = "parallel_models"
    run = probe.start(timeout=terminal == "timeout", analysts=["market", "news", "fundamentals"])

    async def scenario():
        task = asyncio.create_task(run.analyze())
        try:
            await reached(probe.parallel)
            if terminal == "cancel":
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                with pytest.raises(RuntimeError, match="分析超时"):
                    await task
            probe.release.set()
            await reached(run.done)
            assert len(run.errors) == 1 and isinstance(run.errors[0], ta.TradingAgentsCancelled)
        finally:
            probe.release.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())
    assert probe.models == [("cancel-run", False)] * 3
    assert not probe.reports and not probe.http
    assert not (probe.root / "memory" / "trading_memory.md").exists()


@pytest.mark.skipif(not MODERN, reason='Modern SDK memory file locking')
@pytest.mark.parametrize('terminal', ['cancel', 'timeout'])
@pytest.mark.parametrize('existing', [False, True])
def test_cancellation_during_real_memory_file_lock_wait(execution_probe, monkeypatch, terminal, existing):
    from contextlib import contextmanager
    from tradingagents.dataflows.files import locked
    from tradingagents.memory import log as log_module
    probe = execution_probe
    probe.stage = 'memory_lock'
    path = probe.root / 'memory' / 'trading_memory.md'
    path.parent.mkdir(parents=True, exist_ok=True)
    before = None
    if existing:
        memory = log_module.TradingMemoryLog({'memory_log_path': str(path)})
        memory.store_decision('IBM', '2026-10-01', 'Rating: Hold')
        assert memory.update_with_outcome('IBM', '2026-10-01', 0.01, 0.005, 5,
                                          'Keep prior settled decision', resolution_date='2026-10-09')
        before = path.read_text()
    entered = Event()
    original = log_module.locked
    @contextmanager
    def observed_lock(target, *args, **kwargs):
        if str(target) == str(path):
            entered.set()
        with original(target, *args, **kwargs) as held:
            yield held
    monkeypatch.setattr(log_module, 'locked', observed_lock)
    run = probe.start(timeout=terminal == 'timeout')
    async def scenario():
        with locked(path):
            task = asyncio.create_task(run.analyze())
            await reached(entered)
            assert probe.reports == [('cancel-run', False)]
            assert (path.read_text() if path.exists() else None) == before
            if terminal == 'cancel':
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                with pytest.raises(RuntimeError, match='分析超时'):
                    await task
            assert run.handler.cancel_event.is_set()
            assert not run.done.is_set()
        await reached(run.done)
        assert len(run.errors) == 1 and isinstance(run.errors[0], ta.TradingAgentsCancelled)
    asyncio.run(scenario())
    # No write started before cancellation: the process was waiting for the lock.
    assert (path.read_text() if path.exists() else None) == before
    assert log_module.locked is observed_lock
    assert len(list((probe.root / 'results').rglob('full_states_log_*.json'))) == 1
    with locked(path, wait=False) as acquired:
        assert acquired, 'canceled writer leaked the real file lock'


@pytest.mark.skipif(not MODERN, reason='Modern SDK memory file locking')
@pytest.mark.parametrize('terminal', ['cancel', 'timeout'])
def test_shared_memory_lock_cancel_does_not_block_surviving_writer(execution_probe, monkeypatch, terminal):
    from contextlib import contextmanager
    from tradingagents.dataflows.files import locked
    from tradingagents.memory import log as log_module
    probe = execution_probe
    probe.stage = 'memory_lock'
    path = probe.root / 'memory' / 'trading_memory.md'
    path.parent.mkdir(parents=True, exist_ok=True)
    waiting = {'cancel-run': Event(), 'survivor-run': Event()}
    original = log_module.locked
    @contextmanager
    def observed_lock(target, *args, **kwargs):
        if str(target) == str(path):
            waiting[ta._CURRENT_TRACE_ID.get()].set()
        with original(target, *args, **kwargs) as held:
            yield held
    monkeypatch.setattr(log_module, 'locked', observed_lock)
    monkeypatch.setattr(agent_module.TradingAgentsAgent, '_collect_toolkit_diagnostic', lambda *a: {})
    monkeypatch.setattr(agent_module, 'save_analysis', lambda *a, **k: None)
    monkeypatch.setattr('src.modules.automation.suggestion_pool.save_suggestion', lambda *a, **k: None)
    canceled = probe.start(timeout=terminal == 'timeout')
    survivor = probe.start('survivor-run', symbol='MSFT')
    async def scenario():
        with locked(path):
            first = asyncio.create_task(canceled.analyze())
            second = asyncio.create_task(survivor.analyze())
            await reached(waiting['cancel-run'])
            await reached(waiting['survivor-run'])
            assert ta._patch_refcount == 2
            if terminal == 'cancel':
                first.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await first
            else:
                with pytest.raises(RuntimeError, match='分析超时'):
                    await first
            assert canceled.handler.cancel_event.is_set()
            assert not survivor.handler.cancel_event.is_set()
            assert not path.exists()
        await reached(canceled.done)
        result = await asyncio.wait_for(second, 10)
        assert result.raw_data and not survivor.errors
        assert len(canceled.errors) == 1 and isinstance(canceled.errors[0], ta.TradingAgentsCancelled)
        assert ta._patch_refcount == 0 and log_module.locked is observed_lock
    asyncio.run(scenario())
    content = path.read_text()
    assert 'MSFT' in content and 'AAPL' not in content
    with locked(path, wait=False) as acquired:
        assert acquired


@pytest.mark.skipif(not MODERN, reason='Modern SDK memory file locking')
def test_memory_guard_preserves_unrelated_sdk_context_and_global_lock(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from tradingagents.dataflows import files
    from tradingagents.memory import log as log_module
    path = tmp_path / 'memory.md'
    original_global = files.locked
    original_memory = log_module.locked
    memory = log_module.TradingMemoryLog({'memory_log_path': str(path)})
    event = Event()
    event.set()
    with ta.panwatch_data_context({}, cancel_event=event), ta.patch_route_to_vendor():
        assert files.locked is original_global
        with pytest.raises(ta.TradingAgentsCancelled):
            memory.store_decision('AAPL', '2026-10-09', 'Rating: Hold')
        # A plain SDK thread did not inherit this canceled PanWatch request.
        with ThreadPoolExecutor(max_workers=1) as executor:
            executor.submit(memory.store_decision, 'MSFT', '2026-10-09', 'Rating: Hold').result(timeout=10)
    assert log_module.locked is original_memory and files.locked is original_global
    assert 'MSFT' in path.read_text() and 'AAPL' not in path.read_text()


@pytest.mark.skipif(not MODERN, reason='Modern mainland identity')
@pytest.mark.parametrize('stage', ['identity_sse', 'identity_szse', 'identity_english', 'identity_bse'])
def test_identity_after_canceled_lookup_is_retryable(execution_probe, monkeypatch, stage):
    probe = execution_probe
    probe.stage = stage
    monkeypatch.setattr(agent_module.TradingAgentsAgent, '_collect_toolkit_diagnostic', lambda *a: {})
    monkeypatch.setattr(agent_module, 'save_analysis', lambda *a, **k: None)
    monkeypatch.setattr('src.modules.automation.suggestion_pool.save_suggestion', lambda *a, **k: None)
    run = probe.start()
    async def scenario():
        task = asyncio.create_task(run.analyze())
        await reached(probe.entered)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        probe.release.set()
        await reached(run.done)
        count = len(probe.http)
        fresh = probe.start('fresh-identity')
        result = await asyncio.wait_for(fresh.analyze(), 10)
        assert result.raw_data
        assert not fresh.errors
        assert len(probe.http) > count
    asyncio.run(scenario())


@pytest.mark.parametrize('storage', ['filesystem', 'sqlite'])
def test_real_analyze_non_cancel_report_failure_keeps_original_error(execution_probe, monkeypatch, storage):
    if storage == 'sqlite' and not MODERN:
        pytest.skip('Official SDK has no SQLite archive backend')
    import builtins
    probe = execution_probe
    probe.stage = 'ordinary_error'
    original_config = agent_module.build_ta_llm_config
    monkeypatch.setattr(agent_module, 'build_ta_llm_config', lambda *a, **k: {
        **original_config(*a, **k), 'storage_backend': storage,
    })
    failure = PermissionError('controlled report write failure')
    if storage == 'filesystem':
        original_open = builtins.open
        def broken_open(file, *a, **k):
            if 'full_states_log_' in str(file):
                raise failure
            return original_open(file, *a, **k)
        monkeypatch.setattr(builtins, 'open', broken_open)
    else:
        from tradingagents.storage import sqlite as archive_module
        monkeypatch.setattr(archive_module.SQLiteRunStore, 'write_artifact', lambda *a, **k: (_ for _ in ()).throw(failure))
    run = probe.start()
    async def scenario():
        with pytest.raises(PermissionError) as error:
            await run.analyze()
        assert error.value is failure
        await reached(run.done)
        assert run.errors == [failure]
        assert ta._patch_refcount == 0 and ta._patch_saved_sites == []
    asyncio.run(scenario())
    if storage == 'sqlite':
        import sqlite3
        with sqlite3.connect(probe.root / 'results' / 'runs.sqlite3') as connection:
            assert connection.execute('SELECT status FROM runs').fetchall() == [('failed',)]
