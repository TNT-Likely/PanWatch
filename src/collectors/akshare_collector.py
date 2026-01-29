"""数据采集器 - 基于腾讯股票 HTTP API（稳定可靠，无 SSL 问题）"""
import logging
from abc import ABC, abstractmethod
from datetime import datetime

import httpx

from src.models.market import MarketCode, StockData, IndexData

logger = logging.getLogger(__name__)

# 腾讯股票行情 API（HTTP，GBK 编码）
TENCENT_QUOTE_URL = "http://qt.gtimg.cn/q="

# 预定义指数
CN_INDICES = [
    ("000001", "上证指数", "sh"),
    ("399001", "深证成指", "sz"),
    ("399006", "创业板指", "sz"),
]


def _tencent_symbol(symbol: str, market: MarketCode = MarketCode.CN) -> str:
    """转换为腾讯 API 格式: sh600519 / sz000001 / hk00700 / usAAPL / bj430047

    规则：
    - 港股：hk{symbol}
      示例：00700（腾讯控股）、03690（美团-W）
    - 美股：us{symbol}
      示例：AAPL（Apple）、NVDA（NVIDIA）
    - A股：
      - 上交所（含 ETF/LOF/B 股 等）：5/6/900 开头 -> sh{symbol}
        示例：600519（贵州茅台）、510300（沪深300ETF）、900901（B股示例）
      - 深交所（主板/中小板/创业板/B 股/ETF 等）：0/1/2/3 开头 -> sz{symbol}
        示例：000001（平安银行）、300750（宁德时代）
      - 北交所：920 开头 或 83/87/88 开头 -> bj{symbol}
        示例：920001（贝特瑞）、836239（诺思兰德）
      - 其他未知前缀，默认归为深市 sz
    """
    if market == MarketCode.HK:
        # 例如：00700（腾讯控股）→ hk00700，03690（美团-W）→ hk03690
        return f"hk{symbol}"
    if market == MarketCode.US:
        # 例如：AAPL（Apple）→ usAAPL，NVDA（NVIDIA）→ usNVDA
        return f"us{symbol}"
    # CN 市场代码前缀映射
    # 先处理北交所（新版与旧版代码段）
    if symbol.startswith("920") or symbol.startswith(("83", "87", "88")):
        # 例如：920001（贝特瑞）→ bj920001，836239（诺思兰德）→ bj836239
        return "bj" + symbol
    # 上交所：5/6/900 前缀
    if symbol.startswith(("5", "6")) or symbol.startswith("900"):
        # 例如：600519（贵州茅台）→ sh600519，510300（沪深300ETF）→ sh510300，900901（B股）→ sh900901
        return "sh" + symbol
    if symbol.startswith(("0", "1", "2", "3")):
        # 例如：000001（平安银行）→ sz000001，300750（宁德时代）→ sz300750
        return "sz" + symbol
    # Fallback：未知前缀按深市处理
    return "sz" + symbol


def _parse_tencent_line(line: str) -> dict | None:
    """解析腾讯 API 单行响应"""
    if "=\"\"" in line or not line.strip():
        return None
    try:
        _, value = line.split('="', 1)
        value = value.rstrip('";')
        parts = value.split("~")
        if len(parts) < 35:
            return None

        # 解析成交额: parts[35] 格式为 "price/vol/turnover"
        turnover = 0.0
        if "/" in str(parts[35]):
            turnover_parts = parts[35].split("/")
            if len(turnover_parts) >= 3:
                try:
                    turnover = float(turnover_parts[2])
                except (ValueError, IndexError):
                    pass

        # 处理美股 symbol（如 AAPL.OQ -> AAPL）
        # 注意：指数 symbol 以 . 开头（如 .IXIC, .DJI），需要保留
        symbol = parts[2]
        if "." in symbol and not symbol.startswith("."):
            symbol = symbol.split(".")[0]

        return {
            "name": parts[1],
            "symbol": symbol,
            "current_price": float(parts[3] or 0),
            "prev_close": float(parts[4] or 0),
            "open_price": float(parts[5] or 0),
            "volume": float(parts[6] or 0),
            "change_amount": float(parts[31] or 0),
            "change_pct": float(parts[32] or 0),
            "high_price": float(parts[33] or 0),
            "low_price": float(parts[34] or 0),
            "turnover": turnover,
        }
    except (ValueError, IndexError) as e:
        logger.debug(f"解析腾讯行情失败: {e}")
        return None


