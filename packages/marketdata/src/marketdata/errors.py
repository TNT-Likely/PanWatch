"""marketdata 异常类型。"""


class MarketDataError(Exception):
    """本包所有异常的基类。"""


class VendorError(MarketDataError):
    """单个 vendor 抓取失败(Engine 捕获后转移到下一个源)。"""
