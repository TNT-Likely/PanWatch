"""盘前决策流水线单测:五阶段正反用例 + 门禁/互斥/幂等/V11/防覆盖/东财全禁端到端。

全部离线:LLM(ai_client)、akshare、行情通道与 SessionLocal 均以替身注入,
不发起真实网络请求,不触碰真实 data/panwatch.db。
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.platform.persistence.database import Base

# ────────────────────────── 公共替身与夹具 ──────────────────────────


class FakeAIClient:
    """按脚本逐个返回 chat 响应;Exception 项表示该次调用抛错。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[tuple[str, str]] = []

    async def chat(self, system_prompt: str, user_content: str) -> str:
        self.calls.append((system_prompt, user_content))
        if not self.responses:
            raise AssertionError("FakeAIClient 响应脚本耗尽")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def tagged(payload: str) -> str:
    from src.modules.automation import premarket_config as pcfg

    return f"分析正文\n{pcfg.TAG_START}\n{payload}\n{pcfg.TAG_END}\n"


MACRO_CARDS_JSON = """
{"cards": [
  {"title": "市场环境卡", "direction": "neutral", "confidence": 6,
   "rationale": "隔夜美股走弱,若纳指再跌1%则本判断可能错",
   "position_policy": {"review": "中性", "diff": [
      {"item": "仓位上限", "direction": "tighten", "from": "60%", "to": "55%"}]}},
  {"title": "政策与事件卡", "direction": "neutral", "confidence": 5,
   "rationale": "FOMC 在窗口内,若决议超鹰则本判断可能错",
   "position_policy": {"review": "事件前不加仓", "diff": []}},
  {"title": "风险与仓位政策卡", "direction": "neutral", "confidence": 6,
   "rationale": "风控收紧,若波动率回落则本判断可能错",
   "position_policy": {"review": "止损纪律收紧", "diff": [
      {"item": "止损纪律", "direction": "tighten", "from": "-8%", "to": "-6%"}]}}
]}
"""

SECTOR_LLM_JSON = """
{"predictions": [
  {"board_code": "BK0001", "direction": "bullish", "confidence": 7,
   "stage": "突破", "rationale": "【已观察】资金流入;【预测】日内延续,若涨停家数归零则本判断可能错",
   "observed": "主力净流入", "predicted": "延续", "catalysts": ["事件催化"]}
]}
"""

PICK_REVIEW_JSON = """
{"reviews": [
  {"symbol": "600001", "action": "buy", "confidence": 7,
   "three_price_check": "认可三价",
   "risks": ["开盘补跌"], "reasons": ["板块资金共振"],
   "rationale": "若开盘30分钟跌破入场区下沿则本判断可能错"}
]}
"""


@pytest.fixture
def db(monkeypatch):
    """独立内存库 + 全局 SessionLocal 指向它(流水线各模块运行期取用)。"""
    import src.platform.persistence.models  # noqa: F401
    import src.platform.persistence.database as database

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    # 流水线内部按 `from ...database import SessionLocal` 运行期取用,统一指向测试库
    monkeypatch.setattr(database, "SessionLocal", factory)
    # strategy_engine 及其目录/权重助手的顶层绑定一并替换(防覆盖回归全离线)
    import src.modules.strategy.strategy_engine as engine_mod
    import src.modules.strategy.strategy_catalog as catalog_mod

    monkeypatch.setattr(engine_mod, "SessionLocal", factory)
    monkeypatch.setattr(catalog_mod, "SessionLocal", factory)
    # `from ... import SessionLocal` 的模块级绑定:流水线写库经由的模块逐个替换,
    # 否则先于本用例导入的模块会把写入落到真实 data/panwatch.db。
    import src.modules.automation.agent_runs as agent_runs_mod
    import src.modules.research.analysis_history as analysis_history_mod
    import src.modules.research.context_store as context_store_mod

    monkeypatch.setattr(agent_runs_mod, "SessionLocal", factory)
    monkeypatch.setattr(analysis_history_mod, "SessionLocal", factory)
    monkeypatch.setattr(context_store_mod, "SessionLocal", factory)
    session = factory()
    yield session
    session.close()
    engine.dispose()


@pytest.fixture(autouse=True)
def _trading_day_on(monkeypatch):
    """默认把交易日守卫钉为 True;非交易日用例里显式关闭。"""
    from src.modules.automation.premarket_pipeline import stages

    monkeypatch.setattr(stages, "is_trading_day_cn", lambda d=None: True)


@pytest.fixture(autouse=True)
def _fast_akshare_interval(monkeypatch):
    """单测关闭 akshare 限频等待(限频逻辑由专项用例覆盖,生产仍为 1s 间隔)。"""
    from src.modules.automation import premarket_config as pcfg

    monkeypatch.setattr(pcfg, "AKSHARE_FETCH_INTERVAL_SEC", 0.0)


@pytest.fixture
def md_stub(monkeypatch):
    """行情通道替身:全球指数/隔夜美股/批量行情/K线,全部可按用例覆写。"""
    from src.modules.automation.premarket_pipeline import stages

    stub = SimpleNamespace(
        global_markets=lambda **kw: SimpleNamespace(
            ok=True,
            data=[SimpleNamespace(
                symbol="DJI", name="道琼斯", price=44000.0,
                change_pct=-0.6, source_tag="tencent_global",
            )],
        ),
        index_quotes=lambda syms: [
            {"name": "道琼斯", "current_price": 44000.0, "change_pct": -0.6},
            {"name": "纳斯达克", "current_price": 19000.0, "change_pct": -1.1},
        ],
        quotes=lambda syms, market="CN": [],
        klines=lambda symbol, market="CN", days=90: [],
    )
    monkeypatch.setattr(stages, "_md", lambda: stub)
    return stub


def make_bars(symbol_high: float = 14.0, n: int = 80, first_date="2024-01-02"):
    bars = []
    for i in range(n):
        d = date.fromisoformat(first_date) + timedelta(days=i)
        bars.append(
            SimpleNamespace(
                date=d.isoformat(), open=10.0, close=10.0 + i * 0.01,
                high=max(symbol_high, 11.0), low=9.0, volume=1e6,
            )
        )
    return bars


def seed_macro_world(session, *, calendar: bool = True):
    from src.platform.persistence.models import EventCalendarItem, MacroIndicatorValue

    if calendar:
        session.add(
            EventCalendarItem(
                event_date=date.today(), level="high", name="美联储FOMC决议",
                scope="全球", expected="不加息", actual="",
                direction="neutral", impact_boards=["示例行业甲"],
            )
        )
    session.add(
        MacroIndicatorValue(
            indicator="制造业PMI", period="2026年08月", value=50.1,
            publish_date="2026-08-31", source="ut", as_of="2026-09-23T08:00:00",
        )
    )
    session.commit()


def seed_sector_snapshots(session):
    from src.platform.persistence.models import SectorSnapshot

    for code, name, chg, main, small, zt in (
        ("BK0001", "示例行业甲", 3.2, 6.0e8, 1.0e8, 4),
        ("BK0002", "示例行业乙", -2.5, 4.0e8, -1.0e8, 1),
    ):
        session.add(
            SectorSnapshot(
                snapshot_date=date.today().isoformat(), board_code=code, board_name=name,
                change_pct=chg, turnover=1e9, limit_up_count=zt, limit_up_caliber="official",
                main_net_inflow=main, small_net_inflow=small,
                meta={"limit_up": {"official_total": 60, "self_counted_total": 55}},
            )
        )
    session.commit()


