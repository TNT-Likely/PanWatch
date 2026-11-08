import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from openai import BadRequestError, RateLimitError

from src.modules.research.orchestration.context_limits import parse_context_limit
from src.modules.research.orchestration.gateway import ResearchModelGateway


def error(message):
    return BadRequestError(message, response=httpx.Response(400, request=httpx.Request('POST', 'https://fixture.test')), body=None)


def context_error(inputs=15490, window=16384):
    return error(f'Input validation error: `inputs` tokens + `max_new_tokens` must be <= {window}. '
                 f'Given: {inputs} `inputs` tokens and 1024 `max_new_tokens`')


def reply():
    return SimpleNamespace(model='fixture-response', usage=None, choices=[SimpleNamespace(
        finish_reason='stop', message=SimpleNamespace(content='{}', tool_calls=[]))])


def gateway(*outcomes):
    calls, candidates = [], []
    for index, outcome in enumerate(outcomes):
        create = AsyncMock(side_effect=outcome)
        sdk = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
        client = SimpleNamespace(model=f'fixture-{index}', base_url=f'https://fixture-{index}.test',
                                 client=SimpleNamespace(with_options=lambda _sdk=sdk, **options: _sdk))
        candidates.append((client, f'fixture-{index}'))
        calls.append(create)
    return ResearchModelGateway(SimpleNamespace(_iter_candidates=lambda: candidates)), calls


def complete(model, **overrides):
    permits = []
    async def before_attempt():
        permits.append(1)
    kwargs = dict(role='risk', messages=[{'role': 'user', 'content': 'captured evidence'}], tools=[],
                  schema={}, max_output_tokens=None, timeout=10, before_attempt=before_attempt)
    kwargs.update(overrides)
    result = asyncio.run(model.complete(**kwargs))
    return result, permits


@pytest.mark.parametrize('output', [None, 16000])
def test_actual_16k_failure_switches_without_starving_output_or_altering_input(output, caplog):
    model, calls = gateway([context_error()], [reply()])
    messages = [{'role': 'system', 'content': 'schema'}, {'role': 'user', 'content': 'all target evidence'}]
    result, permits = complete(model, max_output_tokens=output, messages=messages)
    assert result.model == 'fixture-response' and len(permits) == 2
    assert calls[0].await_count == calls[1].await_count == 1
    assert calls[1].call_args.kwargs['messages'] == messages
    assert 'max_tokens' not in calls[0].call_args.kwargs if output is None else calls[0].call_args.kwargs['max_tokens'] == output
    assert calls[1].call_args.kwargs.get('max_tokens') == output
    assert 'role=risk' in caplog.text and 'window=16384' in caplog.text and 'action=switch' in caplog.text


@pytest.mark.parametrize('output', [None, 16000])
def test_context_repair_uses_explicit_feasible_output_with_safety_margin(output):
    model, calls = gateway([context_error(inputs=12000), reply()])
    _, permits = complete(model, max_output_tokens=output)
    assert len(permits) == 2
    assert calls[0].call_args.kwargs['max_tokens'] == 16384 - 12000 - 163


def test_repair_never_raises_explicit_user_output_cap():
    model, calls = gateway([context_error(inputs=12000), reply()])
    complete(model, max_output_tokens=2048)
    assert calls[0].call_args.kwargs['max_tokens'] == 2048


def test_repeated_context_rejection_switches_after_one_context_repair():
    model, calls = gateway([context_error(inputs=12000)] * 2, [reply()])
    _, permits = complete(model)
    assert len(permits) == 3 and calls[0].await_count == 2 and calls[1].await_count == 1


def test_multi_target_contract_requires_more_than_tiny_remaining_output():
    model, calls = gateway([context_error(inputs=14000)], [reply()])
    schema = {'properties': {'recommendations': {'minItems': 3}, 'session_notes': {'minItems': 3}}}
    complete(model, schema=schema, role='strategy')
    assert calls[0].await_count == calls[1].await_count == 1


def test_negative_remaining_context_skips_instead_of_sending_invalid_token_limit():
    model, calls = gateway([context_error(inputs=17000)], [reply()])
    complete(model)
    assert calls[0].await_count == 1


def test_no_capable_candidates_fails_explicitly_without_partial_result():
    model, calls = gateway([context_error()])
    with pytest.raises(RuntimeError, match='context capacity: fixture-0: input 15490, window 16384'):
        complete(model)
    assert calls[0].await_count == 1


def test_unknown_input_count_switches_instead_of_guessing_a_tiny_output_cap():
    model, calls = gateway([error('maximum context length is 16384 tokens')], [reply()])
    complete(model)
    assert calls[0].await_count == 1


def test_openai_context_message_is_not_confused_with_output_only_cap():
    limit = parse_context_limit(error('This model maximum context length is 16384 tokens. '
                                    'However, you requested 16514 tokens (15490 in the messages, 1024 in the completion).'))
    assert limit.window == 16384 and limit.input_tokens == 15490
    assert parse_context_limit(error('max_completion_tokens must be in [1, 16384]')) is None
    assert parse_context_limit(error('API parameter error code=1210')) is None


def test_known_candidate_limit_is_rechecked_after_tool_and_repair_growth():
    model, calls = gateway([context_error()], [reply(), reply()])
    complete(model)
    messages = [{'role': 'user', 'content': 'all targets and immutable citations'},
                {'role': 'assistant', 'content': 'previous invalid report'},
                {'role': 'tool', 'tool_call_id': 'fixture-tool', 'content': '证据' * 4000}]
    _, permits = complete(model, messages=messages, tools=[{'type': 'function', 'function': {'name': 'details'}}])
    assert len(permits) == 1 and calls[0].await_count == 1 and calls[1].await_count == 2
    assert calls[1].call_args.kwargs['messages'] == messages
    assert calls[1].call_args.kwargs['tools'][0]['function']['name'] == 'details'


def test_learned_limits_are_candidate_specific_not_applied_to_larger_fallback():
    model, calls = gateway([context_error()], [reply(), reply()])
    complete(model)
    complete(model, messages=[{'role': 'user', 'content': 'x' * 20000}])
    assert calls[0].await_count == 1 and calls[1].await_count == 2
    assert 'max_tokens' not in calls[1].call_args.kwargs


def test_auth_or_content_bad_requests_are_not_treated_as_context_fallback():
    model, calls = gateway([error('content policy rejected the prompt')], [reply()])
    with pytest.raises(BadRequestError):
        complete(model)
    assert calls[1].await_count == 0


def test_rate_limit_still_uses_next_candidate_and_counts_both_attempts():
    request = httpx.Request('POST', 'https://fixture.test')
    model, calls = gateway([RateLimitError('fixture rate limit', response=httpx.Response(429, request=request), body=None)], [reply()])
    _, permits = complete(model)
    assert len(permits) == 2 and calls[1].await_count == 1


def test_attempt_budget_failure_is_not_hidden_by_context_recovery():
    model, calls = gateway([context_error(inputs=12000), reply()])
    attempts = 0
    async def permit():
        nonlocal attempts
        attempts += 1
        if attempts == 2:
            raise ValueError('fixture attempt budget exhausted')
    with pytest.raises(ValueError, match='attempt budget'):
        complete(model, before_attempt=permit)
    assert calls[0].await_count == 1
