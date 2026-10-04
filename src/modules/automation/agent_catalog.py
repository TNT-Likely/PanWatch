"""Agent catalog and kind helpers.

Workflow agents are user-facing, schedulable pipelines.
Capability agents are internal/manual tools and should not be auto-scheduled.
"""

from __future__ import annotations

from dataclasses import dataclass


AGENT_KIND_WORKFLOW = "workflow"
AGENT_KIND_CAPABILITY = "capability"

WORKFLOW_AGENT_NAMES: tuple[str, ...] = (
    "premarket_outlook",
    "intraday_monitor",
    "daily_report",
)

# Retired identifiers reject old imports, filter obsolete suggestions, and
# classify historical reports; they have no runtime implementation or seed.
RETIRED_AGENT_NAMES: tuple[str, ...] = ("chart_analyst", "news_digest")
LEGACY_CAPABILITY_AGENT_NAMES = RETIRED_AGENT_NAMES


def infer_agent_kind(agent_name: str | None) -> str:
    name = (agent_name or "").strip()
    if name in LEGACY_CAPABILITY_AGENT_NAMES:
        return AGENT_KIND_CAPABILITY
    return AGENT_KIND_WORKFLOW


def is_workflow_agent(agent_name: str | None) -> bool:
    return infer_agent_kind(agent_name) == AGENT_KIND_WORKFLOW


def is_capability_agent(agent_name: str | None) -> bool:
    return infer_agent_kind(agent_name) == AGENT_KIND_CAPABILITY


@dataclass(frozen=True)
class AgentSeedSpec:
    name: str
    display_name: str
    description: str
    enabled: bool
    schedule: str
    execution_mode: str
    kind: str
    visible: bool
    lifecycle_status: str = "active"
    replaced_by: str = ""
    display_order: int = 0
    config: dict | None = None


AGENT_SEED_SPECS: tuple[AgentSeedSpec, ...] = (
    AgentSeedSpec(
        name="premarket_outlook",
        display_name="盘前分析",
        description="开盘前结合行情、技术状态和隔夜资讯，独立生成今日交易展望",
        enabled=False,
        schedule="0 9 * * 1-5",
        execution_mode="batch",
        kind=AGENT_KIND_WORKFLOW,
        visible=True,
        display_order=10,
    ),
    AgentSeedSpec(
        name="intraday_monitor",
        display_name="盘中监测",
        description="交易时段实时监控，AI 智能判断是否有值得关注的信号",
        enabled=False,
        schedule="*/5 9-15 * * 1-5",
        execution_mode="single",
        kind=AGENT_KIND_WORKFLOW,
        visible=True,
        display_order=20,
        config={
            "event_only": True,
            "price_alert_threshold": 3.0,
            "volume_alert_ratio": 2.0,
            "stop_loss_warning": -5.0,
            "take_profit_warning": 10.0,
            "throttle_minutes": 30,
        },
    ),
    AgentSeedSpec(
        name="daily_report",
        display_name="收盘复盘",
        description="每日收盘后生成复盘报告，包含市场回顾、个股复盘和次日关注",
        enabled=True,
        schedule="30 15 * * 1-5",
        execution_mode="batch",
        kind=AGENT_KIND_WORKFLOW,
        visible=True,
        display_order=30,
    ),
    AgentSeedSpec(
        name="tradingagents",
        display_name="TradingAgents 深度分析",
        description="多 Agent 投资决策框架(基本面/情绪/新闻/技术 + 看多看空辩论 + 风控 + PM)。"
        "可手动触发，默认关闭。",
        enabled=False,
        schedule="",
        execution_mode="single",
        kind=AGENT_KIND_WORKFLOW,
        visible=True,
        display_order=40,
        config={
            "analyst_types": ["market", "social", "news", "fundamentals"],
            "debate_rounds": 1,
            "cache_ttl_hours": 12,
            "deep_model": "",       # 留空走默认 AI Service 的 model;可填如 "claude-sonnet-4"
            "quick_model": "",      # 留空 = deep_model;可填便宜模型如 "deepseek-chat"
            "timeout_minutes": 30,
            "llm_timeout_seconds": 300,  # 单次 LLM 请求超时，给慢响应留出时间
            "collection_timeout_seconds": 45,  # 单个数据源采集超时，失败时降级
            "llm_max_retries": 0,         # 深度分析失败快速落终态，不在图内重复重试
            "llm_max_tokens": 4096,       # 限制模型输出，避免网关空闲超时
            "emit_paper_trading_signal": False,  # 是否把 BUY 决策写入 StrategySignalRun
                                                  # 驱动模拟盘自动开仓 (默认关,需用户主动启用)
            "enable_sec_edgar": False,  # 仅美股：优先使用有 filing-date 语义的 SEC EDGAR 财报
            "holding_period_days": 5,   # 上游决策质量回测使用的默认持仓期限
        },
    ),
)
