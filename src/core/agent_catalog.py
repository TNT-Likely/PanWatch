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

CAPABILITY_AGENT_NAMES: tuple[str, ...] = (
    "news_digest",
    "chart_analyst",
)


def infer_agent_kind(agent_name: str | None) -> str:
    name = (agent_name or "").strip()
    if name in CAPABILITY_AGENT_NAMES:
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
        description="开盘前综合昨日分析和隔夜信息，展望今日走势",
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
        name="news_digest",
        display_name="新闻速递（能力）",
        description="内部能力：提供新闻抓取、去重与主题聚合，不独立调度",
        enabled=False,
        schedule="",
        execution_mode="batch",
        kind=AGENT_KIND_CAPABILITY,
        visible=False,
        lifecycle_status="deprecated",
        replaced_by="premarket_outlook,daily_report,intraday_monitor",
        display_order=110,
        config={
            "since_hours": 12,
            "fallback_since_hours": 24,
        },
    ),
    AgentSeedSpec(
        name="chart_analyst",
        display_name="技术分析（能力）",
        description="内部能力：详情页按需触发图像技术分析，不独立调度",
        enabled=False,
        schedule="",
        execution_mode="single",
        kind=AGENT_KIND_CAPABILITY,
        visible=False,
        lifecycle_status="deprecated",
        replaced_by="intraday_monitor,daily_report,premarket_outlook",
        display_order=120,
    ),
)

