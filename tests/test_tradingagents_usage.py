"""Real provider usage across aggregate, chat, missing and duplicate callbacks."""
from types import SimpleNamespace

from src.modules.automation.tradingagents.observability import PanWatchProgressHandler, aggregate_progress


def response(aggregate=None, message=None):
    return SimpleNamespace(llm_output={'token_usage': aggregate} if aggregate is not None else None,
        generations=[[SimpleNamespace(message=message)]] if message is not None else [])


def test_usage_prefers_provider_total_without_counting_message_metadata_twice():
    handler = PanWatchProgressHandler('usage-test')
    result = response({'prompt_tokens': 1200, 'completion_tokens': 300, 'total_tokens': 1500},
                      SimpleNamespace(usage_metadata={'input_tokens': 1200, 'output_tokens': 300, 'total_tokens': 1500}))
    handler.on_llm_end(result, run_id='one')
    handler.on_llm_end(result, run_id='one')
    assert handler.token_usage == {'input_tokens': 1200, 'output_tokens': 300, 'total_tokens': 1500,
                                  'recorded_calls': 1, 'completed_calls': 1, 'complete': True}


def test_usage_supports_streaming_chat_metadata_and_distinguishes_missing_calls():
    handler = PanWatchProgressHandler('usage-test')
    handler.on_llm_end(response(message=SimpleNamespace(usage_metadata={'input_tokens': 80, 'output_tokens': 20, 'total_tokens': 100})), run_id='one')
    handler.on_llm_end(response(), run_id='two')
    assert handler.token_usage['total_tokens'] == 100
    assert handler.token_usage['recorded_calls'] == 1
    assert handler.token_usage['completed_calls'] == 2
    assert handler.token_usage['complete'] is False


def test_zero_usage_is_real_and_errors_or_invalid_usage_make_the_total_partial():
    handler = PanWatchProgressHandler('usage-test')
    handler.on_llm_end(response({'prompt_tokens': 0, 'completion_tokens': 0, 'total_tokens': 0}), run_id='zero')
    assert handler.token_usage['complete'] is True
    handler.on_llm_end(response({'prompt_tokens': -1, 'completion_tokens': 12}), run_id='invalid')
    handler.on_llm_error(RuntimeError('retry'), run_id='error')
    assert handler.token_usage['total_tokens'] == 0
    assert handler.token_usage['recorded_calls'] == 1
    assert handler.token_usage['completed_calls'] == 3
    assert handler.token_usage['complete'] is False


def test_usage_can_come_from_message_response_metadata():
    handler = PanWatchProgressHandler('usage-test')
    handler.on_llm_end(response(message=SimpleNamespace(response_metadata={'usage': {'input_tokens': 9, 'output_tokens': 3, 'total_tokens': 12}})), run_id='one')
    assert handler.token_usage['total_tokens'] == 12
    assert handler.token_usage['complete'] is True


def test_progress_uses_cumulative_snapshot_without_summing_it_again():
    first = {'input_tokens': 8, 'output_tokens': 2, 'total_tokens': 10, 'recorded_calls': 1, 'completed_calls': 1, 'complete': True}
    last = {**first, 'input_tokens': 16, 'output_tokens': 4, 'total_tokens': 20, 'recorded_calls': 2, 'completed_calls': 2}
    result = aggregate_progress([{'tags': {'stage': 'llm_call', 'action': 'llm_end', 'token_usage': value}} for value in [first, first, last]])
    assert result['token_usage'] == last


def test_progress_usage_is_current_even_when_tool_log_history_exceeds_limit():
    from datetime import datetime, timezone
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    from src.modules.automation.api.agents import get_run_progress
    from src.platform.persistence.database import Base
    from src.platform.persistence.models import AgentRun, LogEntry

    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    try:
        with Session(engine) as db:
            stamp = datetime.now(timezone.utc)
            db.add(AgentRun(agent_name='tradingagents', status='success', trace_id='usage-long'))
            for index in range(501):
                db.add(LogEntry(trace_id='usage-long', timestamp=stamp, level='INFO', event='ta_toolkit', tags={'action': 'hit'}))
            usage = {'input_tokens': 800, 'output_tokens': 200, 'total_tokens': 1000, 'recorded_calls': 2, 'completed_calls': 2, 'complete': True}
            db.add(LogEntry(trace_id='usage-long', timestamp=stamp, level='INFO', event='ta_progress', tags={'stage': 'llm_call', 'action': 'llm_end', 'token_usage': usage}))
            db.commit()
            assert get_run_progress('usage-long', db)['token_usage'] == usage
    finally:
        engine.dispose()
