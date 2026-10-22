"""Offline HTTP probes through real analyze, installed graphs and news ToolNodes."""
from __future__ import annotations

import asyncio
import json
import socket
import time
from importlib import import_module
from threading import Event
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest
import requests
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from tradingagents.graph import trading_graph as tg

from src.modules.automation.tradingagents import agent as agent_module
from src.modules.automation.tradingagents import toolkit_adapter as ta
from src.modules.automation.tradingagents import upstream_compat as compat
from src.modules.automation.tradingagents.observability import PanWatchProgressHandler

MODERN = not hasattr(tg.TradingAgentsGraph, "_get_provider_kwargs")


class NewsCompleted(RuntimeError):
    """Stop the offline probe only after the real tool returned its result."""


async def wait_event(event):
    assert await asyncio.to_thread(event.wait, 10), "worker did not reach the expected boundary"


@pytest.fixture
def news_probe(monkeypatch, tmp_path):
    # The graph, public tools, source selection, pacing and HTTP parsing are real.
    # Replace only external model/HTTP responses and observation/persistence I/O.
    def no_socket(*args, **kwargs):
        pytest.fail("offline regression attempted a real network connection")
    monkeypatch.setattr(socket.socket, "connect", no_socket)
    monkeypatch.setattr(socket.socket, "connect_ex", no_socket)
    monkeypatch.setattr(socket, "create_connection", no_socket)
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "private-cache"))
    monkeypatch.setattr("src.platform.persistence.database.DB_PATH", str(tmp_path / "panwatch.db"))
    monkeypatch.setattr(ta, "_emit_toolkit_log", lambda *a, **k: None)
    monkeypatch.setattr(tg.TradingAgentsGraph, "resolve_instrument_context", lambda *a, **k: "Offline exact identity")
    probe = SimpleNamespace(
        entered=Event(), release=Event(), worker_done=Event(), worker_errors=[],
        http=[], closed=[], sleeps=[], clients=[], tool_results=[], settings={},
        primary=lambda *a, **k: "DATA_UNAVAILABLE: no primary news",
        stage="primary", symbol="AAPL", market="US", transport_error=False,
    )

    class NewsModel(BaseChatModel):
        @property
        def _llm_type(self):
            return "offline-news-cancellation"

        def bind_tools(self, tools, **kwargs):
            return self.bind(fixture_tools=[tool.name for tool in tools])

        def _generate(self, messages, stop=None, run_manager=None, **kwargs):
            returned = [str(message.content) for message in messages if isinstance(message, ToolMessage)]
            if returned:
                probe.tool_results.extend(returned)
                raise NewsCompleted("real news tool returned")
            assert "get_news" in kwargs.get("fixture_tools", [])
            args = {"start_date": "2026-10-01", "end_date": "2026-10-09"}
            if not MODERN:
                args["ticker"] = probe.symbol
            return ChatResult(generations=[ChatGeneration(message=AIMessage(
                content="", tool_calls=[dict(name="get_news", args=args, id="news-probe")],
            ))])

    def client(**kwargs):
        probe.clients.append(kwargs)
        return SimpleNamespace(get_llm=lambda: NewsModel())
    factory = import_module("tradingagents.llm_clients.factory") if MODERN else tg
    monkeypatch.setattr(factory, "create_llm_client", client)
    monkeypatch.setattr(compat.vendor_router(), "route_to_vendor", lambda *a, **k: probe.primary(*a, **k))
    build_config = agent_module.build_ta_llm_config

    def config(*args, **kwargs):
        return {**build_config(*args, **kwargs), "save_report": False,
                "company_website_enabled": False, "duckduckgo_news_cache_ttl": 0,
                **probe.settings}
    monkeypatch.setattr(agent_module, "build_ta_llm_config", config)
    if MODERN:
        ddg = import_module("tradingagents.extensions.duckduckgo_news")
        website = import_module("tradingagents.extensions.company_website")
        announcements = import_module("tradingagents.extensions.ashare_announcements")
        for module in (ddg, website, announcements):
            monkeypatch.setattr(module, "_CACHE", type(module._CACHE)())
        monkeypatch.setattr(ddg, "_BLOCK_UNTIL", 0)
        monkeypatch.setattr(ddg, "_BLOCK_ERROR", None)
        monkeypatch.setattr(ddg, "_LAST_REQUEST_STARTED", None)
        probe.ddg = ddg
        monkeypatch.setattr(tg, "create_run", lambda *a, **k: None)
        identity = import_module("tradingagents.extensions.ashare_identity")
        monkeypatch.setattr(identity, "_CACHE", type(identity._CACHE)())
        monkeypatch.setattr(identity, "_lookup", lambda exchange, code, *a: {
            "exchange": "SSE", "canonical_symbol": code + ".SS", "chinese_short_name": "测试公司",
            "source": {"provider": "SSE", "url": "https://example.invalid/"},
        } if exchange == "SS" else None)

    def block():
        probe.entered.set()
        assert probe.release.wait(10), "test did not release the in-flight operation"

    def send(session, request, **kwargs):
        event = ta._CANCEL_EVENT.get()
        endpoint = urlsplit(request.url)._replace(query="", fragment="").geturl()
        probe.http.append((endpoint, event.is_set() if event else None, kwargs))
        if (probe.stage == "ddg_landing" and endpoint == "https://duckduckgo.com/"
                or probe.stage == "company" and endpoint == "https://www.sec.gov/files/company_tickers.json"):
            block()
            if probe.transport_error:
                raise requests.Timeout("offline transport timeout")
        payload = {
            "https://duckduckgo.com/": 'vqd="123-456";',
            "https://duckduckgo.com/news.js": json.dumps({"results": []}),
            "https://html.duckduckgo.com/html/": '<div class="no-results">No results found</div>',
            "https://www.sec.gov/files/company_tickers.json": json.dumps({"0": {"ticker": "AAPL", "cik_str": 320193}}),
            "https://data.sec.gov/submissions/CIK0000320193.json": json.dumps({
                "cik": "0000320193", "entityType": "operating", "tickers": ["AAPL"],
                "exchanges": ["Nasdaq"], "name": "Apple", "website": "https://apple.com/",
            }),
        }
        assert endpoint in payload, f"unexpected HTTP endpoint: {endpoint}"
        response = requests.Response()
        response.status_code = 200
        response.url = request.url
        response.headers["content-type"] = "application/json" if endpoint.endswith(("json", "news.js")) else "text/html"
        response._content = payload[endpoint].encode()
        response._content_consumed = True
        response.close = lambda: probe.closed.append(endpoint)
        return response
    monkeypatch.setattr(requests.sessions.Session, "send", send)
    probe.block = block

    def start_agent(*, timeout=False):
        agent = agent_module.TradingAgentsAgent(analyst_types=["news"], cache_ttl_hours=0, llm_timeout_seconds=7)
        assert agent._available, agent._import_error
        # Exercise analyze's actual wait_for deadline without making the suite
        # wait a full production minute. No wait_for or cancellation is mocked.
        if timeout:
            agent.timeout_minutes = 2 / 60
        handler = PanWatchProgressHandler("offline-news-cancel")
        context = SimpleNamespace(
            _force_refresh=True, report_language="en-US", model_label="offline", _progress_handler=handler,
            ai_client=SimpleNamespace(api_key=None, model="offline-news", base_url="https://example.invalid/v1"),
        )
        data = {"stock": SimpleNamespace(symbol=probe.symbol, name="Fixture company",
                                          market=SimpleNamespace(value=probe.market))}
        run_sync = agent._run_tradingagents_sync

        def observed_worker(**kwargs):
            try:
                return run_sync(**kwargs)
            except BaseException as exc:
                probe.worker_errors.append(exc)
                raise
            finally:
                probe.worker_done.set()
        monkeypatch.setattr(agent, "_run_tradingagents_sync", observed_worker)
        return agent, context, data, handler
    probe.start_agent = start_agent
    yield probe
    # Never let a failed assertion leave the to_thread worker blocked.
    probe.release.set()
    assert ta._patch_refcount == 0


