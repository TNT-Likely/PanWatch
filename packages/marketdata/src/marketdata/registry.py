"""包内各数据类型的合法 vendor 权威(唯一真相源)。

`MarketData.__init__` 用 `build_vendors(datatype)` 构建各 Engine 的 `vendors={}`;
`PACKAGE_VENDORS_BY_TYPE` 由同一份 `VENDOR_CLASSES_BY_TYPE` 派生 vendor 名集合。
两者共享同一份类映射,不会出现"改了 Engine 忘了改权威表"的漂移。

宿主(PanWatch `DataSource` 表)据此判定某行 `(type, provider)` 是否为孤儿:
`legal(type) = PACKAGE_VENDORS_BY_TYPE.get(type, frozenset()) | seed 内该 type 的 provider 集合`。
discovery/index 是市场级、非 symbol 模型,不进 Engine/不进 DataSource,故不出现在此表。
"""

from __future__ import annotations

from marketdata.vendors.capital_flow import EastmoneyCapitalFlowVendor, SinaCapitalFlowVendor
from marketdata.vendors.eastmoney import EastmoneyQuoteVendor
from marketdata.vendors.events import EventsVendor
from marketdata.vendors.fundamentals import EastmoneyFundamentalsVendor, TencentFundamentalsVendor
from marketdata.vendors.flash_news import (
    ClsFlashNewsVendor,
    EastmoneyFlashNewsVendor,
    SinaFlashNewsVendor,
)
from marketdata.vendors.kline import (
    EastmoneyKlineVendor,
    StooqKlineVendor,
    TencentKlineVendor,
    YahooKlineVendor,
)
from marketdata.vendors.market_flow import (
    EastmoneyDividendVendor,
    EastmoneyDragonTigerVendor,
    EastmoneyMarginVendor,
    EastmoneyShareholdersVendor,
)
from marketdata.vendors.news import (
    EastmoneyAnnNewsVendor,
    EastmoneyStockNewsVendor,
    XueqiuNewsVendor,
)
from marketdata.vendors.northbound import HexinNorthboundVendor
from marketdata.vendors.sina import SinaQuoteVendor
from marketdata.vendors.tencent import TencentQuoteVendor
from marketdata.vendors.yfinance import YFinanceQuoteVendor

# 各数据类型 → {vendor name: vendor 类}。注意:vendor 的 import 本身是廉价的
# (可选三方依赖如 yfinance 均在 fetch() 内部惰性 import),模块级导入不会引入重依赖。
VENDOR_CLASSES_BY_TYPE: dict[str, dict[str, type]] = {
    "quote": {
        "tencent": TencentQuoteVendor,
        "sina": SinaQuoteVendor,
        "eastmoney": EastmoneyQuoteVendor,
        "yfinance": YFinanceQuoteVendor,
    },
    "kline": {
        "tencent": TencentKlineVendor,
        "stooq": StooqKlineVendor,
        "eastmoney": EastmoneyKlineVendor,
        "yahoo": YahooKlineVendor,
    },
    "capital_flow": {
        "eastmoney": EastmoneyCapitalFlowVendor,
        "sina": SinaCapitalFlowVendor,
    },
    "events": {
        "eastmoney": EventsVendor,
    },
    "fundamentals": {
        "tencent": TencentFundamentalsVendor,
        "eastmoney": EastmoneyFundamentalsVendor,
    },
    "flash_news": {
        "cls": ClsFlashNewsVendor,
        "sina": SinaFlashNewsVendor,
        "eastmoney": EastmoneyFlashNewsVendor,
    },
    "news": {
        "xueqiu": XueqiuNewsVendor,
        "eastmoney_news": EastmoneyStockNewsVendor,
        "eastmoney": EastmoneyAnnNewsVendor,
    },
    "dragon_tiger": {
        "eastmoney": EastmoneyDragonTigerVendor,
    },
    "margin": {
        "eastmoney": EastmoneyMarginVendor,
    },
    "shareholders": {
        "eastmoney": EastmoneyShareholdersVendor,
    },
    "dividend": {
        "eastmoney": EastmoneyDividendVendor,
    },
    "northbound": {
        "ths": HexinNorthboundVendor,
    },
}

# 各数据类型的合法 vendor 名集合(冻结,防止调用方误改)。
PACKAGE_VENDORS_BY_TYPE: dict[str, frozenset[str]] = {
    datatype: frozenset(classes.keys()) for datatype, classes in VENDOR_CLASSES_BY_TYPE.items()
}


def build_vendors(datatype: str) -> dict[str, object]:
    """实例化某数据类型的全部 vendor,供 Engine 注入。未知 datatype 返回空字典。"""
    return {name: cls() for name, cls in VENDOR_CLASSES_BY_TYPE.get(datatype, {}).items()}
