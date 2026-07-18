"""marketdata —— 多市场行情数据抓取层(可插拔数据源)。"""

from marketdata.client import MarketData
from marketdata.defaults import InMemoryMetricsSink, StaticConfigProvider
from marketdata.errors import MarketDataError, VendorError
from marketdata.http import capture_errors, record_error
from marketdata.ports import ConfigProvider, MetricsSink, SourceConfig
from marketdata.registry import PACKAGE_VENDORS_BY_TYPE
from marketdata.symbol import Market, Symbol
from marketdata.types import (
    Bar,
    CapitalFlow,
    DividendItem,
    DragonTigerItem,
    EventItem,
    FlashNews,
    Fundamentals,
    HotBoard,
    HotStock,
    MarginItem,
    NewsArticle,
    NorthboundItem,
    Quote,
    Request,
    Response,
    ShareholderItem,
)

__version__ = "0.1.0"

__all__ = [
    "MarketData", "Symbol", "Market", "Bar", "CapitalFlow", "EventItem", "FlashNews", "Fundamentals",
    "HotStock", "HotBoard", "NewsArticle",
    "DragonTigerItem", "MarginItem", "ShareholderItem", "DividendItem", "NorthboundItem",
    "Quote", "Request", "Response",
    "SourceConfig", "ConfigProvider", "MetricsSink",
    "StaticConfigProvider", "InMemoryMetricsSink",
    "PACKAGE_VENDORS_BY_TYPE",
    "capture_errors", "record_error",
    "MarketDataError", "VendorError", "__version__",
]