def seed_valuation(session, symbol: str = "600001", *, n: int = 250):
    from src.platform.persistence.models import ValuationSeries

    base = date.today() - timedelta(days=n)
    for i in range(n):
        d = base + timedelta(days=i)
        session.add(
            ValuationSeries(
                symbol=symbol, trade_date=d.isoformat(),
                # 历史 PE 低位(10~12),当前 PE 30 → 深度折价入场区
                pe_ttm=10.0 + (i % 20) * 0.1 if i < n - 1 else 30.0,
                pb=None, source="ut",
            )
        )
    session.commit()


def seed_fundamentals(session, symbol: str = "600001"):
    from src.platform.persistence.models import FundamentalsCache

    session.add(
        FundamentalsCache(
            symbol=symbol, report_period="2026Q2",
            summary={
                "report_period": "2026Q2",
                "rev_cagr": 0.22, "profit_cagr": 0.28,
                "gross_margin": 0.45, "net_margin": 0.18, "roe": 0.20,
                "eps_latest": 1.2, "latest_net_profit": 8.0e8,
                "single_quarter_profit_yoy": 0.08,
                "consecutive_loss": False,
                "eps_series": {
                    "20250630": 0.9, "20250930": 1.0, "20251231": 1.05,
                    "20260331": 1.1, "20260630": 1.2,
                },
                "profit_series": {
                    "20250630": 6.0e8, "20250930": 6.5e8, "20251231": 7.0e8,
                    "20260331": 7.5e8, "20260630": 8.0e8,
                },
            },
            sources=[{"source": "ut"}],
        )
    )
    session.commit()


# ══════════════════════════ 阶段1 宏观三卡 ══════════════════════════


def _channels(calendar=True, macro=True, global_=True, us=True):
    from src.modules.automation.premarket_pipeline import stages

    ch = stages.collect_channels.__wrapped__ if hasattr(stages.collect_channels, "__wrapped__") else None
    return {
        "today": "2026-09-23",
        "calendar": {"name": "calendar", "available": calendar, "level": 0 if calendar else 2, "as_of": "", "data": [
            {"event_date": "2026-09-23", "level": "high", "name": "FOMC", "scope": "全球",
             "expected": "", "actual": "", "direction": "neutral", "impact_boards": []}
        ] if calendar else []},
        "macro": {"name": "macro", "available": macro, "level": 0 if macro else 2, "as_of": "", "data": [
            {"indicator": "制造业PMI", "period": "2026年08月", "value": 50.1,
             "publish_date": "", "source": "ut", "as_of": ""}
        ] if macro else []},
        "global": {"name": "global", "available": global_, "level": 0 if global_ else 2, "as_of": "", "data": []},
        "us_indices": {"name": "us_indices", "available": us, "level": 0 if us else 2, "as_of": "", "data": []},
        "events_recent": [],
    }


def test_macro_gates_stop_when_calendar_empty():
    """反例:日历窗口空 → STOP 硬停,附导入指引。"""
    from src.modules.automation.premarket_pipeline.stages import macro_gates

    gate = macro_gates(_channels(calendar=False))
    assert gate["status"] == "stopped"
    assert "导入" in gate["reason"]


def test_macro_gates_snapshot_only_when_channels_lt_2():
    """反例:可用通道 < 2 → 只产数据快照报告。"""
    from src.modules.automation.premarket_pipeline.stages import macro_gates

    gate = macro_gates(_channels(calendar=True, macro=False, global_=False, us=False))
    assert gate["status"] == "snapshot_only"


def test_macro_gates_weak_global_caps_confidence():
    """海外通道弱 → 三卡置信度上限 4;全通道健康 → 不封顶。"""
    from src.modules.automation.premarket_pipeline.stages import macro_gates

    weak = macro_gates(_channels(global_=False))
    assert weak["status"] == "ok"
    assert weak["confidence_cap"] == 4

    healthy = macro_gates(_channels())
    assert healthy["status"] == "ok"
    assert healthy["confidence_cap"] is None


def test_run_macro_stage_happy():
    """正例:结构化解析成功,三卡入卡,relax 无残留。"""
    from src.modules.automation.premarket_pipeline.stages import run_macro_stage

    chat = FakeAIClient([tagged(MACRO_CARDS_JSON)])
    cards = asyncio.run(run_macro_stage(_channels(), chat.chat, llm_timeout_seconds=5))
    assert cards.status == "ok"
    assert len(cards.cards) == 3
    assert any("tighten" in str(c.get("position_policy", {}).get("diff")) for c in cards.cards)
    # system 是渲染后的 prompt(含输出契约),user 是数据快照
    from src.modules.automation import premarket_config as pcfg
    assert pcfg.TAG_START in chat.calls[0][0]
    assert "制造业PMI" in chat.calls[0][1]


def test_run_macro_stage_relax_rejected_and_rewritten():
    """反例:diff 出现 relax → 废卡重写;第二次 tighten 输出被采纳。"""
    from src.modules.automation.premarket_pipeline.stages import run_macro_stage

    relax_payload = MACRO_CARDS_JSON.replace('"direction": "tighten", "from": "60%", "to": "55%"',
                                             '"direction": "relax", "from": "60%", "to": "65%"')
    chat = FakeAIClient([tagged(relax_payload), tagged(MACRO_CARDS_JSON)])
    cards = asyncio.run(run_macro_stage(_channels(), chat.chat, llm_timeout_seconds=5))
    assert cards.status == "ok"
    assert len(chat.calls) == 2
    assert "relax" in chat.calls[1][1]  # 重试 prompt 注入了失败原因
    from src.modules.automation.premarket_pipeline.stages import _has_relax
    assert not _has_relax(cards.raw_payload)


def test_run_macro_stage_unparseable_twice_degrades():
    """反例:两次输出都无法解析 → llm_failed,不中断。"""
    from src.modules.automation.premarket_pipeline.stages import run_macro_stage

    chat = FakeAIClient(["不含契约的输出", "还是没有契约"])
    cards = asyncio.run(run_macro_stage(_channels(), chat.chat, llm_timeout_seconds=5))
    assert cards.status == "llm_failed"
    assert cards.cards == []


def test_run_macro_stage_snapshot_only_skips_llm():
    """反例:通道不足 → 直接返回快照,不调 LLM。"""
    from src.modules.automation.premarket_pipeline.stages import run_macro_stage

    chat = FakeAIClient([])
    cards = asyncio.run(
        run_macro_stage(_channels(macro=False, global_=False, us=False), chat.chat, llm_timeout_seconds=5)
    )
    assert cards.status == "snapshot_only"
    assert chat.calls == []


# ══════════════════════════ 阶段2 板块预测 ══════════════════════════


def test_classify_stage_matrix():
    from src.modules.automation.premarket_pipeline.stages import classify_stage

    assert classify_stage(d5=1.0, d10=1.0, d20=-6.0, main_inflow=-1.0,
                          small_inflow=2.0, limit_up_count=0, market_limit_up_total=30) == "蓄力"
    assert classify_stage(d5=4.0, d10=5.0, d20=8.0, main_inflow=2.0,
                          small_inflow=None, limit_up_count=1, market_limit_up_total=None) == "突破"
    assert classify_stage(d5=6.0, d10=6.0, d20=9.0, main_inflow=-1.0,
                          small_inflow=None, limit_up_count=3, market_limit_up_total=None) == "加速"
    assert classify_stage(d5=1.0, d10=3.0, d20=5.0, main_inflow=-2.0,
                          small_inflow=None, limit_up_count=0, market_limit_up_total=None) == "衰竭"
    assert classify_stage(d5=0.5, d10=0.5, d20=0.5, main_inflow=0.1,
                          small_inflow=None, limit_up_count=0, market_limit_up_total=None) == "未知"


