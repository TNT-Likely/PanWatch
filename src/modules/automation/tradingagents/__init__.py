"""TradingAgents 集成模块。

把 TauricResearch/TradingAgents (多 Agent 投资决策框架,76k star) 适配进 PanWatch:
- 桥接 PanWatch AI Service 到 TradingAgents LLM config
- 把 PanWatch Provider 体系的数据注入 TradingAgents 的 data vendor 层(A 股专用)
- 把 TradingAgents 的 final_state 映射成 PanWatch 的 AnalysisResult
- 通过 LangChain callbacks 反馈进度

软依赖:`tradingagents` 库不在 PyPI,需用户自行 git clone + pip install -e。
未安装时 TradingAgentsAgent.run() 会返回明确错误,不会让服务 crash。

详细设计:`.docs/tradingagents/02-technical-design.md`
"""

from src.modules.automation.tradingagents.agent import TradingAgentsAgent
from src.modules.automation.tradingagents.data_context import (
    build_stock_metadata_context,
    patch_instrument_context,
    to_tradingagents_portfolio,
)
from src.modules.automation.tradingagents.decision import map_state_to_result
from src.modules.automation.tradingagents.observability import (
    PanWatchProgressHandler,
    aggregate_progress,
    check_budget,
    estimate_cost,
)

__all__ = [
    "TradingAgentsAgent",
    "PanWatchProgressHandler",
    "aggregate_progress",
    "build_stock_metadata_context",
    "check_budget",
    "estimate_cost",
    "map_state_to_result",
    "patch_instrument_context",
    "to_tradingagents_portfolio",
]