@pytest.mark.parametrize("terminal", ["cancel", "timeout"])
@pytest.mark.parametrize("stage", [
    "primary", "ddg_landing", "ddg_landing_error", "ddg_pacing",
    "company", "company_error", "announcements",
])
def test_analyze_terminal_state_stops_later_http(news_probe, monkeypatch, terminal, stage):
    if not MODERN and stage != "primary":
        pytest.skip("official pinned SDK has no optional news enrichment")
    probe = news_probe
    probe.transport_error = stage.endswith("_error")
    stage = stage.removesuffix("_error")
    probe.stage = stage
    if stage == "primary":
        def primary(*args, **kwargs):
            probe.block()
            return "DATA_UNAVAILABLE: no primary news"
        probe.primary = primary
    elif stage == "company":
        probe.settings["company_website_enabled"] = True
    elif stage in {"ddg_pacing", "announcements"}:
        def sleep(delay):
            probe.sleeps.append(delay)
            probe.block()
        # Scope the clock substitution to the DDG module, not Python's global
        # time module used by asyncio, LangGraph and concurrent workers.
        monkeypatch.setattr(probe.ddg, "time", SimpleNamespace(monotonic=time.monotonic, sleep=sleep))
        if stage == "announcements":
            probe.symbol, probe.market = "600519", "CN"
            probe.settings["duckduckgo_news_enabled"] = False
            monkeypatch.setattr(probe.ddg, "_LAST_REQUEST_STARTED", time.monotonic() + 2)

    agent, context, data, handler = probe.start_agent(timeout=terminal == "timeout")

    async def scenario():
        task = asyncio.create_task(agent.analyze(context, data))
        try:
            await wait_event(probe.entered)
            saved_sites = list(ta._patch_saved_sites)
            assert saved_sites
            assert not handler.cancel_event.is_set()
            if terminal == "cancel":
                task.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await task
            else:
                with pytest.raises(RuntimeError, match="分析超时"):
                    await task
            assert handler.cancel_event.is_set()
            # The original HTTP may still be in flight. Releasing it must not
            # allow a new HTTP request, even inside the same enrichment call.
            assert not probe.worker_done.is_set()
            probe.release.set()
            await wait_event(probe.worker_done)
            assert len(probe.worker_errors) == 1
            assert isinstance(probe.worker_errors[0], ta.TradingAgentsCancelled)
            assert ta._patch_refcount == 0
            assert ta._patch_saved_sites == []
            for module, name, original in saved_sites:
                assert getattr(module, name) is original
        finally:
            probe.release.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())
    expected = {
        "primary": [], "announcements": [],
        "ddg_landing": ["https://duckduckgo.com/"],
        "ddg_pacing": ["https://duckduckgo.com/"],
        "company": ["https://www.sec.gov/files/company_tickers.json"],
    }[stage]
    assert [endpoint for endpoint, _, _ in probe.http] == expected
    assert not any(cancelled for _, cancelled, _ in probe.http)
    assert probe.closed == ([] if probe.transport_error else expected)
    for _, _, kwargs in probe.http:
        assert kwargs["allow_redirects"] is False and kwargs["stream"] is True
        assert 0 < kwargs["timeout"].total <= 15
    if stage in {"ddg_pacing", "announcements"}:
        assert len(probe.sleeps) == 1 and probe.sleeps[0] > 0
    assert not probe.tool_results
    assert len(probe.clients) == 2
    assert all(client["timeout"] == 7.0 and client["max_retries"] == 0 for client in probe.clients)


