"""市场/资金面 vendor:龙虎榜 / 融资融券 / 股东户数 / 分红,均走东财 datacenter 同构接口
(datacenter-web.eastmoney.com/api/data/v1/get,reportName + filter + columns=ALL)。

字段索引按任务方给出的列名(源自 a-stock SKILL.md)+ 现有 fundamentals.py 取数骨架校准,
未在沙箱内实抓验证——标"待实抓校准"的字段上线前需用真实响应复核。拿不到的字段一律 None,
不伪造、不用无参 now()/random 填充数值。

- 龙虎榜(dragon_tiger):**市场级**,不按 symbol,按 date 过滤当日全部上榜明细。
- 融资融券(margin)/股东户数(shareholders)/分红(dividend):**按 symbol**,逐只请求
  (datacenter 该几个 report 均只支持单代码 filter,无法一次性批量多只)。
"""

from __future__ import annotations

import logging

from marketdata.http import market_get
from marketdata.symbol import Symbol
from marketdata.types import DividendItem, DragonTigerItem, MarginItem, ShareholderItem
from marketdata.vendors.base import (
    DividendVendor,
    DragonTigerVendor,
    MarginVendor,
    ShareholdersVendor,
)

logger = logging.getLogger(__name__)

_DATACENTER_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
_DATACENTER_HOST = "datacenter-web.eastmoney.com"


def _to_float(value) -> float | None:
    if value is None or value == "" or value == "-":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _to_int(value) -> int | None:
    if value is None or value == "" or value == "-":
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _datacenter_get(report: str, filter_str: str, sort_col: str, page_size: int = 50) -> list[dict]:
    """东财 datacenter 统一请求 helper:GET datacenter-web.eastmoney.com/api/data/v1/get。

    防御:请求失败或响应结构不含 result.data 一律返回 []。
    """
    resp = market_get(
        _DATACENTER_URL,
        host_key=_DATACENTER_HOST,
        params={
            "reportName": report,
            "columns": "ALL",
            "filter": filter_str,
            "pageNumber": 1,
            "pageSize": page_size,
            "sortColumns": sort_col,
            "sortTypes": -1,
            "source": "WEB",
            "client": "WEB",
        },
        parse="json",
        retries=2,
        timeout=10,
        log_label=f"东财市场资金面/{report}",
    )
    if not resp or not isinstance(resp, dict):
        return []
    result = resp.get("result")
    if not result or not isinstance(result, dict):
        return []
    data = result.get("data")
    return data if isinstance(data, list) else []


# ============================== 龙虎榜(市场级) ==============================

_REPORT_DRAGON_TIGER = "RPT_DAILYBILLBOARD_DETAILSNEW"


class EastmoneyDragonTigerVendor(DragonTigerVendor):
    """龙虎榜:市场级,fetch 忽略 symbols,按 config["date"](YYYY-MM-DD)过滤当日明细。

    不猜测"今天"——调用方未显式给 date 时直接返回 [],避免包内出现无参 now()。
    """

    name = "eastmoney"
    supports_markets = {"CN"}

    def fetch(self, symbols: list[Symbol], config: dict) -> list[DragonTigerItem]:
        date = (config or {}).get("date")
        if not date:
            return []
        filter_str = f"(TRADE_DATE>='{date}')(TRADE_DATE<='{date}')"
        rows = _datacenter_get(_REPORT_DRAGON_TIGER, filter_str, "BILLBOARD_NET_AMT", page_size=500)

        out: list[DragonTigerItem] = []
        for row in rows:
            try:
                out.append(
                    DragonTigerItem(
                        trade_date=str(row.get("TRADE_DATE") or date)[:10],
                        symbol=str(row.get("SECURITY_CODE") or ""),
                        name=str(row.get("SECURITY_NAME_ABBR") or ""),
                        reason=row.get("EXPLANATION"),
                        close=_to_float(row.get("CLOSE_PRICE")),
                        change_pct=_to_float(row.get("CHANGE_RATE")),
                        net_buy=_to_float(row.get("BILLBOARD_NET_AMT")),
                        buy_amt=_to_float(row.get("BILLBOARD_BUY_AMT")),
                        sell_amt=_to_float(row.get("BILLBOARD_SELL_AMT")),
                        turnover_pct=_to_float(row.get("TURNOVERRATE")),
                    )
                )
            except Exception as e:
                logger.debug(f"解析龙虎榜行失败: {e}")
                continue
        return out


