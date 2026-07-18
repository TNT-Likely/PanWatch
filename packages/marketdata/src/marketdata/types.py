"""请求 / 响应 / 行情数据类型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class Request:
    """一次数据请求。frozen=True 便于做缓存键。"""

    symbols: tuple[str, ...] = ()
    market: str = "CN"
    timeframe: str = "day"
    limit: int = 120
    since_hours: int = 12
    extra: tuple[tuple[str, Any], ...] = ()

    def cache_key(self, datatype: str) -> str:
        sym = ",".join(self.symbols)
        extra = ",".join(f"{k}={v}" for k, v in self.extra)
        return f"{datatype}|{self.market}|{self.timeframe}|{self.limit}|{self.since_hours}|{sym}|{extra}"


@dataclass
class Quote:
    """标准化实时报价。字段对齐 _parse_tencent_line 的产出。"""

    symbol: str
    market: str
    current_price: float
    name: str = ""
    prev_close: float | None = None
    open_price: float | None = None
    high_price: float | None = None
    low_price: float | None = None
    change_amount: float | None = None
    change_pct: float | None = None
    volume: float | None = None
    turnover: float | None = None
    turnover_rate: float | None = None
    volume_ratio: float | None = None
    pe_ratio: float | None = None
    circulating_market_value: float | None = None
    total_market_value: float | None = None
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class Bar:
    """标准化日K(对齐 PanWatch KlineData:date/open/close/high/low/volume)。"""

    date: str
    open: float
    close: float
    high: float
    low: float
    volume: float = 0.0


@dataclass
class CapitalFlow:
    """资金流向(对齐 PanWatch src/collectors/capital_flow_collector.CapitalFlow)。"""

    symbol: str
    name: str
    main_net_inflow: float | None = None      # 主力净流入
    main_net_inflow_pct: float | None = None   # 主力净流入占比
    super_net_inflow: float | None = None      # 超大单净流入
    big_net_inflow: float | None = None        # 大单净流入
    mid_net_inflow: float | None = None        # 中单净流入
    small_net_inflow: float | None = None      # 小单净流入
    main_net_5d: float | None = None           # 5日主力净流入


@dataclass(frozen=True)
class HotStock:
    """热门/异动股(对齐 PanWatch src/collectors/discovery_collector.HotStock)。"""

    symbol: str
    market: str
    name: str
    price: float | None
    change_pct: float | None
    turnover: float | None
    volume: float | None


@dataclass(frozen=True)
class HotBoard:
    """热门板块(对齐 PanWatch src/collectors/discovery_collector.HotBoard)。"""

    code: str
    name: str
    change_pct: float | None
    change_amount: float | None
    turnover: float | None


@dataclass
class EventItem:
    """结构化事件(对齐 PanWatch src/collectors/events_collector.EventItem)。"""

    source: str
    external_id: str
    event_type: str
    title: str
    publish_time: datetime
    symbols: list[str]
    importance: int
    url: str


@dataclass
class Fundamentals:
    """标准化基本面/财务数据(按 symbol)。估值类字段/财报类字段来源不同、可能分批到位,
    拿不到的字段一律 None,不伪造。"""

    symbol: str
    market: str
    name: str = ""
    # —— 估值类 ——
    pe_ttm: float | None = None                    # 市盈率(TTM)
    pe_static: float | None = None                  # 市盈率(静态)
    pb: float | None = None                         # 市净率
    ps_ttm: float | None = None                     # 市销率(TTM)
    total_market_value: float | None = None         # 总市值(亿)
    circulating_market_value: float | None = None   # 流通市值(亿)
    dividend_yield: float | None = None             # 股息率(%)
    total_shares: float | None = None               # 总股本(股)
    float_shares: float | None = None                # 流通股本(股)
    # —— 财报类 ——
    eps: float | None = None                        # 每股收益
    bps: float | None = None                        # 每股净资产
    roe: float | None = None                        # 净资产收益率(%)
    revenue: float | None = None                    # 营业收入
    net_profit: float | None = None                 # 归母净利润
    gross_margin: float | None = None               # 毛利率(%)
    net_margin: float | None = None                 # 净利率(%)
    revenue_yoy: float | None = None                # 营收同比增长(%)
    net_profit_yoy: float | None = None             # 净利润同比增长(%)
    report_date: str = ""                           # 报告期(原样字符串,不做日期解析)
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class DragonTigerItem:
    """龙虎榜(东财每日龙虎榜明细,市场级,按 date 过滤)。字段待实抓校准。"""

    trade_date: str
    symbol: str
    name: str = ""
    reason: str | None = None          # 上榜原因
    close: float | None = None         # 收盘价
    change_pct: float | None = None    # 涨跌幅(%)
    net_buy: float | None = None       # 龙虎榜净买额(元)
    buy_amt: float | None = None       # 龙虎榜买入额(元)
    sell_amt: float | None = None      # 龙虎榜卖出额(元)
    turnover_pct: float | None = None  # 换手率(%)


@dataclass
class MarginItem:
    """融资融券(东财 datacenter,按 symbol,取最新一条快照)。字段待实抓校准。"""

    date: str
    symbol: str
    rz_balance: float | None = None     # 融资余额(元)
    rz_buy: float | None = None         # 融资买入额(元)
    rz_repay: float | None = None       # 融资偿还额(元)
    rq_balance: float | None = None     # 融券余额(元)
    rq_sell_vol: float | None = None    # 融券卖出量(股)
    rq_repay_vol: float | None = None   # 融券偿还量(股)
    total_balance: float | None = None  # 两融余额(元)


@dataclass
class ShareholderItem:
    """股东户数(东财 datacenter,按 symbol,取最新一期)。字段待实抓校准。"""

    report_date: str
    symbol: str
    holder_num: int | None = None      # 股东户数
    change_num: int | None = None      # 户数变化(较上期)
    change_ratio: float | None = None  # 户数环比变化(%)
    avg_shares: float | None = None    # 户均持股(股)


@dataclass
class DividendItem:
    """分红(东财 datacenter,按 symbol,返回该只全部历史)。字段待实抓校准。"""

    ex_date: str
    symbol: str
    dividend_per_share: float | None = None  # 每股派息(税前,元)
    transfer_ratio: float | None = None      # 每10股转增(股)
    bonus_ratio: float | None = None         # 每10股送股(股)
    progress: str = ""                       # 方案进度


@dataclass
class NorthboundItem:
    """北向资金(同花顺 hexin 当日分钟累计净买入,市场级,取当日末值快照)。
    字段待实抓校准(沙箱代理拦截,无法验证真实响应结构)。"""

    date: str
    hgt_net: float | None = None   # 沪股通净买入(亿元)
    sgt_net: float | None = None   # 深股通净买入(亿元)⚠️ 近期不可靠(可能 NaN/量级异常),需容错
    total_net: float | None = None  # 北向合计=hgt_net+sgt_net;任一为 None 则 None(不臆造)
    time: str = ""                  # 末值对应的分钟时间点(可选)


@dataclass
class FlashNews:
    """快讯(7×24,对齐 cls/sina/eastmoney 快讯流)。市场级,symbols 可空。"""

    source: str
    external_id: str
    title: str
    content: str
    publish_time: datetime
    symbols: list[str] = field(default_factory=list)
    importance: int = 0
    url: str = ""


@dataclass
class NewsArticle:
    """新闻资讯(个股新闻+公告,对齐 PanWatch src/collectors/news_collector.NewsItem)。
    来源可为 xueqiu(雪球个股新闻)/ eastmoney_news(东财个股新闻搜索)/ eastmoney(东财公告)。"""

    source: str
    external_id: str
    title: str
    content: str
    publish_time: datetime
    symbols: list[str] = field(default_factory=list)
    importance: int = 0
    url: str = ""


@dataclass
class Response:
    """Engine 返回:承载 payload + 命中的 vendor/延迟。"""

    ok: bool
    data: Any = None
    error: str = ""
    vendor: str = ""
    latency_ms: int = 0

    @property
    def is_empty(self) -> bool:
        if self.data is None:
            return True
        if isinstance(self.data, (list, tuple, dict, set)) and len(self.data) == 0:
            return True
        return False
