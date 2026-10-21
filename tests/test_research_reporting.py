"""Projection checks use fixed research results, never call a model or rewrite history."""
from copy import deepcopy
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from src.modules.portfolio.api.history import get_history_detail, list_history
from src.modules.research.orchestration.models import ResearchRun
from src.modules.research.orchestration.reporting import build_report
from src.platform.persistence.database import Base
from src.platform.persistence.models import AnalysisHistory


@pytest.fixture
def sample():
    keys = ["CN:12345", "HK:12345"]
    sources, claims, plans = [], [], []
    for key, name in zip(keys, ["A 股样本", "港股样本"]):
        sources.append({"id": key + "/quote", "stock_key": key, "domain": "technical", "source": "fixture",
            "available_at": "2026-10-19T10:00:00Z", "content": {"quote": {"name": name, "current_price": 100, "change_pct": 0},
                "indices": [{"name": "样本指数", "current_price": 3000, "change_pct": 0}], "sources": {"quote": "fixture"}}})
        claims.append({"id": key + "/accepted", "stock_key": key, "statement": key + " 技术结论", "evidence_ids": [key + "/quote"]})
        plans.append({"stock_key": key, "outlook": "bullish", "confidence": .7, "action": "buy", "reason": key + " 最终理由",
            "triggers": [key + " 放量确认"], "invalidations": [key + " 跌破支撑"], "risks": [key + " 波动风险"],
            "execution_status": "blocked", "execution_constraints": ["insufficient_funds"]})
    rejected = {**claims[0], "id": "rejected", "statement": "未采纳的错误判断"}
    artifacts = [
        {"id": "0/0/technical", "epoch": 0, "role": "technical", "model": "fixture", "evidence": sources,
         "report": {"summary": "完整研究过程", "claims": [*claims, rejected]}},
        {"id": "0/0/manager", "epoch": 0, "role": "manager", "model": "fixture", "evidence": [],
         "report": {"summary": "复核裁定", "accepted_claim_ids": [c["id"] for c in claims]}},
    ]
    return keys, {"completion": "complete", "recommendations": plans, "artifacts": artifacts, "usage": {"attempts": 2}}


@pytest.mark.parametrize("agent,focus", [("daily_report", "明日关注"), ("premarket_outlook", "今日关注"), ("intraday_monitor", "后续关注")])
def test_report_recovers_readable_sections_without_changing_plan(sample, agent, focus):
    keys, research = sample
    before = deepcopy(research)
    report = build_report(agent, research, scope=keys, as_of="2026-10-19T10:00:00Z")
    content = report["content"]
    assert f"## {focus}" in content
    assert "## 个股分析" in content and "## 风险提示" in content
    assert content.count("样本指数") == 1
    assert "+0.00%" in content and "100.00" in content  # zero is real data
    assert "A 股样本（CN:12345）" in content and "港股样本（HK:12345）" in content
    assert "条件性建议：买入" in content and "执行受限" in content and "看多" in content
    assert "未采纳的错误判断" not in content and "完整研究过程" not in content
    assert "Models:" not in content and "Usage:" not in content
    assert report["process"][0]["content"] == "完整研究过程"
    assert len(report["evidence"]) == 2 and research == before
    cn_section = report["body"].split("### A 股样本")[1].split("### 港股样本")[0]
    assert "HK:12345 技术结论" not in cn_section


def test_incomplete_results_remain_drafts_and_missing_market_is_explicit(sample):
    keys, research = sample
    research.update(completion="budget_exhausted", reason="test budget", recommendations=[])
    for e in research["artifacts"][0]["evidence"]:
        e["content"].pop("indices")
    report = build_report("daily_report", research, scope=keys)
    assert "研究预算耗尽" in report["content"] and "未发布正式建议或预测" in report["content"]
    assert "本次未采集大盘指数" in report["content"]
    assert "尚无有效投研结论" in report["content"] and "草案中的待核实条件" in report["content"]