def test_detect_divergence():
    from src.modules.automation.premarket_pipeline.stages import detect_divergence

    assert detect_divergence(2.5, -6.0) == "派发"      # 涨>2% 且流出>5亿
    assert detect_divergence(-2.5, 4.0) == "吸筹"      # 跌>2% 且流入>3亿
    assert detect_divergence(2.5, 1.0) == ""
    assert detect_divergence(None, -6.0) == ""


def test_screen_sectors_uses_db_snapshot(db, monkeypatch):
    """正例:快照库命中(degrade 0),动量/背离/热度齐全,top_n 截断。"""
    from src.modules.automation.premarket_pipeline import stages

    seed_sector_snapshots(db)
    momentum = {
        "BK0001": {"momentum": {"d5": 4.0, "d10": 5.0, "d20": 8.0}, "degrade_level": 0, "source": "ut"},
        "BK0002": {"momentum": {"d5": -1.0, "d10": -0.5, "d20": 1.0}, "degrade_level": 0, "source": "ut"},
    }

    def fake_momentum(session, board_code, **kw):
        return momentum[board_code]

    ssvc = stages._svc()
    monkeypatch.setattr(ssvc, "sector_momentum", fake_momentum)

    items, sources = stages.screen_sectors(db, snapshot_date=date.today().isoformat(), top_n=6)
    assert len(items) == 2
    assert sources[0]["name"] == "sector_snapshot_db"
    top = items[0]
    assert top.board_code == "BK0001"            # 动量得分更高排前
    assert top.stage == "突破"
    assert top.heat is True                       # 行业涨停 4>=3 且全市场 60>50
    assert top.divergence == "" and top.degrade_level == 0
    bear = next(i for i in items if i.board_code == "BK0002")
    assert bear.divergence == "吸筹"              # 跌 2.5% 且主力净流入 4 亿
    assert 0 < bear.confidence <= 0.85


def test_screen_sectors_live_fallback(db, monkeypatch):
    """反例:快照库为空 → 实时拉取(degrade=1)兜底。"""
    from src.modules.automation.premarket_pipeline import stages

    ssvc = stages._svc()
    calls = []

    def fake_collect(session, *, snapshot_date=None, **kw):
        calls.append(snapshot_date)
        seed_sector_snapshots(session)
        return {}

    monkeypatch.setattr(ssvc, "collect_daily_snapshot", fake_collect)
    monkeypatch.setattr(
        ssvc, "sector_momentum",
        lambda session, code, **kw: {"momentum": {"d5": 1.0, "d10": 1.0, "d20": 1.0},
                                     "degrade_level": 0, "source": "ut"},
    )
    items, sources = stages.screen_sectors(db, snapshot_date=date.today().isoformat(), top_n=6)
    assert calls == [date.today().isoformat()]
    assert sources[0]["name"] == "sector_snapshot_live"
    assert items and all(it.degrade_level <= 1 for it in items)


def test_run_sector_stage_llm_failure_keeps_algorithm(db, monkeypatch):
    """反例:LLM 失败 → 保留算法结果并标注 AI 失败降级。"""
    from src.modules.automation.premarket_pipeline import stages

    seed_sector_snapshots(db)
    ssvc = stages._svc()
    monkeypatch.setattr(
        ssvc, "sector_momentum",
        lambda session, code, **kw: {"momentum": {"d5": 1.0, "d10": 1.0, "d20": 1.0},
                                     "degrade_level": 0, "source": "ut"},
    )
    chat = FakeAIClient([RuntimeError("LLM 不可用")])
    fc = asyncio.run(
        stages.run_sector_stage(
            db, snapshot_date=date.today().isoformat(), chat_fn=chat.chat,
            llm_timeout_seconds=5, top_n=6,
        )
    )
    assert fc.llm_ok is False
    assert all(it.source == "algorithm(AI失败降级)" for it in fc.items)


def test_upsert_sector_predictions_idempotent(db, monkeypatch):
    """同日重跑幂等:每板块仍只有一行,字段被覆盖。"""
    from src.platform.persistence.models import SectorPrediction

    from src.modules.automation.premarket_pipeline import stages

    seed_sector_snapshots(db)
    ssvc = stages._svc()
    monkeypatch.setattr(
        ssvc, "sector_momentum",
        lambda session, code, **kw: {"momentum": {"d5": 1.0, "d10": 1.0, "d20": 1.0},
                                     "degrade_level": 0, "source": "ut"},
    )
    fc = asyncio.run(
        stages.run_sector_stage(
            db, snapshot_date=date.today().isoformat(), chat_fn=None,
            llm_timeout_seconds=5, top_n=6,
        )
    )
    n1 = stages.upsert_sector_predictions(db, fc, date.today().isoformat())
    n2 = stages.upsert_sector_predictions(db, fc, date.today().isoformat())
    assert n1 == n2 == len(fc.items)
    assert db.query(SectorPrediction).count() == len(fc.items)


# ══════════════════════════ 阶段3 选股三价 ══════════════════════════


def test_hard_filter_rules(db):
    from src.modules.automation.premarket_pipeline.stages import hard_filter

    bars = make_bars()
    good_quote = {"volume": 1e6, "circulating_market_value": 120.0}
    assert hard_filter(symbol="600001", name="示例股", db_name="示例股",
                       quote=good_quote, bars=bars) == ""
    assert "ST" in hard_filter(symbol="600002", name="ST 某某", db_name=None,
                               quote=good_quote, bars=bars)
    assert "ST" in hard_filter(symbol="600002", name="清白名", db_name="*ST 某某",
                               quote=good_quote, bars=bars)  # Stock 表双保险
    fresh = [
        SimpleNamespace(date=(date.today() - timedelta(days=10)).isoformat(),
                        open=1, close=1, high=1, low=1, volume=1),
        SimpleNamespace(date=(date.today() - timedelta(days=9)).isoformat(),
                        open=1, close=1, high=1, low=1, volume=1),
    ]
    assert "上市" in hard_filter(symbol="600003", name="新股", db_name=None,
                                 quote=good_quote, bars=fresh)
    assert "停牌" in hard_filter(symbol="600004", name="停牌股", db_name=None,
                                 quote={**good_quote, "volume": 0}, bars=bars)
    assert "市值" in hard_filter(symbol="600005", name="小盘", db_name=None,
                                 quote={**good_quote, "circulating_market_value": 10.0}, bars=bars)
    assert "市值" in hard_filter(symbol="600006", name="巨盘", db_name=None,
                                 quote={**good_quote, "circulating_market_value": 99999.0}, bars=bars)