# ============================== 融资融券(按 symbol) ==============================

_REPORT_MARGIN = "RPTA_WEB_RZRQ_GGMX"


class EastmoneyMarginVendor(MarginVendor):
    """融资融券:按 symbol 逐只请求近期明细,取最新一条(sortTypes=-1 已降序 → data[0])。"""

    name = "eastmoney"
    supports_markets = {"CN"}

    def fetch(self, symbols: list[Symbol], config: dict) -> list[MarginItem]:
        if not symbols:
            return []
        out: list[MarginItem] = []
        for sym in symbols:
            try:
                filter_str = f'(SCODE="{sym.code}")'
                rows = _datacenter_get(_REPORT_MARGIN, filter_str, "DATE", page_size=30)
                if not rows:
                    continue
                row = rows[0]
                out.append(
                    MarginItem(
                        date=str(row.get("DATE") or "")[:10],
                        symbol=sym.code,
                        rz_balance=_to_float(row.get("RZYE")),
                        rz_buy=_to_float(row.get("RZMRE")),
                        rz_repay=_to_float(row.get("RZCHE")),
                        rq_balance=_to_float(row.get("RQYE")),
                        rq_sell_vol=_to_float(row.get("RQMCL")),
                        rq_repay_vol=_to_float(row.get("RQCHL")),
                        total_balance=_to_float(row.get("RZRQYE")),
                    )
                )
            except Exception as e:
                logger.debug(f"东财融资融券取数异常 symbol={sym.code}: {e}")
                continue
        return out


# ============================== 股东户数(按 symbol) ==============================

_REPORT_SHAREHOLDERS = "RPT_HOLDERNUMLATEST"


class EastmoneyShareholdersVendor(ShareholdersVendor):
    """股东户数:按 symbol 逐只请求,取最新一期。"""

    name = "eastmoney"
    supports_markets = {"CN"}

    def fetch(self, symbols: list[Symbol], config: dict) -> list[ShareholderItem]:
        if not symbols:
            return []
        out: list[ShareholderItem] = []
        for sym in symbols:
            try:
                filter_str = f'(SECURITY_CODE="{sym.code}")'
                rows = _datacenter_get(_REPORT_SHAREHOLDERS, filter_str, "END_DATE", page_size=1)
                if not rows:
                    continue
                row = rows[0]
                out.append(
                    ShareholderItem(
                        report_date=str(row.get("END_DATE") or "")[:10],
                        symbol=sym.code,
                        holder_num=_to_int(row.get("HOLDER_NUM")),
                        change_num=_to_int(row.get("HOLDER_NUM_CHANGE")),
                        change_ratio=_to_float(row.get("HOLDER_NUM_RATIO")),
                        avg_shares=_to_float(row.get("AVG_FREE_SHARES")),
                    )
                )
            except Exception as e:
                logger.debug(f"东财股东户数取数异常 symbol={sym.code}: {e}")
                continue
        return out


# ============================== 分红(按 symbol) ==============================

_REPORT_DIVIDEND = "RPT_SHAREBONUS_DET"


class EastmoneyDividendVendor(DividendVendor):
    """分红:按 symbol 逐只请求,返回该只全部分红历史(可能多条)。"""

    name = "eastmoney"
    supports_markets = {"CN"}

    def fetch(self, symbols: list[Symbol], config: dict) -> list[DividendItem]:
        if not symbols:
            return []
        out: list[DividendItem] = []
        for sym in symbols:
            try:
                filter_str = f'(SECURITY_CODE="{sym.code}")'
                rows = _datacenter_get(_REPORT_DIVIDEND, filter_str, "EX_DIVIDEND_DATE", page_size=20)
                for row in rows:
                    try:
                        out.append(
                            DividendItem(
                                ex_date=str(row.get("EX_DIVIDEND_DATE") or "")[:10],
                                symbol=sym.code,
                                dividend_per_share=_to_float(row.get("PRETAX_BONUS_RMB")),
                                transfer_ratio=_to_float(row.get("TRANSFER_RATIO")),
                                bonus_ratio=_to_float(row.get("BONUS_RATIO")),
                                progress=str(row.get("ASSIGN_PROGRESS") or ""),
                            )
                        )
                    except Exception as e:
                        logger.debug(f"解析分红行失败 symbol={sym.code}: {e}")
                        continue
            except Exception as e:
                logger.debug(f"东财分红取数异常 symbol={sym.code}: {e}")
                continue
        return out