@pytest.mark.skipif(not MODERN, reason="official pinned SDK has no optional news enrichment")
@pytest.mark.parametrize("cancel_first", [True, False])
def test_concurrent_analyze_survives_other_run_cancellation(news_probe, monkeypatch, cancel_first):
    probe = news_probe
    probe.stage = "ddg_landing"
    probe.settings["duckduckgo_news_min_interval"] = 0
    first, first_context, first_data, first_handler = probe.start_agent()
    second, second_context, second_data, second_handler = probe.start_agent()
    first_handler.trace_id = "first-news-run"
    second_handler.trace_id = "second-news-run"
    waiting = Event()
    request_lock = probe.ddg._REQUEST_LOCK

    def acquire(*args, **kwargs):
        if ta._CURRENT_TRACE_ID.get() == "second-news-run":
            waiting.set()
        return request_lock.acquire(*args, **kwargs)
    monkeypatch.setattr(probe.ddg, "_REQUEST_LOCK", SimpleNamespace(acquire=acquire, release=request_lock.release))

    async def scenario():
        first_task = asyncio.create_task(first.analyze(first_context, first_data))
        second_task = None
        try:
            await wait_event(probe.entered)
            # The first send is already blocked. Later sends belong to the
            # surviving run and can proceed when DDG releases its shared gate.
            probe.stage = "concurrent"
            second_task = asyncio.create_task(second.analyze(second_context, second_data))
            await wait_event(waiting)
            assert ta._patch_refcount == 2
            saved_sites = list(ta._patch_saved_sites)
            cancelled, survivor = (first_task, second_task) if cancel_first else (second_task, first_task)
            cancelled_handler, survivor_handler = (first_handler, second_handler) if cancel_first else (second_handler, first_handler)
            cancelled.cancel()
            with pytest.raises(asyncio.CancelledError):
                await cancelled
            assert cancelled_handler.cancel_event.is_set()
            assert not survivor_handler.cancel_event.is_set()
            probe.release.set()
            # The survivor's real ToolNode must return news to its model. A
            # cancelled waiter must also check its event after taking the lock.
            with pytest.raises(NewsCompleted):
                await survivor
            return saved_sites
        finally:
            probe.release.set()
            tasks = [first_task] + ([second_task] if second_task is not None else [])
            for task in tasks:
                if not task.done():
                    task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
    saved_sites = asyncio.run(scenario())  # also drains the cancelled to_thread worker
    assert ta._patch_refcount == 0
    for module, name, original in saved_sites:
        assert getattr(module, name) is original
    assert [url for url, _, _ in probe.http] == (
        ["https://duckduckgo.com/"] * (2 if cancel_first else 1) + ["https://duckduckgo.com/news.js"]
    )
    assert not any(cancelled for _, cancelled, _ in probe.http)
    assert len(probe.tool_results) == 1
    assert sum(isinstance(error, ta.TradingAgentsCancelled) for error in probe.worker_errors) == 1
    assert sum(isinstance(error, NewsCompleted) for error in probe.worker_errors) == 1