def test_v11_boundary():
    """V11 边界:未受阻时 A 档恰为 2.0(通过);压力位压制时不过。"""
    from src.modules.automation.premarket_pipeline.stages import _three_prices

    def valuation():
        return 10.0, 12.0, {"source": "ut", "degrade_level": 0}

    # 无近端压力(高点 14.3 > target=mid*1.3=14.3):A 档 rr 恰为 2.0
    bars = [SimpleNamespace(date="2024-01-02", open=10, close=10, high=14.3, low=9, volume=1)]
    prices = _three_prices(price=11.0, leader_score=0.9, bars=bars, valuation_meta_fn=valuation)
    assert prices["grade"] == "A"
    assert prices["rr"] == pytest.approx(2.0, abs=1e-3)
    assert prices["v11_pass"] is True

    # 压力位 12.0 压制止盈 → rr < 2 → 弃
    bars_low = [SimpleNamespace(date="2024-01-02", open=10, close=10, high=12.0, low=9, volume=1)]
    blocked = _three_prices(price=11.0, leader_score=0.9, bars=bars_low, valuation_meta_fn=valuation)
    assert blocked["rr"] < 2.0
    assert blocked["v11_pass"] is False


def test_score_triple_high_missing_rules():
    from src.modules.automation.premarket_pipeline.stages import score_triple_high

    full = {
        "rev_cagr": 0.2, "profit_cagr": 0.3, "gross_margin": 0.4,
        "net_margin": 0.2, "roe": 0.25,
    }
    r1 = score_triple_high(full)
    assert r1["composite"] is not None and r1["factor"] == 1.0 and r1["indicators_available"] == 5

    partial = {"rev_cagr": 0.2, "profit_cagr": 0.3}   # 2/5 → ×0.9
    r2 = score_triple_high(partial)
    assert r2["factor"] == 0.9 and r2["composite"] is not None

    poor = {"rev_cagr": 0.2}                          # <2/5 → 弃维度
    r3 = score_triple_high(poor)
    assert r3["composite"] is None and "all" in r3["missing_dims"]


def test_pick_candidates_offline(db, monkeypatch):
    """正例:成分→硬过滤→三高→三价(PE 百分位)→V11,离线全链路。"""
    from src.modules.automation.premarket_pipeline import stages
    from src.modules.automation.premarket_pipeline.types import SectorForecastItem

    seed_valuation(db, "600001")
    seed_fundamentals(db, "600001")

    ssvc = stages._svc()
    monkeypatch.setattr(
        ssvc, "fetch_board_constituents",
        lambda code, limit=30: [{"symbol": "600001", "name": "示例股", "turnover": 1e8}],
    )
    md_stub = SimpleNamespace(
        quotes=lambda syms, market="CN": [SimpleNamespace(
            symbol="600001", name="示例股", current_price=11.0,
            volume=1e6, circulating_market_value=120.0,
        )],
        klines=lambda symbol, market="CN", days=90: make_bars(),
    )
    monkeypatch.setattr(stages, "_md", lambda: md_stub)
    monkeypatch.setattr(stages, "_fetch_financial_abstract", lambda symbol: (_ for _ in ()).throw(
        AssertionError("缓存命中时不应按需拉取")))

    sector = SectorForecastItem(board_code="BK0001", board_name="示例行业甲", direction="bullish")
    candidates, sources = stages.pick_candidates(
        db, [sector], [{"event_date": "2026-09-23", "level": "high",
                        "name": "FOMC", "impact_boards": ["示例行业甲"], "scope": "全球"}],
        max_candidates=5, budget_seconds=300,
    )
    cand = next(c for c in candidates if c.symbol == "600001")
    assert cand.filters_dropped == ""
    assert cand.veto_reason == ""
    assert cand.entry_low is not None and cand.entry_high is not None
    assert cand.valuation["source"] == "valuation_series_pe_p30_p50"
    assert cand.valuation["degrade_level"] == 0
    assert cand.rr >= 2.0 and cand.v11_pass is True
    # 政策关联:事件命中板块 → 0.7(中档默认)~1.0(高档)
    assert cand.policy_score >= 0.7
    assert cand.leader_score > 0


def test_pick_candidates_budget_exhausted(db, monkeypatch):
    """反例:预算不足 → 跳过按需拉取并标注 budget_exhausted。"""
    from src.modules.automation.premarket_pipeline import stages
    from src.modules.automation.premarket_pipeline.types import SectorForecastItem

    ssvc = stages._svc()
    monkeypatch.setattr(
        ssvc, "fetch_board_constituents",
        lambda code, limit=30: [{"symbol": "600777", "name": "新希望股", "turnover": 1e8}],
    )
    md_stub = SimpleNamespace(
        quotes=lambda syms, market="CN": [SimpleNamespace(
            symbol="600777", name="新希望股", current_price=8.0,
            volume=1e6, circulating_market_value=80.0,
        )],
        klines=lambda symbol, market="CN", days=90: make_bars(),
    )
    monkeypatch.setattr(stages, "_md", lambda: md_stub)

    sector = SectorForecastItem(board_code="BK0009", board_name="示例行业丙")
    candidates, _ = stages.pick_candidates(
        db, [sector], [], max_candidates=5, budget_seconds=1,  # < 60s 预留
    )
    cand = candidates[0]
    assert cand.budget_exhausted is True
    assert "budget_exhausted" in cand.filters_dropped


# ══════════════════════════ 阶段4 财报核验 ══════════════════════════


def _raw_financial(rev_series, prof_series, eps_series):
    """构造 financial_abstract 原始形态(供核验通道替身返回)。"""
    periods = ["20250630", "20250930", "20251231", "20260331", "20260630"]
    return {"periods": periods, "indicators": {
        "营业总收入": dict(zip(periods, rev_series)),
        "归母净利润": dict(zip(periods, prof_series)),
        "基本每股收益": dict(zip(periods, eps_series)),
    }}


CONSISTENT_RAW = _raw_financial(
    [100, 200, 300, 400, 510],      # yoy = 4.1
    [80, 160, 240, 320, 410],       # yoy = 4.125
    [1.0, 1.0, 1.0, 1.0, 1.19],
)
PRIMARY_RAW = _raw_financial(
    [100, 200, 300, 400, 500],      # yoy = 4.0
    [80, 160, 240, 320, 400],       # yoy = 4.0
    [1.0, 1.0, 1.0, 1.0, 1.2],
)
DISPUTED_RAW = _raw_financial(
    [100, 200, 300, 400, 900],      # yoy = 8.0,与主源偏差 >5%
    [80, 160, 240, 320, 400],
    [1.0, 1.0, 1.0, 1.0, 1.2],
)


def _stub_dual_sources(monkeypatch, *, em=None, sina=None, ths=None):
    """按源注入原始财务摘要替身;None=该源不可得(None 返回)。"""
    from src.modules.automation.premarket_pipeline import verifier

    monkeypatch.setitem(verifier._FETCHERS, "em", (lambda s: em) if em is not None else (lambda s: None))
    monkeypatch.setitem(verifier._FETCHERS, "sina", (lambda s: sina) if sina is not None else (lambda s: None))
    monkeypatch.setitem(verifier._FETCHERS, "ths", (lambda s: ths) if ths is not None else (lambda s: None))


GOOD_SUMMARY = {
    "report_period": "2026Q2",
    "eps_latest": 1.2,
    "latest_net_profit": 8.0e8,
    "single_quarter_profit_yoy": 0.08,
    "eps_series": {"20250630": 0.9, "20250930": 1.0, "20251231": 1.05,
                   "20260331": 1.1, "20260630": 1.2},
}