def _fetch_tencent_quotes(symbols: list[str]) -> list[dict]:
    """批量获取腾讯实时行情"""
    if not symbols:
        return []
    url = TENCENT_QUOTE_URL + ",".join(symbols)
    with httpx.Client() as client:
        resp = client.get(url, timeout=10)
        content = resp.content.decode("gbk", errors="ignore")

    results = []
    for line in content.strip().split(";"):
        parsed = _parse_tencent_line(line)
        if parsed and parsed["current_price"] > 0:
            results.append(parsed)
    return results


class BaseCollector(ABC):
    """数据采集器抽象基类"""

    market: MarketCode

    @abstractmethod
    async def get_index_data(self) -> list[IndexData]:
        ...

    @abstractmethod
    async def get_stock_data(self, symbols: list[str]) -> list[StockData]:
        ...


class AkshareCollector(BaseCollector):
    """基于腾讯 HTTP API 的数据采集器"""

    def __init__(self, market: MarketCode):
        self.market = market

    async def get_index_data(self) -> list[IndexData]:
        if self.market == MarketCode.CN:
            return self._get_cn_index()
        return []

    async def get_stock_data(self, symbols: list[str]) -> list[StockData]:
        if self.market == MarketCode.CN:
            return self._get_cn_stocks(symbols)
        elif self.market == MarketCode.HK:
            return self._get_hk_stocks(symbols)
        elif self.market == MarketCode.US:
            return self._get_us_stocks(symbols)
        return []

    def _get_cn_index(self) -> list[IndexData]:
        tencent_symbols = [f"{prefix}{symbol}" for symbol, _, prefix in CN_INDICES]
        try:
            items = _fetch_tencent_quotes(tencent_symbols)
        except Exception as e:
            logger.error(f"获取 A 股指数失败: {e}")
            return []

        return [
            IndexData(
                symbol=item["symbol"],
                name=item["name"],
                market=MarketCode.CN,
                current_price=item["current_price"],
                change_pct=item["change_pct"],
                change_amount=item["change_amount"],
                volume=item["volume"],
                turnover=item["turnover"],
                timestamp=datetime.now(),
            )
            for item in items
        ]

    def _get_cn_stocks(self, symbols: list[str]) -> list[StockData]:
        tencent_symbols = [_tencent_symbol(s, MarketCode.CN) for s in symbols]
        try:
            items = _fetch_tencent_quotes(tencent_symbols)
        except Exception as e:
            logger.error(f"获取 A 股行情失败: {e}")
            return []

        return [
            StockData(
                symbol=item["symbol"],
                name=item["name"],
                market=MarketCode.CN,
                current_price=item["current_price"],
                change_pct=item["change_pct"],
                change_amount=item["change_amount"],
                volume=item["volume"],
                turnover=item["turnover"],
                open_price=item["open_price"],
                high_price=item["high_price"],
                low_price=item["low_price"],
                prev_close=item["prev_close"],
                timestamp=datetime.now(),
            )
            for item in items
        ]

    def _get_hk_stocks(self, symbols: list[str]) -> list[StockData]:
        tencent_symbols = [_tencent_symbol(s, MarketCode.HK) for s in symbols]
        try:
            items = _fetch_tencent_quotes(tencent_symbols)
        except Exception as e:
            logger.error(f"获取港股行情失败: {e}")
            return []

        return [
            StockData(
                symbol=item["symbol"],
                name=item["name"],
                market=MarketCode.HK,
                current_price=item["current_price"],
                change_pct=item["change_pct"],
                change_amount=item["change_amount"],
                volume=item["volume"],
                turnover=item["turnover"],
                open_price=item["open_price"],
                high_price=item["high_price"],
                low_price=item["low_price"],
                prev_close=item["prev_close"],
                timestamp=datetime.now(),
            )
            for item in items
        ]

    def _get_us_stocks(self, symbols: list[str]) -> list[StockData]:
        tencent_symbols = [_tencent_symbol(s, MarketCode.US) for s in symbols]
        try:
            items = _fetch_tencent_quotes(tencent_symbols)
        except Exception as e:
            logger.error(f"获取美股行情失败: {e}")
            return []

        return [
            StockData(
                symbol=item["symbol"],
                name=item["name"],
                market=MarketCode.US,
                current_price=item["current_price"],
                change_pct=item["change_pct"],
                change_amount=item["change_amount"],
                volume=item["volume"],
                turnover=item["turnover"],
                open_price=item["open_price"],
                high_price=item["high_price"],
                low_price=item["low_price"],
                prev_close=item["prev_close"],
                timestamp=datetime.now(),
            )
            for item in items
        ]
