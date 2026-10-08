"""Locale-aware presentation metadata for host-owned assistant tools."""

from __future__ import annotations

from copy import deepcopy
from typing import Any


TOOL_PRESENTATIONS_EN: dict[str, tuple[str, str]] = {
    "check_watch_request": ("Check watch request", "Identify the original request's instruments, scope, horizon, channels and unsupported alert conditions."),
    "get_monitoring_health": ("Get monitoring health", "Read price-alert check health, daily trigger quota and durable delivery states; no credentials or writes."),
    "get_notification_channels": ("Get notification channels", "Read channel IDs, names, types and enabled/default flags; never return secrets."),
    "get_watchlist": ("Get watchlist", "Read the saved watchlist with market-qualified symbols."),
    "get_research_history": ("Get research history", "Read market-scoped historical suggestions, reports and snapshots with dates and expiry."),
    "get_stock_events": ("Get stock events", "Read CN announcements with source links, publication dates and IDs; HK/US regulatory filings are not covered."),
    "get_event_details": ("Read announcement", "Verify an event ID against its instrument, then read original text; disclose unavailable content."),
    "get_portfolio": ("Get portfolio", "Read live and paper-trading positions, allocation, and a portfolio summary."),
    "get_stock_quote": ("Get live quote", "Read the latest price, change, and intraday quote data for a stock."),
    "find_research_candidates": ("Find research candidates", "Find stocks and opportunity signals worth further research from the market and portfolio."),
    "get_kline_summary": ("Analyze price trend", "Read moving averages, momentum, and a recent candlestick-indicator summary for a stock."),
    "get_stock_news": ("Get stock news", "Retrieve recent news, announcements, and a concise summary for a stock."),
    "search_stocks": ("Search stocks", "Search PanWatch stocks by symbol or name and confirm their market."),
    "get_market_status": ("Get market status", "Check current market status and trading sessions for A-shares, Hong Kong, and U.S. markets."),
    "get_hot_stocks": ("Get hot stocks", "Rank active stocks in a market by turnover or price gain."),
    "get_hot_boards": ("Get hot sectors", "Rank active sectors and themes in a market by gain, turnover, or heat."),
    "get_board_stocks": ("Get sector constituents", "List stocks in a sector ranked by gain, turnover, or heat."),
    "get_stock_fundamentals": ("Get stock fundamentals", "Read valuation, profitability, growth, and reporting-period fundamentals for a stock."),
    "get_capital_flow": ("Get capital flow", "Read main-fund, large-order, and recent capital-flow data for a stock."),
    "get_dragon_tiger": ("Get Dragon Tiger List", "Read Dragon Tiger List stocks, listing reasons, and buy/sell amounts for a trading date."),
    "get_price_alerts": ("Get price alerts", "List existing price alerts, their conditions, and enabled status."),
    "update_price_alert": ("Update price alert", "Update a price alert's name, target price, direction, or enabled status after approval."),
    "delete_price_alert": ("Delete price alert", "Delete a price alert and its hit history after approval."),
    "create_price_alert": ("Create price alert", "Create an alert with full AND/OR conditions, expiry, channels and frequency after approval; no bar-close or moving-average triggers."),
    "portfolio_diagnosis": ("Diagnose portfolio", "Analyze portfolio risk and holdings, then provide evidence-based rebalancing guidance."),
}


INPUT_DESCRIPTIONS_EN: dict[str, str] = {
    "symbol": "Stock symbol, for example 600519.",
    "market": "Market code: CN, HK, or US.",
    "query": "Stock symbol or name.",
    "limit": "Maximum number of results.",
    "date": "Trading date in YYYY-MM-DD format.",
    "mode": "Ranking mode supported by this tool.",
    "board_code": "Sector code.",
    "holding": "Holding filter.",
    "risk_level": "Risk-level filter.",
    "min_score": "Minimum opportunity score.",
    "enabled": "Whether the alert is enabled.",
    "rule_id": "Price-alert ID.",
    "name": "New alert name.",
    "direction": "Whether the price should move above or below the target.",
    "target_price": "Target price for the alert.",
    "cooldown_minutes": "Cooldown after a trigger, in minutes.",
    "max_triggers_per_day": "Maximum daily triggers; zero means no limit.",
    "repeat_mode": "Whether the alert triggers once or repeatedly.",
    "market_hours_mode": "Whether the alert runs only during market hours.",
    "expire_at": "ISO-8601 expiry time; use null to clear it.",
}


ENTITY_LABELS_EN: dict[str, str] = {
    "股票代码": "stock symbol",
    "股票名称": "stock name",
    "市场": "market",
    "排序方式": "sort order",
    "数量": "result count",
    "板块代码": "sector code",
    "交易日期": "trading date",
}


def localized_tool_presentation(
    tool_name: str,
    title: str,
    description: str,
    language: str,
) -> tuple[str, str]:
    """Return English tool metadata when the current interface is English."""
    if language == "en-US":
        return TOOL_PRESENTATIONS_EN.get(tool_name, (tool_name.replace("_", " "), description))
    return title, description


def localized_input_schema(schema: dict[str, Any], language: str) -> dict[str, Any]:
    """Keep model-visible JSON-schema descriptions in the selected language."""
    if language != "en-US":
        return schema

    localized = deepcopy(schema)
    for field, definition in (localized.get("properties") or {}).items():
        if isinstance(definition, dict) and "description" in definition:
            definition["description"] = INPUT_DESCRIPTIONS_EN.get(
                field,
                f"Optional input for {field.replace('_', ' ')}.",
            )
    return localized