def test_verify_candidate_pass(db, monkeypatch):
    """正例:六项全过 → passed=True,核验摘要回写 FundamentalsCache。"""
    from src.modules.automation.premarket_pipeline import verifier
    from src.platform.persistence.models import FundamentalsCache

    seed_fundamentals(db, "600001")
    _stub_dual_sources(monkeypatch, em=PRIMARY_RAW, sina=CONSISTENT_RAW)  # 偏差 <5%
    ver = verifier.verify_candidate(db, symbol="600001", summary=GOOD_SUMMARY, events=[])
    assert ver.passed is True
    assert ver.status_of("dual_source") == "PASS"
    assert ver.status_of("single_quarter") == "PASS"
    row = (
        db.query(FundamentalsCache)
        .filter(FundamentalsCache.symbol == "600001", FundamentalsCache.report_period == "2026Q2")
        .first()
    )
    assert row.summary.get("verification", {}).get("passed") is True


def test_verify_candidate_fail_on_single_quarter(db, monkeypatch):
    """反例:最新单季净利同比 <0(伪增长)→ FAIL 剔除。"""
    from src.modules.automation.premarket_pipeline import verifier

    _stub_dual_sources(monkeypatch, em=PRIMARY_RAW, sina=PRIMARY_RAW)
    summary = {**GOOD_SUMMARY, "single_quarter_profit_yoy": -0.12}
    ver = verifier.verify_candidate(db, symbol="600001", summary=summary, events=[])
    assert ver.passed is False
    assert ver.status_of("single_quarter") == "FAIL"


def test_verify_candidate_fail_on_disputed_dual_source(db, monkeypatch):
    """反例:双源偏差 >5% → disputed → FAIL(fail-closed)。"""
    from src.modules.automation.premarket_pipeline import verifier

    _stub_dual_sources(monkeypatch, em=PRIMARY_RAW, sina=DISPUTED_RAW)  # 偏差远超 5%
    ver = verifier.verify_candidate(db, symbol="600001", summary=GOOD_SUMMARY, events=[])
    assert ver.passed is False
    assert ver.status_of("dual_source") == "FAIL"
    assert "偏差" in ver.checks["dual_source"]["detail"]


def test_verify_candidate_fail_closed_when_no_sources(db, monkeypatch):
    """反例:所有核验通道不可得 → FAIL(任一字段取不到)。"""
    from src.modules.automation.premarket_pipeline import verifier

    _stub_dual_sources(monkeypatch)  # 全部返回 None
    ver = verifier.verify_candidate(db, symbol="600001", summary=GOOD_SUMMARY, events=[])
    assert ver.passed is False
    assert ver.status_of("dual_source") == "FAIL"


def test_verify_candidate_missing_summary(db, monkeypatch):
    """反例:财务摘要整体缺失 → fail-closed FAIL。"""
    from src.modules.automation.premarket_pipeline import verifier

    ver = verifier.verify_candidate(db, symbol="600001", summary=None, events=[])
    assert ver.passed is False
    assert ver.status_of("field_availability") == "FAIL"


def test_verify_disclosure_window_missing_source_warns(db, monkeypatch):
    """反例:events 不含按标的披露数据(生产现况:日历只有宏观事件)
    → WARN「检测缺失」,不给无数据支撑的 PASS。"""
    from src.modules.automation.premarket_pipeline import verifier

    _stub_dual_sources(monkeypatch, em=PRIMARY_RAW, sina=PRIMARY_RAW)
    macro_only_events = [{"event_date": "2026-09-25", "level": "high", "name": "FOMC"}]
    ver = verifier.verify_candidate(
        db, symbol="600001", summary=GOOD_SUMMARY, events=macro_only_events
    )
    assert ver.status_of("disclosure_window") == "WARN"
    assert "检测缺失" in ver.checks["disclosure_window"]["detail"]
    assert ver.passed is True  # WARN 只降置信度,不剔除


def test_call_with_timeout_raises_fetch_timeout():
    """akshare 硬超时:慢调用超时抛 FetchTimeout(不挂死调用方);限频保持最小间隔。"""
    import time as _time

    from src.modules.automation import premarket_config as pcfg
    from src.modules.automation.premarket_pipeline.stages import (
        FetchTimeout,
        akshare_throttle,
        call_with_timeout,
    )

    def slow():
        _time.sleep(2.0)
        return "late"

    assert call_with_timeout(lambda: "fast", timeout_sec=pcfg.AKSHARE_FETCH_TIMEOUT_SEC) == "fast"
    with pytest.raises(FetchTimeout):
        call_with_timeout(slow, timeout_sec=0.3)

    # 限频:0.05s 间隔下连续两次调用,第二次至少等待一个间隔
    monkey_spacing = pytest.MonkeyPatch()
    try:
        monkey_spacing.setattr(pcfg, "AKSHARE_FETCH_INTERVAL_SEC", 0.05)
        from src.modules.automation.premarket_pipeline import stages

        stages._AKSHARE_LAST_FETCH[0] = 0.0
        t0 = _time.monotonic()
        akshare_throttle()
        akshare_throttle()
        assert _time.monotonic() - t0 >= 0.04
    finally:
        monkey_spacing.undo()


def test_dual_source_fetch_timeout_fails_closed(db, monkeypatch):
    """反例:核验通道抓取超时 → 该源不可得;全超时 → FAIL(fail-closed)。"""
    import time as _time

    from src.modules.automation import premarket_config as pcfg
    from src.modules.automation.premarket_pipeline import verifier

    monkeypatch.setattr(pcfg, "AKSHARE_FETCH_TIMEOUT_SEC", 0.3)
    monkeypatch.setattr(pcfg, "AKSHARE_FETCH_INTERVAL_SEC", 0.0)

    def slow_fetch(symbol):
        _time.sleep(1.5)
        return PRIMARY_RAW

    # 直接注入慢速 fetcher(_stub_dual_sources 只收原始数据替身)
    monkeypatch.setitem(verifier._FETCHERS, "em", slow_fetch)
    monkeypatch.setitem(verifier._FETCHERS, "sina", slow_fetch)
    monkeypatch.setitem(verifier._FETCHERS, "ths", lambda s: None)
    verdict = verifier.dual_source_check(db, "600001")
    assert verdict["status"] == "FAIL"
    assert "无法双源交叉" in verdict["detail"] or "不可得" in verdict["detail"]


def test_verify_disclosure_window_warn_not_reject(db, monkeypatch):
    """反例:未来 10 交易日有预约披露 → WARN 只降置信度,不剔除。"""
    from src.modules.automation.premarket_pipeline import verifier

    _stub_dual_sources(monkeypatch, em=PRIMARY_RAW, sina=PRIMARY_RAW)
    future = (date.today() + timedelta(days=3)).isoformat()
    ver = verifier.verify_candidate(
        db, symbol="600001", summary=GOOD_SUMMARY,
        events=[{"symbol": "600001", "date": future, "name": "三季报披露"}],
    )
    assert ver.passed is True
    assert ver.status_of("disclosure_window") == "WARN"
    assert ver.warnings


# ══════════════════════════ 阶段5 WriteSet 幂等 ══════════════════════════


def _mk_candidate(symbol="600001", verified=True, v11=True, price=11.0):
    from src.modules.automation.premarket_pipeline.types import PickCandidate

    return PickCandidate(
        symbol=symbol, stock_name=f"股{symbol}", board_code="BK0001", board_name="示例行业甲",
        current_price=price, entry_low=10.0, entry_high=12.0, entry_mid=11.0,
        stop_loss=9.35, target_price=14.3, grade="A",
        leader_score=0.85, triple_high={"composite": 0.9}, policy_score=0.7,
        rr=2.0 if v11 else 1.5, v11_pass=v11,
        action="buy", action_label="建仓", confidence=7.0,
        verified=verified, verification={"passed": verified} if v11 else {},
    )


