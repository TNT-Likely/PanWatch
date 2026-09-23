"""盘前影子周报:数据源可用率聚合 / 基线对比 / 渲染与 main 入口(内存库,离线可跑)。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.platform.persistence.database import Base
from src.platform.persistence.models import (
    AgentPredictionOutcome,
    AnalysisHistory,
    SectorPrediction,
    SectorSnapshot,
)

WINDOW = ("2026-09-14", "2026-09-20")


@pytest.fixture
def db():
    import src.platform.persistence.models  # noqa: F401

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    session = factory()
    yield session
    session.close()
    engine.dispose()


# ────────────────────────── 纯函数:数据源可用率 ──────────────────────────


def _history(day, stages):
    return SimpleNamespace(raw_data={"stages": stages}, analysis_date=day)


def test_aggregate_source_availability_counts_ok_and_degrade():
    """按 source 聚合 ok 率与最近降级级别;跨运行累计。"""
    from scripts.pipeline_shadow_report import aggregate_source_availability

    histories = [
        _history(
            "2026-09-15",
            [
                {"stage": "macro", "elapsed_ms": 100, "sources": [
                    {"name": "macro_db", "ok": True, "degrade_level": 0},
                    {"name": "global_live", "ok": False, "degrade_level": 3},
                ]},
                {"stage": "sector", "elapsed_ms": 50, "sources": [
                    {"name": "sector_snapshot_db", "ok": True, "degrade_level": 0},
                ]},
            ],
        ),
        _history(
            "2026-09-16",
            [
                {"stage": "macro", "elapsed_ms": 90, "sources": [
                    # 同名 source 第二次出现且恢复 ok:last_degrade_level 刷新为 0
                    {"name": "global_live", "ok": True, "degrade_level": 0},
                ]},
            ],
        ),
        SimpleNamespace(raw_data=None, analysis_date="2026-09-17"),  # 无 raw_data,跳过
        SimpleNamespace(raw_data={"stages": "junk"}, analysis_date="2026-09-17"),  # 结构不符,跳过
        # 含非 dict 阶段条目:跳过坏条目,正常条目照常聚合
        _history("2026-09-18", [
            "not-a-dict",
            {"stage": "write", "elapsed_ms": 5, "sources": [
                {"name": "write_db", "ok": True, "degrade_level": 0},
            ]},
        ]),
    ]
    result = aggregate_source_availability(histories)
    assert result["runs_total"] == 5
    assert result["runs_with_stages"] == 3

    sources = result["sources"]
    assert set(sources) == {"macro_db", "global_live", "sector_snapshot_db", "write_db"}
    assert sources["global_live"] == {
        "total": 2, "ok": 1, "ok_rate": 0.5, "last_degrade_level": 0,
        "stages": "macro",
    }
    assert sources["macro_db"]["ok_rate"] == 1.0
    assert sources["sector_snapshot_db"]["stages"] == "sector"
    assert sources["write_db"]["ok_rate"] == 1.0


def test_aggregate_source_availability_empty():
    """空历史:总次数为 0,sources 为空,不抛错。"""
    from scripts.pipeline_shadow_report import aggregate_source_availability

    result = aggregate_source_availability([])
    assert result == {"runs_total": 0, "runs_with_stages": 0, "sources": {}}


# ────────────────────────── 纯函数:基线对比 ──────────────────────────


def test_build_baseline_comparison_delta_and_none_safety():
    """按 horizon 对比命中率;任一侧样本不足时差值为 None,不编造。"""
    from scripts.pipeline_shadow_report import BASELINE_AGENT_NAME, build_baseline_comparison
    from src.modules.automation import premarket_config as pcfg

    assert BASELINE_AGENT_NAME == "premarket_outlook"

    pipeline = {
        "suggestion_count": 5,
        "pending_count": 2,
        "insufficient_sample": True,
        "horizons": {
            "1": {"completed_count": 3, "hit_rate": 0.6667},
            "3": {"completed_count": 0, "hit_rate": None},
        },
    }
    baseline = {
        "suggestion_count": 9,
        "pending_count": 1,
        "insufficient_sample": True,
        "horizons": {
            "1": {"completed_count": 8, "hit_rate": 0.5},
            "5": {"completed_count": 6, "hit_rate": 0.45},
        },
    }
    result = build_baseline_comparison(pipeline, baseline)
    assert result["pipeline_suggestion_count"] == 5
    assert result["baseline_suggestion_count"] == 9
    horizons = result["horizons"]
    assert set(horizons) == {"1", "3", "5"}
    assert horizons["1"]["delta_hit_rate"] == pytest.approx(0.1667)
    assert horizons["3"]["delta_hit_rate"] is None  # 基线无 3 日
    assert horizons["3"]["pipeline_hit_rate"] is None
    assert horizons["5"]["pipeline_completed"] == 0
    assert horizons["5"]["delta_hit_rate"] is None
    # 流水线 horizon 键以 pcfg.PREDICTION_HORIZONS 为口径写入对比之外不影响
    assert pcfg.PREDICTION_HORIZONS == (1, 3, 5, 10)


# ────────────────────────── build_report + 渲染(内存库) ──────────────────────────


def _seed_week(db):
    """一周样本:09-15 命中日、09-16 未命中日;流水线与基线 outcomes;运行历史。"""
    db.add_all(
        [
            SectorPrediction(snapshot_date="2026-09-15", board_code="BK1", board_name="半导体", direction="bullish", momentum_score=2.0),
            SectorPrediction(snapshot_date="2026-09-15", board_code="BK2", board_name="银行", direction="bullish", momentum_score=1.0),
            SectorPrediction(snapshot_date="2026-09-16", board_code="BK3", board_name="券商", direction="bullish", momentum_score=2.0),
            SectorSnapshot(snapshot_date="2026-09-15", board_code="BK1", board_name="半导体", change_pct=3.1),
            SectorSnapshot(snapshot_date="2026-09-15", board_code="BK4", board_name="煤炭", change_pct=2.0),
            SectorSnapshot(snapshot_date="2026-09-15", board_code="BK5", board_name="电力", change_pct=1.0),
            SectorSnapshot(snapshot_date="2026-09-16", board_code="BK6", board_name="汽车", change_pct=2.5),
            SectorSnapshot(snapshot_date="2026-09-16", board_code="BK7", board_name="家电", change_pct=1.5),
            SectorSnapshot(snapshot_date="2026-09-16", board_code="BK8", board_name="食品", change_pct=0.5),
        ]
    )
    db.add_all(
        [
            AgentPredictionOutcome(
                agent_name="premarket_pipeline", stock_symbol="600001", stock_market="CN",
                prediction_date="2026-09-15", horizon_days=1, outcome_status="evaluated",
                action="buy", outcome_return_pct=2.0,
            ),
            AgentPredictionOutcome(
                agent_name="premarket_pipeline", stock_symbol="600002", stock_market="CN",
                prediction_date="2026-09-15", horizon_days=5, outcome_status="pending", action="buy",
            ),
            AgentPredictionOutcome(
                agent_name="premarket_outlook", stock_symbol="600100", stock_market="CN",
                prediction_date="2026-09-15", horizon_days=1, outcome_status="evaluated",
                action="buy", outcome_return_pct=-1.0,
            ),
        ]
    )
    db.add_all(
        [
            AnalysisHistory(
                agent_name="premarket_pipeline", stock_symbol="*", analysis_date="2026-09-15",
                content="r1", title="t",
                raw_data={"stages": [
                    {"stage": "macro", "elapsed_ms": 10, "sources": [
                        {"name": "macro_db", "ok": True, "degrade_level": 0},
                        {"name": "us_live", "ok": False, "degrade_level": 3},
                    ]},
                ]},
            ),
            AnalysisHistory(
                agent_name="premarket_pipeline", stock_symbol="*", analysis_date="2026-09-16",
                content="r2", title="t",
                raw_data={"stages": [
                    {"stage": "sector", "elapsed_ms": 20, "sources": [
                        {"name": "sector_snapshot_db", "ok": True, "degrade_level": 0},
                    ]},
                ]},
            ),
        ]
    )
    db.commit()


def test_build_report_and_render_markdown(db):
    """build_report 聚合三段数据;渲染含命中标记/趋势表/基线对比/可用率。"""
    from scripts.pipeline_shadow_report import build_report, render_markdown

    _seed_week(db)
    report = build_report(db, *WINDOW)

    sector = report["sector_hit_rate"]["summary"]
    assert sector["days_evaluated"] == 2
    assert sector["picks_total"] == 3
    assert sector["picks_hit"] == 1  # 09-15 的 BK1 命中
    horizons = report["three_price"]["horizons"]
    assert horizons["1"]["total"] == 1 and horizons["1"]["hit_count"] == 1
    comparison = report["baseline_comparison"]
    assert comparison["pipeline_suggestion_count"] == 2
    assert comparison["baseline_suggestion_count"] == 1
    assert comparison["horizons"]["1"]["baseline_hit_rate"] == 0.0
    assert report["source_availability"]["runs_with_stages"] == 2

    md = render_markdown(report)
    assert f"盘前决策流水线影子周报({WINDOW[0]} ~ {WINDOW[1]})" in md
    assert "命中率 33.3%" in md  # 1/3
    assert "半导体✅" in md and "券商❌" in md  # 逐日预测与命中标记
    assert "累计命中率" in md
    assert "premarket_outlook" in md and "差值" in md
    assert "样本不足" in md  # 基线 1 样本 + 空 horizon 不编造
    assert "| macro_db |" in md and "| us_live |" in md
    assert "降级语义" in md
    assert "人工" in md  # 开启模拟盘需人工决策


def test_render_markdown_empty_report_marks_insufficient_sample(db):
    """空库周报:各段显式标注,无趋势表,不出现编造的百分比。"""
    from scripts.pipeline_shadow_report import build_report, render_markdown

    report = build_report(db, *WINDOW)
    md = render_markdown(report)
    assert "无可评估样本" in md
    assert "区间内无阶段明细" in md
    assert "✅" not in md and "❌" not in md


# ────────────────────────── 窗口解析与 main 入口 ──────────────────────────


def test_resolve_window_explicit_and_backfill():
    from scripts.pipeline_shadow_report import resolve_window

    args = SimpleNamespace(from_="2026-09-14", to="2026-09-20", days=7)
    assert resolve_window(args) == WINDOW

    args = SimpleNamespace(from_=None, to="2026-09-20", days=3)
    assert resolve_window(args) == ("2026-09-18", "2026-09-20")

    args = SimpleNamespace(from_="2026-09-30", to="2026-09-20", days=7)
    with pytest.raises(SystemExit):
        resolve_window(args)


def test_main_writes_markdown_file(db, monkeypatch, tmp_path):
    """main():替身 SessionLocal 指向内存库;--out 落文件;退出码 0。"""
    import scripts.pipeline_shadow_report as shadow

    _seed_week(db)
    monkeypatch.setattr(shadow, "SessionLocal", lambda: db)
    monkeypatch.setattr(shadow, "init_db", lambda: None)

    out = tmp_path / "reports" / "shadow.md"  # 父目录不存在也应创建
    assert shadow.main(["--from", WINDOW[0], "--to", WINDOW[1], "--out", str(out)]) == 0
    text = out.read_text(encoding="utf-8")
    assert "盘前决策流水线影子周报" in text
    assert "数据源可用率" in text


def test_main_stdout_default(db, monkeypatch, capsys):
    """缺省输出到 stdout。"""
    import scripts.pipeline_shadow_report as shadow

    monkeypatch.setattr(shadow, "SessionLocal", lambda: db)
    monkeypatch.setattr(shadow, "init_db", lambda: None)
    assert shadow.main(["--from", WINDOW[0], "--to", WINDOW[1]]) == 0
    captured = capsys.readouterr()
    assert "盘前决策流水线影子周报" in captured.out
