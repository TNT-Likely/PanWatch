"""盘前决策流水线的数据类型(纯 dataclass,无 IO,便于单测)。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class MacroCards:
    """阶段1 宏观三卡产出。

    cards: [{title, direction, confidence, rationale, position_policy:{...,
        diff:[{item, direction(tighten|relax), from, to}]}} ...]
    status: ok / snapshot_only(可用通道不足) / stopped(日历窗口空)
    """

    cards: list[dict] = field(default_factory=list)
    status: str = "ok"
    confidence_cap: int | None = None      # 事件/海外通道弱时的置信度上限
    gate_reason: str = ""                  # STOP / 降级原因(给人看)
    raw_payload: dict = field(default_factory=dict)
    sources: list[dict] = field(default_factory=list)


@dataclass
class SectorForecastItem:
    """单个板块的方向预测(算法初筛 + LLM 修正)。"""

    board_code: str
    board_name: str = ""
    direction: str = "neutral"             # bullish / bearish / neutral
    confidence: float = 0.0                # [0,1]
    stage: str = ""                        # 蓄力/突破/加速/衰竭/未知
    momentum_score: float | None = None    # d5/d10/d20 加权动量
    rationale: str = ""
    catalysts: list[str] = field(default_factory=list)
    divergence: str = ""                   # 派发/吸筹/""(背离标记)
    heat: bool = False                     # 涨停热度达标
    degrade_level: int = 0                 # 0=快照库 1=实时拉 2=维度缺失
    source: str = ""                       # algorithm / llm+algorithm / algorithm(AI失败降级)
    observed_labels: list[str] = field(default_factory=list)  # 【已观察】标记
    predicted_labels: list[str] = field(default_factory=list)  # 【预测】标记


@dataclass
class SectorForecast:
    """阶段2 产出:初筛 + 修正后的板块预测集合。"""

    items: list[SectorForecastItem] = field(default_factory=list)
    sources: list[dict] = field(default_factory=list)
    llm_ok: bool = False                   # LLM 修正是否成功(False=纯算法降级)


@dataclass
class PickCandidate:
    """阶段3 选股三价候选(含三高评分与 V11 结论)。"""

    symbol: str
    stock_name: str = ""
    board_code: str = ""
    board_name: str = ""
    current_price: float | None = None
    entry_low: float | None = None
    entry_high: float | None = None
    entry_mid: float | None = None
    stop_loss: float | None = None
    target_price: float | None = None
    grade: str = "C"                       # A/B/C → 对应止损
    leader_score: float = 0.0              # 龙头总分(0-1)
    triple_high: dict = field(default_factory=dict)   # 各维度得分与明细
    policy_score: float = 0.0
    valuation: dict = field(default_factory=dict)     # {source, pe_percentile, degrade_level, ...}
    rr: float | None = None                # V11 盈亏比
    v11_pass: bool = False
    veto_reason: str = ""                  # 一票否决原因(非空即弃)
    filters_dropped: str = ""              # 硬过滤淘汰原因(非空即弃)
    missing_dims: list[str] = field(default_factory=list)  # 三高缺项维度
    risks: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    action: str = "watch"
    action_label: str = "观望"
    confidence: float = 0.0                # 0-10(prompt 公式)
    final_review: dict = field(default_factory=dict)  # LLM 终评结果
    llm_ok: bool = False                   # 终评是否成功(失败=算法结果+标注)
    budget_exhausted: bool = False         # 财务拉取预算耗尽标注
    fundamentals: dict = field(default_factory=dict)  # 财务摘要(核验复用,避免二次拉取)
    verified: bool = False                 # 阶段4 核验通过
    verification: dict = field(default_factory=dict)  # EarningsVerification 摘要


@dataclass
class EarningsVerification:
    """阶段4 财报核验结果(fail-closed:任一 FAIL 即整体未过)。"""

    symbol: str
    report_period: str = ""
    checks: dict = field(default_factory=dict)   # {check_name: {status, detail}}
    passed: bool = False
    warnings: list[str] = field(default_factory=list)
    summary: dict = field(default_factory=dict)  # 写回 FundamentalsCache 的摘要
    sources: list[dict] = field(default_factory=list)

    def status_of(self, name: str) -> str:
        item = self.checks.get(name) or {}
        return str(item.get("status") or "")


@dataclass
class AlertPlan:
    """单只候选的三条提醒规则参数(写入前组装,便于审查)。"""

    stock_symbol: str
    stock_name: str = ""
    rules: list[dict] = field(default_factory=list)  # [{name, op, value, kind}]


@dataclass
class WriteSet:
    """阶段5 两段式写入的全部写入物(先组装审查,再单事务执行)。"""

    snapshot_date: str = ""
    suggestions: list[dict] = field(default_factory=list)
    signals: list[dict] = field(default_factory=list)
    alert_plans: list[AlertPlan] = field(default_factory=list)
    prediction_outcomes: list[dict] = field(default_factory=list)
    report_md: str = ""
    notify_content: str = ""
    title: str = ""
    declarations: list[dict] = field(default_factory=list)  # 数据声明(source/as_of/口径/degrade)
    alert_errors: list[str] = field(default_factory=list)   # 提醒创建失败(不阻断)