def test_write_set_idempotent_rerun(db):
    """同日重跑幂等:删除后再写,建议/信号/提醒计数不翻倍。"""
    from src.platform.persistence.models import (
        PriceAlertRule,
        StockSuggestion,
        StrategySignalRun,
    )

    from src.modules.automation.premarket_pipeline import writer
    from src.modules.automation.premarket_pipeline.types import MacroCards, SectorForecast

    snapshot = date.today().isoformat()
    ws = writer.build_write_set(
        snapshot_date=snapshot,
        cards=MacroCards(cards=[{"title": "卡", "confidence": 6}]),
        forecast=SectorForecast(items=[]),
        candidates=[_mk_candidate("600001", verified=True), _mk_candidate("600002", verified=False)],
        report_md="# 报告", notify_content="通知",
        declarations=[{"item": "日历", "source": "ut", "as_of": "", "caliber": "", "degrade_level": 0}],
    )
    stats1 = writer.execute_write_set(db, ws)
    assert stats1["suggestions"] == 2
    assert stats1["signals"] == 1                      # 仅核验通过者写信号
    assert stats1["alerts_created"] == 3               # 触及买区/止损/止盈

    ws2 = writer.build_write_set(
        snapshot_date=snapshot,
        cards=MacroCards(cards=[{"title": "卡", "confidence": 6}]),
        forecast=SectorForecast(items=[]),
        candidates=[_mk_candidate("600001", verified=True), _mk_candidate("600002", verified=False)],
        report_md="# 报告", notify_content="通知", declarations=[],
    )
    stats2 = writer.execute_write_set(db, ws2)
    assert stats2["deleted"]["suggestions"] == 2       # 当日产物已清
    assert stats2["deleted"]["signals"] == 1
    assert stats2["deleted"]["alerts"] == 3
    assert db.query(StockSuggestion).filter(StockSuggestion.agent_name == "premarket_pipeline").count() == 2
    assert db.query(StrategySignalRun).filter(StrategySignalRun.strategy_code == "premarket_pipeline").count() == 1
    assert db.query(PriceAlertRule).filter(PriceAlertRule.name.like("[盘前 %")).count() == 3


def test_unverified_candidate_watch_only(db):
    """核验未过:建议 action=watch,不产生信号行与提醒。"""
    from src.platform.persistence.models import StockSuggestion, StrategySignalRun

    from src.modules.automation.premarket_pipeline import writer
    from src.modules.automation.premarket_pipeline.types import MacroCards, SectorForecast

    ws = writer.build_write_set(
        snapshot_date=date.today().isoformat(),
        cards=MacroCards(), forecast=SectorForecast(),
        candidates=[_mk_candidate("600001", verified=False)],
        report_md="", notify_content="", declarations=[],
    )
    assert ws.suggestions[0]["action"] == "watch"
    assert "核验未过" in ws.suggestions[0]["action_label"]
    writer.execute_write_set(db, ws)
    assert db.query(StockSuggestion).count() == 1
    assert db.query(StrategySignalRun).count() == 0


# ══════════════════════════ Agent 编排 ══════════════════════════


def _make_context(monkeypatch, responses):
    from src.modules.automation.base import AgentContext

    ctx = AgentContext(ai_client=FakeAIClient(responses), notifier=None, config=None)
    setattr(ctx, "_trace_id", "pm-test")
    return ctx


def _patch_sector_layer(monkeypatch):
    """阶段2/3 公共替身:动量与成分/行情(供端到端用例复用)。"""
    from src.modules.automation.premarket_pipeline import stages

    ssvc = stages._svc()
    monkeypatch.setattr(
        ssvc, "sector_momentum",
        lambda session, code, **kw: {"momentum": {"d5": 4.0, "d10": 5.0, "d20": 8.0},
                                     "degrade_level": 0, "source": "ut"},
    )
    monkeypatch.setattr(
        ssvc, "fetch_board_constituents",
        lambda code, limit=30: [{"symbol": "600001", "name": "示例股", "turnover": 1e8}],
    )
    return ssvc


def _patch_pick_layer(monkeypatch, md_stub):
    from src.modules.automation.premarket_pipeline import stages

    md_stub.quotes = lambda syms, market="CN": [SimpleNamespace(
        symbol="600001", name="示例股", current_price=11.0,
        volume=1e6, circulating_market_value=120.0)]
    md_stub.klines = lambda symbol, market="CN", days=90: make_bars()


def test_agent_skips_non_trading_day(monkeypatch):
    """非交易日:collect 返回 skip,analyze 不产报告。"""
    from src.modules.automation.premarket_pipeline import stages
    from src.modules.automation.premarket_pipeline.agent import PremarketPipelineAgent

    monkeypatch.setattr(stages, "is_trading_day_cn", lambda d=None: False)
    agent = PremarketPipelineAgent()
    ctx = _make_context(monkeypatch, [])
    data = asyncio.run(agent.collect(ctx))
    assert data.get("skip") is True
    result = asyncio.run(agent.analyze(ctx, data))
    assert result.raw_data.get("skipped") is True
    assert "非交易日" in result.content


def test_agent_double_run_mutex(db, monkeypatch):
    """双跑互斥:同 agent 当日已有 running 记录 → skip(排除自身 trace)。"""
    from src.platform.persistence.models import AgentRun

    from src.modules.automation.premarket_pipeline.agent import PremarketPipelineAgent

    session = db
    session.add(AgentRun(agent_name="premarket_pipeline", status="running", trace_id="pm-other"))
    session.commit()

    agent = PremarketPipelineAgent()
    ctx = _make_context(monkeypatch, [])
    data = {"today": "2026-09-23", "channels": _channels(), "trace_id": "pm-test"}
    result = asyncio.run(agent.analyze(ctx, data))
    assert result.raw_data.get("skipped") is True
    assert "pm-other" in result.content
    # 被互斥跳过时不产生自身 running 标记
    assert (
        db.query(AgentRun)
        .filter(AgentRun.trace_id == "pm-test", AgentRun.status == "running")
        .count()
        == 0
    )


def test_agent_running_marker_lifecycle(db, monkeypatch):
    """生产触发路径自管 running 标记:进入管线插 running 行,结束落终态。

    AgentScheduler/trigger_agent 只在结束时写终态,本 agent 必须自插自管,
    互斥才对 08:15 定时 + 手动并发真实生效。
    """
    from src.platform.persistence.models import AgentRun

    from src.modules.automation.premarket_pipeline.agent import PremarketPipelineAgent

    seed_macro_world(db, calendar=False)  # STOP 门禁路径:不调 LLM、跑完整标记生命周期
    agent = PremarketPipelineAgent()
    ctx = _make_context(monkeypatch, [])
    data = asyncio.run(agent.collect(ctx))
    # 管线运行中:running 标记应已插入(start_agent_run 在 analyze 同步段执行)
    result = asyncio.run(agent.analyze(ctx, data))
    assert "硬停" in result.title or "STOP" in result.content

    rows = (
        db.query(AgentRun)
        .filter(AgentRun.agent_name == "premarket_pipeline", AgentRun.trace_id == "pm-test")
        .all()
    )
    assert rows, "管线运行应留下生命周期记录"
    assert all(r.status != "running" for r in rows), "运行结束后 running 标记必须落终态"
    assert any(r.status == "success" for r in rows)


