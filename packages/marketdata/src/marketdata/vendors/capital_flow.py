"""资金流向 vendor:东财 + 新浪(CN)。移植自 PanWatch capital_flow_collector 抓取核。"""
from __future__ import annotations

import json
import time

from marketdata.http import market_get
from marketdata.symbol import Market, Symbol
from marketdata.types import CapitalFlow
from marketdata.vendors.base import CapitalFlowVendor

_EASTMONEY_FLOW_URL = "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
_FLOW_HOST = "push2his.eastmoney.com"
_FLOW_MIN_INTERVAL_S = 0.2

_SINA_FLOW_URL = (
    "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/"
    "MoneyFlow.ssl_qsfx_zjlrqs"
)
_SINA_FLOW_HOST = "vip.stock.finance.sina.com.cn"
_SINA_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
_SINA_HEADERS = {"User-Agent": _SINA_UA, "Referer": "https://finance.sina.com.cn/"}
_SINA_DEFAULT_DAYS = 60


def _cn_exchange_prefix(code: str) -> str:
    """sh / sz / bj —— 与 Symbol._cn_exchange 规则一致(北交所另判)。"""
    if code.startswith("920") or code.startswith(("83", "87", "88")):
        return "bj"
    if code.startswith(("5", "6")) or code.startswith("900"):
        return "sh"
    return "sz"


def _safe_float(value) -> float:
    """将字符串或数字安全转换为 float,无效值返回 0.0。"""
    if value is None or value == "" or value == "-":
        return 0.0
    try:
        return float(value)
    except (ValueError, TypeError):
        return 0.0


class EastmoneyCapitalFlowVendor(CapitalFlowVendor):
    name = "eastmoney"
    supports_markets = {"CN", "HK", "US"}

    def fetch(self, symbols: list[Symbol], config: dict) -> list[CapitalFlow]:
        if not symbols:
            return []
        sym = symbols[0]

        params = {
            "lmt": "0",
            "klt": "101",
            "secid": sym.to_eastmoney_secid(),
            "fields1": "f1,f2,f3,f7",
            "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
            "ut": "b2884a393a59ad64002292a3e90d46a5",
            "_": int(time.time() * 1000),
        }
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Referer": "https://quote.eastmoney.com/",
        }

        data = market_get(
            _EASTMONEY_FLOW_URL,
            host_key=_FLOW_HOST,
            params=params,
            headers=headers,
            min_interval_s=_FLOW_MIN_INTERVAL_S,
            timeout=8,
            retries=2,
            parse="json",
            symbol=sym.code,
            log_label="资金流",
        )
        if not data:
            return []

        d = data.get("data")
        if d is None:
            return []
        klines = d.get("klines")
        if not klines:
            return []

        # 字段索引(从0开始,逗号行):
        # 0:日期, 1:主力净额, 2:小单净额, 3:中单净额, 4:大单净额, 5:超大单净额,
        # 6:主力占比, 7:小单占比, 8:中单占比, 9:大单占比, 10:超大单占比,
        # 11:收盘价, 12:涨跌幅, 13:成交量, 14:成交额
        last_line = klines[-1]
        parts = str(last_line).split(",")
        if len(parts) < 13:
            return []

        # 5日主力净流入(klines 从旧到新,取最后5条的主力净额之和)
        last_five = klines[-5:] if len(klines) >= 5 else klines
        main_net_5d = 0.0
        for line in last_five:
            line_parts = str(line).split(",")
            if len(line_parts) >= 2:
                main_net_5d += _safe_float(line_parts[1])

        return [CapitalFlow(
            symbol=str(d.get("code") or sym.code),
            name=str(d.get("name") or ""),
            main_net_inflow=_safe_float(parts[1]),      # 主力净流入
            main_net_inflow_pct=_safe_float(parts[6]),  # 主力净流入占比
            super_net_inflow=_safe_float(parts[5]),      # 超大单净流入
            big_net_inflow=_safe_float(parts[4]),        # 大单净流入
            mid_net_inflow=_safe_float(parts[3]),         # 中单净流入
            small_net_inflow=_safe_float(parts[2]),       # 小单净流入
            main_net_5d=main_net_5d,                      # 5日主力净流入
        )]


class SinaCapitalFlowVendor(CapitalFlowVendor):
    """资金流向 vendor:新浪(CN 单市场,东财之外的第二源)。

    端点 MoneyFlow.ssl_qsfx_zjlrqs(资金流入趋势)实测只提供「主力」+「超大单」两档
    净额,没有大/中/小单细分——这两档字段按东财 vendor 的同形字段填 0.0,保持
    CapitalFlow 结构一致,不抛异常、不伪造数据。
    """

    name = "sina"
    supports_markets = {"CN"}

    def fetch(self, symbols: list[Symbol], config: dict) -> list[CapitalFlow]:
        if not symbols:
            return []
        sym = symbols[0]
        if sym.market != Market.CN:
            return []

        days = config.get("days") or _SINA_DEFAULT_DAYS
        daima = _cn_exchange_prefix(sym.code) + sym.code
        params = {
            "page": "1",
            "num": days,
            "sort": "opendate",
            "asc": "0",
            "daima": daima,
        }

        text = market_get(
            _SINA_FLOW_URL,
            host_key=_SINA_FLOW_HOST,
            params=params,
            headers=_SINA_HEADERS,
            parse="text",
            encoding="gbk",
            retries=2,
            timeout=8,
            symbol=sym.code,
            log_label="新浪资金流",
        )
        if not text:
            return []

        text = text.strip()
        try:
            start, end = text.index("["), text.rindex("]")
            rows = json.loads(text[start:end + 1])
        except (ValueError, json.JSONDecodeError):
            return []
        if not rows:
            return []

        # 新浪按 opendate 降序返回(最新在前),取最近 5 条求主力净额之和
        last_five = rows[:5]
        main_net_5d = sum(_safe_float(row.get("netamount")) for row in last_five)

        latest = rows[0]
        return [CapitalFlow(
            symbol=sym.code,
            name="",  # 新浪该端点不返回股票名称
            main_net_inflow=_safe_float(latest.get("netamount")),       # 主力净流入
            main_net_inflow_pct=_safe_float(latest.get("ratioamount")),  # 主力净流入占比
            super_net_inflow=_safe_float(latest.get("r0_net")),          # 超大单净流入
            big_net_inflow=0.0,     # 新浪该端点无大单细分
            mid_net_inflow=0.0,     # 新浪该端点无中单细分
            small_net_inflow=0.0,   # 新浪该端点无小单细分
            main_net_5d=main_net_5d,  # 5日主力净流入
        )]
