"""跨市场股票代码值对象:一处归一化,替代散落各处的 _to_market/前缀逻辑。"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import Enum


class Market(str, Enum):
    CN = "CN"
    HK = "HK"
    US = "US"


_CN_RE = re.compile(r"^[036]\d{5}$")   # 6 位,0/3/6 开头
_HK_RE = re.compile(r"^\d{5}$")        # 5 位数字
_US_RE = re.compile(r"^[A-Z.]{1,6}$")  # 1-6 位字母(含指数 .DJI)


def _detect_market(code: str) -> Market:
    c = code.strip().upper()
    if _CN_RE.match(c):
        return Market.CN
    if _HK_RE.match(c):
        return Market.HK
    if _US_RE.match(c):
        return Market.US
    # 兜底:6 位数字当 CN,其余当 US
    return Market.CN if c.isdigit() and len(c) == 6 else Market.US


def _cn_exchange(code: str) -> str:
    """SH / SZ / BJ —— 与 src/core/cn_symbol.get_cn_exchange 规则一致。"""
    if code.startswith("920") or code.startswith(("83", "87", "88")):
        return "bj"
    if code.startswith(("5", "6")) or code.startswith("900"):
        return "sh"
    return "sz"


@dataclass(frozen=True)
class Symbol:
    market: Market
    code: str

    @classmethod
    def parse(cls, raw: str, market: str | None = None) -> "Symbol":
        code = raw.strip()
        if market:
            return cls(Market(market), code)
        return cls(_detect_market(code), code)

    def to_tencent(self) -> str:
        if self.market == Market.HK:
            return f"hk{self.code}"
        if self.market == Market.US:
            return f"us{self.code}"
        return _cn_exchange(self.code) + self.code

    def to_yfinance(self) -> str:
        if self.market == Market.HK:
            return f"{int(self.code):04d}.HK" if self.code.isdigit() else f"{self.code}.HK"
        return self.code  # US 直接用;CN 由 vendor.supports_markets 拦截,不会走到这

    def to_eastmoney_secid(self) -> str:
        if self.market == Market.HK:
            return f"116.{self.code}"
        if self.market == Market.US:
            return f"105.{self.code}"
        return f"{'1' if _cn_exchange(self.code) == 'sh' else '0'}.{self.code}"