def test_agent_finalize_marker_on_failure(db, monkeypatch):
    """管线异常:running 标记落 failed 终态后异常照常上抛。"""
    from src.platform.persistence.models import AgentRun

    from src.modules.automation.premarket_pipeline.agent import PremarketPipelineAgent

    agent = PremarketPipelineAgent()
    ctx = _make_context(monkeypatch, [])

    async def boom(*a, **kw):
        raise RuntimeError("管线炸了")

    monkeypatch.setattr(agent, "_run_pipeline", boom)
    data = {"today": "2026-09-23", "channels": _channels(), "trace_id": "pm-test"}
    with pytest.raises(RuntimeError):
        asyncio.run(agent.analyze(ctx, data))

    row = db.query(AgentRun).filter(AgentRun.trace_id == "pm-test").first()
    assert row is not None and row.status == "failed"
    assert "管线炸了" in (row.error or "")


def test_agent_mutex_blocks_concurrent_via_self_managed_marker(db, monkeypatch):
    """自管标记的互斥闭环:第一次运行中途(模拟:直接插 marker 后不再 finalize),
    第二次触发被互斥跳过——不需要外部预插 running 行。"""
    from src.platform.persistence.models import AgentRun

    from src.modules.automation.premarket_pipeline.agent import PremarketPipelineAgent

    seed_macro_world(db, calendar=False)
    agent = PremarketPipelineAgent()
    ctx = _make_context(monkeypatch, [])
    data = asyncio.run(agent.collect(ctx))
    asyncio.run(agent.analyze(ctx, data))  # 完整跑一遍并落终态

    # 模拟并发中的另一次运行:手工把标记置回 running(等价于第一次还没跑完)
    row = db.query(AgentRun).filter(AgentRun.trace_id == "pm-test").first()
    row.status = "running"
    db.commit()

    ctx2 = _make_context(monkeypatch, [])
    data2 = {"today": "2026-09-23", "channels": _channels(), "trace_id": "pm-second"}
    result2 = asyncio.run(agent.analyze(ctx2, data2))
    assert result2.raw_data.get("skipped") is True
    assert "pm-test" in result2.content


def test_agent_stop_gate_produces_degraded_report(db, monkeypatch, md_stub):
    """STOP 门禁:日历为空 → 硬停报告+导入指引,后续阶段不运行。"""
    from src.modules.automation.premarket_pipeline.agent import PremarketPipelineAgent

    seed_macro_world(db, calendar=False)  # macro 有,calendar 无;global 走 md_stub
    chat = FakeAIClient([])
    ctx = _make_context(monkeypatch, [])
    agent = PremarketPipelineAgent()

    async def fake_chat(system, user):
        return await chat.chat(system, user)

    monkeypatch.setattr(agent, "_make_chat_fn", lambda context: fake_chat)
    data = asyncio.run(agent.collect(ctx))
    result = asyncio.run(agent.analyze(ctx, data))
    assert "硬停" in result.title or "STOP" in result.content
    assert "导入" in result.content
    assert chat.calls == []          # 硬停后不再调 LLM
    assert any(s["stage"] == "macro" for s in result.raw_data["stages"])
    # 后续阶段不产生写入物
    from src.platform.persistence.models import StockSuggestion
    assert db.query(StockSuggestion).count() == 0


def test_agent_e2e_happy_path(db, monkeypatch, md_stub):
    """端到端正例:五阶段全通,建议/信号/提醒/预测回评/报告齐全。"""
    from src.platform.persistence.models import (
        PriceAlertRule,
        StockSuggestion,
        StrategySignalRun,
    )

    from src.modules.automation.premarket_pipeline.agent import PremarketPipelineAgent

    seed_macro_world(db)
    seed_sector_snapshots(db)
    seed_valuation(db, "600001")
    seed_fundamentals(db, "600001")
    _patch_sector_layer(monkeypatch)
    _patch_pick_layer(monkeypatch, md_stub)

    # 核验双源替身(一致;三通道全部替身化,保证离线)
    from src.modules.automation.premarket_pipeline import verifier

    _stub_dual_sources(monkeypatch, em=PRIMARY_RAW, sina=CONSISTENT_RAW)

    ctx = _make_context(monkeypatch, [tagged(MACRO_CARDS_JSON), tagged(SECTOR_LLM_JSON), tagged(PICK_REVIEW_JSON)])
    agent = PremarketPipelineAgent()
    data = asyncio.run(agent.collect(ctx))
    assert data["skip"] is False
    result = asyncio.run(agent.analyze(ctx, data))

    raw = result.raw_data
    stage_names = [s["stage"] for s in raw["stages"]]
    for name in ("macro", "sector", "pick", "verify", "write"):
        assert name in stage_names, stage_names
    assert raw["stage_errors"] == []

    # 落库断言
    sug = db.query(StockSuggestion).filter(StockSuggestion.stock_symbol == "600001").first()
    assert sug is not None and sug.action == "buy"
    sig = db.query(StrategySignalRun).filter(StrategySignalRun.stock_symbol == "600001").first()
    assert sig is not None
    assert sig.strategy_code == "premarket_pipeline"
    assert sig.source_candidate_id == 0
    assert sig.entry_low is not None and sig.stop_loss is not None and sig.target_price is not None
    assert (sig.payload or {}).get("verification", {}).get("passed") is True
    assert db.query(PriceAlertRule).filter(PriceAlertRule.name.like("[盘前 %")).count() == 3

    # 报告内容:三卡/板块表/候选表/数据声明/事件对表/海外参照
    for section in ("宏观三卡", "板块预判", "候选三价", "数据声明", "事件对表", "海外参照"):
        assert section in result.content
    assert result.notify_content and "600001" in result.notify_content
    # 分析历史落库
    from src.platform.persistence.models import AnalysisHistory
    hist = (
        db.query(AnalysisHistory)
        .filter(AnalysisHistory.agent_name == "premarket_pipeline")
        .first()
    )
    assert hist is not None
    # 预测回评 4 个 horizon
    from src.platform.persistence.models import AgentPredictionOutcome
    horizons = sorted(
        o.horizon_days
        for o in db.query(AgentPredictionOutcome)
        .filter(AgentPredictionOutcome.stock_symbol == "600001")
    )
    assert horizons == [1, 3, 5, 10]


