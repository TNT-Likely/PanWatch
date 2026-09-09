"""把 PanWatch 的 PortfolioInfo 渲染成 TradingAgents past_context 文本。

TradingAgents 上游设计 past_context 是 PM 节点的扩展通道
(见 tradingagents/agents/managers/portfolio_manager.py:35-40):

    past_context = state.get("past_context", "")
    lessons_line = (
        f"- Lessons from prior decisions and outcomes:\\n{past_context}\\n"
        if past_context else ""
    )

我们利用这个通道注入"用户持仓上下文",让 PM 在生成最终决策时考虑:
- 用户当前持仓量 / 成本价 / 当前盈亏
- 账户可用资金 / 总持仓占比
- 用户交易风格(短线/波段/长线)

这是上游官方扩展点,跨版本稳定;不需要破坏 prompt 模板。
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def build_stock_metadata_context(
    stock_symbol: str,
    stock_name: str = "",
    market: str = "CN",
    current_price: float | None = None,
    industry: str = "",
) -> str:
    """渲染标的元信息(公司名/市场/当前价),强制告诉 LLM 不要从 ticker 反查公司。

    A 股 ticker(6 位数字)不在 yfinance/finnhub 数据集,LLM 不能从 ticker 反查
    公司,必须显式告诉它"601127 = 赛力斯",否则会瞎编。

    本段落注入到 past_context 顶部,PM 节点等读 past_context 的节点能看到。
    """
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


def build_portfolio_context(
    portfolio,
    stock_symbol: str,
    current_price: float | None = None,
) -> str:
    """从 PanWatch PortfolioInfo 渲染该股票 + 账户的个性化上下文文本。

    Args:
        portfolio: AgentContext.portfolio (PortfolioInfo 实例)
        stock_symbol: 当前分析的标的代码
        current_price: 实时价(可选,用于算 PnL)

    Returns:
        Markdown 文本,如果用户没有该股票持仓且没账户信息,返回空串。
    """
    if portfolio is None:
        return ""

    try:
        positions = portfolio.get_positions_for_stock(stock_symbol)
    except Exception:
        positions = []

    try:
        total_available = portfolio.total_available_funds
        total_cost = portfolio.total_cost
        all_positions = portfolio.all_positions
    except Exception:
        total_available = 0.0
        total_cost = 0.0
        all_positions = []

    if not positions and total_available <= 0 and total_cost <= 0:
        return ""

    lines: list[str] = []
    lines.append("[User Portfolio Context]")
    lines.append(
        "The following is the user's personal portfolio context. Use it to tailor your "
        "final decision (e.g. adjust position sizing, evaluate PnL impact, respect user's "
        "trading style and available capital)."
    )

    # 该股票的具体持仓
    if positions:
        # 多账户汇总
        total_qty = sum(p.quantity for p in positions)
        total_cost_value = sum(p.cost_value for p in positions)
        avg_cost = total_cost_value / total_qty if total_qty else 0
        styles = list({p.trading_style for p in positions if p.trading_style})
        style_text = "/".join(styles) if styles else "swing"
        style_map = {"short": "短线", "swing": "波段", "long": "长线"}
        style_zh = "/".join(style_map.get(s, s) for s in styles) if styles else "波段"

        lines.append(f"\n## Holding for {stock_symbol}")
        lines.append(f"- Quantity: {total_qty} shares (across {len(positions)} account(s))")
        lines.append(f"- Average cost: {avg_cost:.2f}")
        lines.append(f"- Total cost basis: {total_cost_value:.2f}")
        lines.append(f"- Trading style: {style_text} ({style_zh})")

        # PnL(需当前价)
        if current_price and current_price > 0:
            unrealized = (current_price - avg_cost) * total_qty
            pnl_pct = (current_price / avg_cost - 1) * 100 if avg_cost else 0
            sign = "+" if unrealized >= 0 else ""
            lines.append(
                f"- Current price: {current_price:.2f}, "
                f"unrealized P&L: {sign}{unrealized:.2f} ({sign}{pnl_pct:.2f}%)"
            )
    else:
        lines.append(f"\n## Holding for {stock_symbol}")
        lines.append("- The user does NOT currently hold this stock.")
        lines.append("- Treat this as a potential new entry, not a hold/add decision.")

    # 账户总览
    if total_available > 0 or total_cost > 0:
        lines.append("\n## Account Overview")
        total_market = total_cost  # 简略;实际 market_value 需汇总当前价计算
        total_assets = total_available + total_market
        ratio = (total_market / total_assets * 100) if total_assets > 0 else 0
        lines.append(f"- Available cash: {total_available:.2f}")
        lines.append(f"- Total position cost basis: {total_market:.2f}")
        lines.append(f"- Position ratio: {ratio:.1f}% (positions / total assets)")
        lines.append(f"- Number of holdings: {len(all_positions)}")

    lines.append(
        "\n## Guidance"
    )
    lines.append(
        "- If user is heavily positioned and the analysis is bullish, prefer 'Hold' over 'Buy'."
    )
    lines.append(
        "- If user has no position and analysis is bearish, prefer 'Hold' (don't open new short)."
    )
    lines.append(
        "- Reference the user's trading style when sizing recommendations."
    )
    lines.append(
        "- Quote the user's average cost in your decision if relevant for stop-loss/profit-take."
    )

    return "\n".join(lines)


def patch_propagator(graph, portfolio_context_text: str) -> None:
    """猴补 TradingAgentsGraph.propagator.create_initial_state,把 portfolio context
    拼到 past_context 前面。

    安全性:
    - past_context 是 TradingAgents 上游公开的扩展通道(agent_states.py:73 已标注为
      Memory log context injected at run start)
    - 上游 PortfolioManager 直接读这个字段并拼到 prompt,我们注入即被 PM 看到
    - 不动 prompt 模板、不动 LLM、不动 node 创建逻辑
    - 跨版本稳定 — 字段是 typed schema,即使上游重命名也会保留兼容

    Args:
        graph: TradingAgentsGraph 实例(已经 __init__ 完成)
        portfolio_context_text: 渲染好的 portfolio context 文本(可能为空串)
    """
    if not portfolio_context_text:
        return  # 没东西要注入,跳过 patch

    propagator = getattr(graph, "propagator", None)
    if propagator is None or not hasattr(propagator, "create_initial_state"):
        logger.warning("[TA portfolio] propagator.create_initial_state 不存在,跳过注入")
        return

    original = propagator.create_initial_state

    def _patched(company_name: str, trade_date: str, past_context: str = "", **kwargs: Any):
        merged = portfolio_context_text
        if past_context:
            merged = f"{merged}\n\n---\n\n{past_context}"
        return original(company_name, trade_date, past_context=merged, **kwargs)

    propagator.create_initial_state = _patched  # type: ignore[method-assign]
    logger.info(
        f"[TA portfolio] 已注入 portfolio context ({len(portfolio_context_text)} 字符) 到 past_context"
    )