def test_projection_uses_original_language_and_ignores_stale_manager(sample):
    keys, research = sample
    research["artifacts"][0].update(id="0/1/technical", epoch=1)
    report = build_report("daily_report", research, scope=keys, language="en-US")
    assert "## Market overview" in report["content"] and "## Next-session watchlist" in report["content"]
    assert "CN:12345 技术结论" not in report["content"]


def test_old_graph_history_is_projected_consistently_without_database_writes(sample):
    keys, research = sample
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        now = datetime.now(timezone.utc)
        db.add(ResearchRun(id="old-run", trace_id="old-trace", agent_name="daily_report", scope_hash="scope",
            engine="langgraph", as_of="2026-10-19T10:00:00Z", payload=research))
        raw = {"engine": "langgraph", "research_run_id": "old-run", "scope": keys}
        row = AnalysisHistory(agent_name="daily_report", stock_symbol="*", analysis_date="2026-10-19",
            content="## 投研结论\nold immutable text", title="old title", raw_data=raw, created_at=now, updated_at=now)
        legacy = AnalysisHistory(agent_name="premarket_outlook", stock_symbol="*", analysis_date="2026-10-18",
            content="一、大盘概览\nlegacy text", raw_data={}, created_at=now, updated_at=now)
        db.add_all([row, legacy]); db.commit()
        detail = get_history_detail(row.id, db)
        listing = list_history(kind="all", limit=30, db=db)
        assert detail.content == listing[0].content
        assert detail.research_report == listing[0].research_report
        assert "明日关注" in detail.content
        assert listing[1].research_report is None and listing[1].content == legacy.content
        db.expire_all()
        assert db.get(AnalysisHistory, row.id).content == "## 投研结论\nold immutable text"
        assert db.get(AnalysisHistory, row.id).raw_data == raw
    engine.dispose()


def test_zero_artifact_budget_failure_also_gets_a_readable_draft_report():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        db.add(ResearchRun(id="budget-run", trace_id="trace", agent_name="daily_report", scope_hash="scope",
            engine="langgraph", as_of="2026-10-19T10:00:00Z", payload={"completion": "budget_exhausted", "artifacts": [], "recommendations": []}))
        row = AnalysisHistory(agent_name="daily_report", stock_symbol="*", analysis_date="2026-10-19", content="## 投研结论",
            raw_data={"engine": "langgraph", "research_run_id": "budget-run", "scope": ["CN:600519"]})
        db.add(row); db.commit()
        result = get_history_detail(row.id, db)
        assert result.research_report and "未发布正式建议或预测" in result.content
        assert "尚无有效投研结论" in result.content and not result.research_report["process"]
    engine.dispose()


def test_graph_metadata_and_evidence_previews_preserve_rounds_and_safe_links(sample):
    keys, research = sample
    old = deepcopy(research['artifacts'][0])
    research['artifacts'][0].update(id='0/1/technical', epoch=1)
    research['artifacts'].append(old)
    research['artifacts'][1]['report'].update(verdict='evidence_needed', issues=[{'domain': 'technical'}])
    research['artifacts'][0]['evidence'].append({'id': keys[0] + '/news', 'stock_key': keys[0], 'domain': 'events',
        'source': 'fixture', 'available_at': '2026-10-19T10:00:00Z', 'content': {'items': [
            {'title': 'Safe source', 'source': 'fixture', 'url': 'https://example.com/news'},
            {'title': 'Unsafe URL', 'url': 'javascript:alert(1)'},
        ]}})
    report = build_report('daily_report', research, scope=keys, elapsed_seconds=82.4)
    assert report['version'] == 3 and report['runtime']['elapsed_seconds'] == 82.4
    assert report['runtime']['stock_count'] == 2
    technical = [p for p in report['process'] if p['role'] == 'technical']
    assert [p['active'] for p in technical] == [False, True]
    assert next(p for p in report['process'] if p['role'] == 'manager')['feedback_roles'] == ['technical']
    news = next(e for e in report['evidence'] if e['domain'] == 'events')
    assert news['stock_key'] == keys[0] and news['providers'] == ['fixture']
    assert '2 条新闻与公告' in news['summary']
    assert news['items'][0]['url'] == 'https://example.com/news' and news['items'][1]['url'] == ''
    assert 'javascript:' not in news['content']


