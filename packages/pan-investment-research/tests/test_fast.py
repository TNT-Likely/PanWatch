import asyncio
from datetime import timedelta

import pytest
from pydantic import ValidationError

from pan_investment_research import InvestmentResearch, ResearchPolicy, ResearchSignal
from test_research import Model, prepared, DirectionalModel


def policy(**updates):
    return ResearchPolicy.model_validate(dict(profile='intraday', graph='intraday_fast', specialists=['technical', 'events'],
        debate=False, max_feedback=0, max_attempts=6, **updates))


def test_unchanged_input_never_calls_a_model_or_invents_watch():
    model = Model()
    result = asyncio.run(InvestmentResearch().run(prepared(), model=model, policy=policy()))
    assert result.status == result.completion == 'skipped'
    assert result.recommendations == [] and result.usage['attempts'] == 0 and not model.calls
    assert len(result.artifacts) == 1 and result.artifacts[0].kind == 'rule'


@pytest.mark.parametrize('domains,calls', [(['technical'], 2), (['events'], 2), (['technical', 'events'], 3)])
def test_only_triggered_experts_and_one_decision_use_the_model(domains, calls):
    data, model = prepared(), Model()
    data.signals = [ResearchSignal(stock_key=data.securities[0].key, domain=d, reasons=['new signal'], observed_at=data.as_of) for d in domains]
    result = asyncio.run(InvestmentResearch().run(data, model=model, policy=policy()))
    assert result.status == 'ready', result.reason
    assert result.graph == 'intraday_fast' and result.usage['attempts'] == calls
    assert set(model.calls) == {*domains, 'strategy'}
    assert [a.role for a in result.artifacts if a.kind == 'rule'] == ['gate', 'execution']
    assert result.recommendations[0].execution_status == 'not_applicable'


def test_fast_multi_stock_excludes_quiet_stocks_and_preserves_scoped_evidence():
    data, model = prepared(('CN:600519', 'CN:002594', 'HK:00700')), Model()
    data.signals = [ResearchSignal(stock_key=data.securities[0].key, domain='technical', reasons=['breakout'], observed_at=data.as_of),
                    ResearchSignal(stock_key=data.securities[2].key, domain='events', reasons=['announcement'], observed_at=data.as_of)]
    result = asyncio.run(InvestmentResearch().run(data, model=model, policy=policy()))
    assert result.status == 'ready', result.reason
    assert {r.stock_key for r in result.recommendations} == {'CN:600519', 'HK:00700'}
    for role, payload in model.payloads:
        assert 'CN:002594' not in {s['key'] for s in payload['input']['securities']}
        if role == 'technical':
            assert payload['input']['portfolio'] == {}
            assert {e['stock_key'] for e in payload['input']['evidence']} == {'CN:600519'}


def test_fast_rules_preserve_a_buy_outlook_but_block_unverified_execution():
    data, model = prepared(), DirectionalModel()
    data.signals = [ResearchSignal(stock_key='CN:600519', domain='technical', reasons=['volume'], observed_at=data.as_of)]
    data.portfolio = {'accounts': [{'available_funds': 0}]}
    result = asyncio.run(InvestmentResearch().run(data, model=model, policy=policy()))
    assert result.completion == 'complete', result.reason
    rec = result.recommendations[0]
    assert (rec.outlook, rec.action, rec.execution_status, rec.quantity) == ('bullish', 'buy', 'blocked', None)
    assert 'insufficient_funds' in rec.execution_constraints
    assert not model.calls['risk'] and result.usage['attempts'] == 2


def test_material_only_debate_skips_quiet_runs_but_honors_material_signals():
    graph = InvestmentResearch()
    quiet, busy = Model(), Model()
    data = prepared()
    first = asyncio.run(graph.run(data, model=quiet, policy=ResearchPolicy(debate_on='material')))
    data.signals = [ResearchSignal(stock_key='CN:600519', domain='events', reasons=['material report'], observed_at=data.as_of, material=True)]
    second = asyncio.run(graph.run(data, model=busy, policy=ResearchPolicy(debate_on='material')))
    assert first.completion == second.completion == 'complete'
    assert first.usage['attempts'] == 7 and second.usage['attempts'] == 9
    assert quiet.calls['bull'] == quiet.calls['bear'] == 0 and busy.calls['bull'] == busy.calls['bear'] == 1


def test_signals_cannot_expand_scope_or_use_future_observations():
    for key, delta in [('US:FAKE', 0), ('CN:600519', 1)]:
        data = prepared()
        with pytest.raises(ValidationError):
            type(data).model_validate({**data.model_dump(), 'signals': [dict(stock_key=key, domain='technical', reasons=['x'], observed_at=data.as_of + timedelta(seconds=delta))]})


def test_fast_cannot_configure_away_safety_or_add_full_graph_nodes():
    for changes in [{'debate': True}, {'max_feedback': 1}, {'specialists': ['flow']}]:
        with pytest.raises(ValidationError):
            ResearchPolicy.model_validate({**policy().model_dump(), **changes})


def test_same_captured_input_has_fewer_calls_with_the_same_controlled_market_view():
    data = prepared(('CN:600519', 'CN:002594'))
    data.signals = [ResearchSignal(stock_key=s.key, domain='technical', reasons=['new signal'], observed_at=data.as_of) for s in data.securities]
    engine = InvestmentResearch()
    full = asyncio.run(engine.run(data, model=DirectionalModel(), policy=ResearchPolicy()))
    fast = asyncio.run(engine.run(data, model=DirectionalModel(), policy=policy()))
    assert full.completion == fast.completion == 'complete'
    assert full.usage['attempts'] == 9 and fast.usage['attempts'] == 2
    fields = lambda r: (r.stock_key, r.outlook, r.action, r.confidence, r.execution_status, r.quantity)
    assert [fields(r) for r in full.recommendations] == [fields(r) for r in fast.recommendations]