@pytest.mark.skipif(not MODERN, reason="official pinned SDK has no optional news enrichment")
def test_pacing_keeps_original_enrichment_deadline(news_probe, monkeypatch):
    probe = news_probe
    probe.stage = "ddg_pacing"
    probe.settings["duckduckgo_news_total_timeout"] = 5
    offset = [0.0]

    def sleep(delay):
        probe.sleeps.append(delay)
        probe.block()
    monkeypatch.setattr(probe.ddg, "time", SimpleNamespace(
        monotonic=lambda: time.monotonic() + offset[0], sleep=sleep,
    ))
    agent, context, data, handler = probe.start_agent()

    async def scenario():
        task = asyncio.create_task(agent.analyze(context, data))
        try:
            await wait_event(probe.entered)
            assert not handler.cancel_event.is_set()
            offset[0] = 10  # The original shared 5-second budget expires in pacing.
            probe.release.set()
            with pytest.raises(NewsCompleted):
                await task
        finally:
            probe.release.set()
            if not task.done():
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
    asyncio.run(scenario())
    assert [url for url, _, _ in probe.http] == ["https://duckduckgo.com/"]
    assert "budget_exhausted" in probe.tool_results[0]
    assert len(probe.worker_errors) == 1 and isinstance(probe.worker_errors[0], NewsCompleted)
