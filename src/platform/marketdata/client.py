"""Transitional facade for the marketdata package integration."""

from src.core.marketdata_client import (
    DbConfigProvider,
    get_market_data,
    md_news,
    md_news_by_keyword,
    md_quote_rows,
    md_stock_data,
    reset_market_data,
)

__all__ = [
    "DbConfigProvider",
    "get_market_data",
    "md_news",
    "md_news_by_keyword",
    "md_quote_rows",
    "md_stock_data",
    "reset_market_data",
]

