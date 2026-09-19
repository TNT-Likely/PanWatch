"""PanWatch 持仓与 TradingAgents 0.5.0 原生 portfolio 接口的适配。

用户持仓由 ``PortfolioContext`` 结构化传入 ``TradingAgentsGraph.propagate``；
不要再把现金、仓位或成本价拼接到 ``past_context``。``past_context`` 仅保留
PanWatch 补充的标的元数据，避免持仓语义依赖上游 prompt 的内部实现。
"""

from __future__ import annotations

import logging
import math
from collections.abc import Iterable
from typing import Any

logger = logging.getLogger(__name__)


def build_stock_metadata_context(
    stock_symbol: str,
    stock_name: str = "",
    market: str = "CN",
    current_price: float | None = None,
    industry: str = "",
) -> str:
    """渲染标的元信息，避免模型从 A/HK ticker 反查并臆测公司。"""
    if not stock_symbol:
        return ""

    market_label = {"CN": "中国 A 股", "HK": "港股", "US": "美股"}.get(market, market)
    lines = [
        "[Stock Metadata]",
        f"- Ticker: {stock_symbol}",
        f"- Company name: {stock_name or 'N/A'}",
        f"- Market: {market_label}",
    ]
    if industry:
        lines.append(f"- Industry: {industry}")
    if current_price and current_price > 0:
        lines.append(f"- Current price: {current_price:.2f}")
    lines.append(
        "- IMPORTANT: This is an A-share / HK / cross-market ticker. DO NOT guess the "
        "company from the ticker code; always use the company name above."
    )
    return "\n".join(lines)


def _finite_number(value: Any) -> float | None:
    """把可能来自数据库的数值安全转换为有限 float。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def to_tradingagents_portfolio(portfolio: Any):
    """把 ``PortfolioInfo`` 转为 TradingAgents 0.5.0 的 ``PortfolioContext``。

    多账户中同一 ticker 的仓位按数量加权平均成本价聚合。没有账户快照时返回
    ``None``，让上游明确区分“用户未提供组合”与“组合现金/仓位均为零”。
    """
    accounts: Iterable[Any] = getattr(portfolio, "accounts", ()) or ()
    accounts = list(accounts)
    if not accounts:
        return None

    from tradingagents.portfolio import PortfolioContext, Position

    cash = 0.0
    by_ticker: dict[str, list[tuple[float, float | None]]] = {}
    for account in accounts:
        available_funds = _finite_number(getattr(account, "available_funds", 0))
        if available_funds is not None:
            cash += available_funds

        for position in getattr(account, "positions", ()) or ():
            ticker = str(getattr(position, "symbol", "") or "").strip().upper()
            quantity = _finite_number(getattr(position, "quantity", None))
            if not ticker or quantity is None or quantity <= 0:
                continue
            average_price = _finite_number(getattr(position, "cost_price", None))
            by_ticker.setdefault(ticker, []).append((quantity, average_price))

    positions = []
    for ticker, lots in by_ticker.items():
        quantity = sum(lot_quantity for lot_quantity, _ in lots)
        priced_lots = [
            (lot_quantity, average_price)
            for lot_quantity, average_price in lots
            if average_price is not None
        ]
        average_price = (
            sum(lot_quantity * price for lot_quantity, price in priced_lots) / quantity
            if len(priced_lots) == len(lots) and quantity > 0
            else None
        )
        positions.append(
            Position(ticker=ticker, quantity=quantity, average_price=average_price)
        )

    return PortfolioContext(cash=cash, positions=positions)


def patch_past_context(graph: Any, metadata_context: str) -> None:
    """把 PanWatch 标的元数据附加到上游公开的 ``past_context`` 扩展点。

    该补丁不处理 portfolio；0.5.0 会由 ``propagate(..., portfolio=...)``
    生成并传入 ``portfolio_context``，这里必须完整透传该参数。
    """
    if not metadata_context:
        return

    propagator = getattr(graph, "propagator", None)
    if propagator is None or not hasattr(propagator, "create_initial_state"):
        logger.warning("[TA context] propagator.create_initial_state 不存在，跳过元数据注入")
        return

    original = propagator.create_initial_state

    def _patched(
        company_name: str,
        trade_date: str,
        asset_type: str = "stock",
        past_context: str = "",
        **kwargs: Any,
    ):
        merged = metadata_context
        if past_context:
            merged = f"{merged}\n\n---\n\n{past_context}"
        return original(
            company_name,
            trade_date,
            asset_type=asset_type,
            past_context=merged,
            **kwargs,
        )

    propagator.create_initial_state = _patched  # type: ignore[method-assign]
    logger.info("[TA context] 已注入 %s 字符标的元数据到 past_context", len(metadata_context))