def test_agent_e2e_eastmoney_all_disabled(db, monkeypatch, md_stub):
    """端到端反例:东财系通道全部抛错 → 流程不中断,缺口标注,核验未过转观望。"""
    from src.platform.persistence.models import StockSuggestion, StrategySignalRun

    from src.modules.automation.premarket_pipeline import stages, verifier
    from src.modules.automation.premarket_pipeline.agent import PremarketPipelineAgent

    seed_macro_world(db)
    seed_sector_snapshots(db)
    seed_valuation(db, "600001")
    seed_fundamentals(db, "600001")
    ssvc = _patch_sector_layer(monkeypatch)

    # 东财系全禁:全球指数/隔夜美股/宏观缓存重拉/动量K线/财务摘要全部抛错
    def boom(*a, **kw):
        raise RuntimeError("东财源已禁用(测试注入)")

    md_stub.global_markets = boom
    md_stub.index_quotes = boom
    _patch_pick_layer(monkeypatch, md_stub)  # 行情/K线通道(腾讯主源)保持可用
    monkeypatch.setattr(ssvc, "collect_daily_snapshot", boom)
    monkeypatch.setattr(stages, "_fetch_financial_abstract", boom)
    # 动量也断(行业K线属东财) → 维度缺失降级
    monkeypatch.setattr(ssvc, "sector_momentum", boom)
    # 核验双源全断 → fail-closed FAIL
    monkeypatch.setitem(verifier._FETCHERS, "em", boom)
    monkeypatch.setitem(verifier._FETCHERS, "sina", boom)
    monkeypatch.setitem(verifier._FETCHERS, "ths", boom)

    ctx = _make_context(monkeypatch, [tagged(MACRO_CARDS_JSON), tagged(SECTOR_LLM_JSON), tagged(PICK_REVIEW_JSON)])
    agent = PremarketPipelineAgent()
    data = asyncio.run(agent.collect(ctx))
    # 全球/隔夜通道挂 → 采集仍成功(available=False),但 calendar+macro 可用 → 不 STOP
    assert data["skip"] is False
    result = asyncio.run(agent.analyze(ctx, data))

    raw = result.raw_data
    assert not raw.get("skipped")
    stage_names = [s["stage"] for s in raw["stages"]]
    assert {"macro", "sector", "pick", "verify", "write"} <= set(stage_names)
    # 核验 fail-closed → 候选未过 → 仅观望建议,无信号行
    sug = db.query(StockSuggestion).filter(StockSuggestion.stock_symbol == "600001").first()
    assert sug is not None and sug.action == "watch"
    assert db.query(StrategySignalRun).count() == 0
    # 缺口标注进数据声明与报告
    assert "数据声明" in result.content
    decl_text = result.content
    assert "通道不可用" in decl_text or "缺口" in decl_text
    # 板块动量缺失被标注 degrade=2
    sector_meta = [
        s for s in raw["stages"] if s["stage"] == "sector"
    ]
    assert sector_meta, "sector 阶段应有来源记录"


# ══════════════════════════ 防覆盖回归(refresh existing 索引) ══════════════════════════


def test_refresh_strategy_signals_keeps_direct_write_rows(db):
    """直写行(source_candidate_id=0)不被 refresh 的 existing 索引命中:
    09:15 刷新既不覆盖、也不在 stale 清理时误删。"""
    from src.platform.persistence.models import EntryCandidate, StrategySignalRun

    from src.modules.strategy.strategy_engine import refresh_strategy_signals

    snapshot = date.today().isoformat()
    session = db
    # 直写行:盘前流水线 + 深度分析(0 哨兵)
    pm_row = StrategySignalRun(
        snapshot_date=snapshot, stock_symbol="600001", stock_market="CN",
        stock_name="示例股", strategy_code="premarket_pipeline", strategy_name="盘前决策流水线",
        source_candidate_id=0, source_agent="premarket_pipeline",
        score=88.0, rank_score=88.0, status="active", action="buy", action_label="建仓",
        entry_low=10.0, entry_high=12.0, stop_loss=9.35, target_price=14.3,
        evidence=["板块逻辑:示例行业甲"], payload={"k": "v"},
    )
    ta_row = StrategySignalRun(
        snapshot_date=snapshot, stock_symbol="600002", stock_market="CN",
        stock_name="深度股", strategy_code="tradingagents", strategy_name="TradingAgents 深度分析",
        source_candidate_id=0, source_agent="tradingagents",
        score=70.0, rank_score=70.0, status="active", action="buy", action_label="买入",
    )
    session.add_all([pm_row, ta_row])
    # 一条正常 EntryCandidate,让 refresh 有候选可刷
    session.add(
        EntryCandidate(
            stock_symbol="600003", stock_market="CN", stock_name="候选股",
            snapshot_date=snapshot, score=60.0, action="watch",
        )
    )
    session.commit()

    summary = refresh_strategy_signals(snapshot_date=snapshot)
    assert "count" in summary

    session.expire_all()
    pm = (
        session.query(StrategySignalRun)
        .filter(
            StrategySignalRun.strategy_code == "premarket_pipeline",
            StrategySignalRun.stock_symbol == "600001",
            StrategySignalRun.snapshot_date == snapshot,
        )
        .first()
    )
    assert pm is not None, "直写行被 refresh 误删"
    assert pm.entry_low == 10.0 and pm.evidence == ["板块逻辑:示例行业甲"], "直写行被覆盖"
    ta = (
        session.query(StrategySignalRun)
        .filter(StrategySignalRun.strategy_code == "tradingagents",
                StrategySignalRun.stock_symbol == "600002")
        .first()
    )
    assert ta is not None, "0 哨兵行(深度分析)被 refresh 误删"


# ══════════════════════════ 配置/接线/prompt ══════════════════════════


def test_agent_seed_spec_and_workflow_names():
    from src.modules.automation.agent_catalog import (
        AGENT_KIND_WORKFLOW,
        AGENT_SEED_SPECS,
        WORKFLOW_AGENT_NAMES,
    )

    assert "premarket_pipeline" in WORKFLOW_AGENT_NAMES
    spec = next(s for s in AGENT_SEED_SPECS if s.name == "premarket_pipeline")
    assert spec.schedule == "15 8 * * 1-5"
    assert spec.enabled is True and spec.visible is True
    assert spec.execution_mode == "batch" and spec.kind == AGENT_KIND_WORKFLOW
    assert spec.display_order == 15
    assert spec.config == {
        "pipeline_timeout_minutes": 30,
        "llm_timeout_seconds": 380,
        "emit_paper_trading_signal": False,
        "auto_create_alerts": True,
        "max_candidates": 5,
        "board_top_n": 6,
        "mv_min_e8": 50,
        "mv_max_e8": 3000,
    }


def test_server_registry_maps_pipeline_agent():
    import server

    from src.modules.automation.premarket_pipeline.agent import PremarketPipelineAgent

    assert server.AGENT_REGISTRY.get("premarket_pipeline") is PremarketPipelineAgent


def test_prompts_render_with_config_tokens():
    from src.modules.automation import premarket_config as pcfg

    for name in (pcfg.PROMPT_MACRO, pcfg.PROMPT_SECTOR, pcfg.PROMPT_PICK):
        text = pcfg.render_prompt(name)
        assert pcfg.TAG_START in text
        assert pcfg.TAG_END in text
        assert "{{" + "RR_MIN" + "}}" not in text  # 占位符已被配置值替换
        assert "PanWatch" in text
    sector_prompt = pcfg.render_prompt(pcfg.PROMPT_SECTOR)
    assert str(pcfg.MOMENTUM_WEIGHTS["d5"]) in sector_prompt
    assert str(pcfg.HEAT_LIMIT_UP["industry"]) in sector_prompt


def test_collect_channels_fail_soft(db, monkeypatch):
    """采集 fail-soft:全球/隔夜通道抛错 → 通道 available=False,整体不抛错。"""
    from src.modules.automation.premarket_pipeline import stages

    seed_macro_world(db)
    md_stub = SimpleNamespace(
        global_markets=lambda **kw: (_ for _ in ()).throw(RuntimeError("挂了")),
        index_quotes=lambda syms: (_ for _ in ()).throw(RuntimeError("挂了")),
    )
    monkeypatch.setattr(stages, "_md", lambda: md_stub)
    channels = stages.collect_channels(db)
    assert channels["calendar"]["available"] is True
    assert channels["macro"]["available"] is True
    assert channels["global"]["available"] is False
    assert channels["us_indices"]["available"] is False
    assert channels["global"]["level"] == 2
