"""marketdata —— 多市场行情数据抓取层(可插拔数据源)。"""

from marketdata.client import MarketData
from marketdata.defaults import InMemoryMetricsSink, StaticConfigProvider
from marketdata.errors import MarketDataError, VendorError
from marketdata.ports import ConfigProvider, MetricsSink, SourceConfig
from marketdata.symbol import Market, Symbol
from marketdata.types import Bar, CapitalFlow, EventItem, HotBoard, HotStock, Quote, Request, Response

__version__ = "0.1.0"

__all__ = [
    "MarketData", "Symbol", "Market", "Bar", "CapitalFlow", "EventItem", "HotStock", "HotBoard",
    "Quote", "Request", "Response",
    "SourceConfig", "ConfigProvider", "MetricsSink",
    "StaticConfigProvider", "InMemoryMetricsSink",
    "MarketDataError", "VendorError", "__version__",
]
