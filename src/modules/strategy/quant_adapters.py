"""量化框架适配器接口(Phase 4 预留,轻量)。

定义统一的回测后端协议,让未来可插入不同实现而不改上层:
- 内置(默认,永远可用):src/core/backtest(纯 Python 轻量内核,Phase 0)
- 可选升级(按路线图,默认不安装,保持自托管轻量):
    · vectorbt —— 向量化批量回测 / 因子网格寻参
    · rqalpha  —— A 股高保真成本撮合(印花税/涨跌停/交易日历)
    · qlib     —— ML 因子研究(Alpha158/360 + LightGBM 等)

此处仅声明接口 + 探测「装了哪些后端」,真正接入时各写一个实现本协议的 adapter。
选型依据见 .docs/quant-framework-comparison.md。
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class BacktestAdapter(Protocol):
    """回测后端统一接口。内置 backtest.engine.Backtester 已满足 run()。"""

    name: str

    def run(self, signals: list, bars_by_symbol: dict):  # noqa: D401
        """对一批信号回测,返回带 metrics 的结果对象。"""
        ...


_OPTIONAL_BACKENDS = (
    ("vectorbt", "vectorbt"),
    ("rqalpha", "rqalpha"),
    ("qlib", "qlib"),
)


def available_backends() -> dict[str, bool]:
    """探测可用回测后端。内置永远可用;可选重依赖按是否已安装返回。

    供 UI / 文档展示当前环境装了哪些后端,不触发任何安装。
    """
    backends: dict[str, bool] = {"builtin": True}
    for module_name, key in _OPTIONAL_BACKENDS:
        try:
            __import__(module_name)
            backends[key] = True
        except Exception:
            backends[key] = False
    return backends