def test_cached_v1_report_gets_graph_metadata_without_rewriting_its_text(sample):
    keys, research = sample
    engine = create_engine('sqlite://')
    Base.metadata.create_all(engine)
    with Session(engine) as db:
        cached = {'version': 1, 'content': '## Market overview\nOriginal reader report', 'process': [], 'evidence': [], 'models': [], 'usage': {}}
        raw = {'engine': 'langgraph', 'research_run_id': 'cached-run', 'scope': keys, 'research_report': cached}
        row = AnalysisHistory(agent_name='daily_report', stock_symbol='*', analysis_date='2026-10-19', content=cached['content'], raw_data=raw)
        db.add(ResearchRun(id='cached-run', trace_id='trace', agent_name='daily_report', scope_hash='scope',
            engine='langgraph', as_of='2026-10-19T10:00:00Z', payload=research,
            created_at=datetime(2026, 10, 19, 10, 0), finished_at=datetime(2026, 10, 19, 10, 1, 22)))
        db.add(row); db.commit()
        result = get_history_detail(row.id, db)
        assert result.research_report['version'] == 3 and result.research_report['body'] == cached['content']
        assert result.research_report['runtime']['elapsed_seconds'] == 82
        assert result.research_report['process'][0]['role'] == 'technical'
        db.expire_all()
        assert db.get(AnalysisHistory, row.id).raw_data == raw
        db.query(ResearchRun).delete(); db.commit()
        assert get_history_detail(row.id, db).research_report == cached
    engine.dispose()


def test_actions_lead_reports_and_plain_notifications_without_changing_directions(sample):
    from src.platform.notifications.notifier import sanitize_for_telegram
    from src.modules.research.signals.actions import normalize_suggestion
    keys, research = sample
    research['recommendations'][0]['action'] = 'watch'
    research['recommendations'][1]['action'] = 'avoid'
    before = deepcopy(research)
    report = build_report('daily_report', research, scope=keys)
    assert [r['action'] for r in report['actions']] == ['watch', 'avoid']
    assert all(r['execution_status'] == 'not_applicable' for r in report['actions'])
    assert report['content'].index('## 操作建议') < report['content'].index('## 大盘概览')
    assert '执行受限' not in report['content'] and '无需下单' in report['content']
    plain = sanitize_for_telegram(report['content'])
    assert plain.index('操作建议：观望') < plain.index('大盘概览')
    assert '操作建议：回避' in plain and keys[0] + ' 放量确认' in plain
    assert research == before and normalize_suggestion(research['recommendations'][0])['execution_status'] == 'not_applicable'


def test_incomplete_summary_never_promotes_drafts_to_action_cards(sample):
    keys, research = sample
    research['completion'] = 'risk_rejected'
    report = build_report('daily_report', research, scope=keys)
    assert report['actions'] == []
    assert '暂无正式操作建议' in report['action_summary'] and '草案' in report['body']


def test_new_session_notes_render_scenarios_attribution_and_no_hindsight(sample):
    keys, research = sample
    research['artifacts'].append({'id': 'portfolio/0/strategy', 'epoch': 0, 'role': 'strategy', 'model': 'fixture', 'evidence': [],
        'report': {'summary': 'Strategy', 'session_notes': [{'stock_key': key, 'summary': 'Evidence-backed attribution',
            'upside': 'Resistance confirms -> reassess', 'downside': 'Support fails -> risk review', 'prior_verdict': 'no_prior'} for key in keys]}})
    pre = build_report('premarket_outlook', research, scope=keys)
    post = build_report('daily_report', research, scope=keys)
    assert pre['content'].count('**开盘情景') == 2 and 'Resistance confirms' in pre['content'] and 'Support fails' in pre['content']
    assert post['content'].count('**收盘归因') == 2 and '未提供同日盘前计划，不作兑现评价' in post['content']
    english = build_report('daily_report', research, scope=keys, language='en-US')
    assert 'Session attribution' in english['content'] and 'no fulfilment assessment' in english['content']
