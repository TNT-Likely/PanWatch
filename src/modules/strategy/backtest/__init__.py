"""PanWatch 回测模块(Phase 0 地基)。

轻量、纯 Python、零第三方依赖的事件式回测内核,服务于:
- 历史验证现有 StrategySignalRun 信号的真实表现
- 为 Phase 2 的因子 IC/IR 与回测驱动调权提供真值
- 与模拟盘(Phase 1)共用 A 股交易成本模型

vectorbt 作为未来可选的向量化升级路径(见 .docs/quant-framework-comparison.md)。
"""

from src.modules.strategy.backtest.cost_model import CostConfig, CostModel, DEFAULT_COST_MODEL, Fill
from src.modules.strategy.backtest.data_adapter import PriceBar, from_klines, load_price_history
from src.modules.strategy.backtest.engine import (
    Backtester,
    BacktestResult,
    BTTrade,
    Signal,
    fixed_cash_sizer,
    horizon_return,
)
from src.modules.strategy.backtest import metrics

__all__ = [
    "CostConfig",
    "CostModel",
    "DEFAULT_COST_MODEL",
    "Fill",
    "PriceBar",
    "from_klines",
    "load_price_history",
    "Backtester",
    "BacktestResult",
    "BTTrade",
    "Signal",
    "fixed_cash_sizer",
    "horizon_return",
    "metrics",
]
