"""智兔数服 Engine vendors(K线/资金流/股东/财务), 走 ZhituApiClient, token 多 key 池化。

注册进 Engine 的 kline/capital_flow/shareholders/fundamentals 类型, 优先级高于东财
(东财在云服务器不稳定, 智兔作优先稳定源; 腾讯仍保留)。
"""

import logging

from marketdata.symbol import Symbol
from marketdata.types import Bar, CapitalFlow, ShareholderItem, Fundamentals
from marketdata.vendors.base import (
    KlineVendor,
    CapitalFlowVendor,
    ShareholdersVendor,
    FundamentalsVendor,
)
from marketdata.vendors.zhitu_api import (
    kline as zhitu_kline,
    capital_flow as zhitu_capital_flow,
    top10_holders as zhitu_top10,
    top_holder_changes as zhitu_holder_changes,
    finance_main as zhitu_finance_main,
)

logger = logging.getLogger("marketdata.zhitu_full")


def _to_zhitu_code(sym: Symbol) -> str:
    """Symbol → 智兔代码格式 000001.SZ / 600519.SH / 00700.HK。"""
    if sym.market.value == "CN":
        suffix = "SH" if sym.code.startswith(("6", "9")) else "SZ"
        return f"{sym.code}.{suffix}"
    if sym.market.value == "HK":
        return f"{sym.code}.HK"
    return sym.code


class ZhituKlineVendor(KlineVendor):
    name = "zhitu"
    supports_markets = {"CN", "HK"}

    def fetch(self, symbols: list[Symbol], config: dict) -> list[Bar]:
        out: list[Bar] = []
        for sym in symbols:
            code = _to_zhitu_code(sym)
            rows = zhitu_kline(code, level="d", latest=120)
            if not rows:
                continue
            for r in rows:
                try:
                    out.append(Bar(
                        date=str(r.get("date") or r.get("t") or r.get("time"))[:10],
                        open=float(r.get("open") or r.get("o") or 0),
                        high=float(r.get("high") or r.get("h") or 0),
                        low=float(r.get("low") or r.get("l") or 0),
                        close=float(r.get("close") or r.get("c") or 0),
                        volume=float(r.get("volume") or r.get("v") or 0),
                    ))
                except (TypeError, ValueError):
                    continue
        return out


class ZhituCapitalFlowVendor(CapitalFlowVendor):
    name = "zhitu"
    supports_markets = {"CN"}

    def fetch(self, symbols: list[Symbol], config: dict) -> list[CapitalFlow]:
        out: list[CapitalFlow] = []
        for sym in symbols:
            code = _to_zhitu_code(sym)
            rows = zhitu_capital_flow(code, latest=1)
            if not rows:
                continue
            r = rows[0]
            try:
                out.append(CapitalFlow(
                    symbol=sym.code,
                    name="",
                    main_net_inflow=float(r.get("main_net") or r.get("zljlr") or 0),
                    super_net_inflow=float(r.get("特大") or 0),
                    big_net_inflow=float(r.get("大") or 0),
                    mid_net_inflow=float(r.get("中") or 0),
                    small_net_inflow=float(r.get("小") or 0),
                ))
            except (TypeError, ValueError):
                continue
        return out


class ZhituShareholdersVendor(ShareholdersVendor):
    name = "zhitu"
    supports_markets = {"CN"}

    def fetch(self, symbols: list[Symbol], config: dict) -> list[ShareholderItem]:
        out: list[ShareholderItem] = []
        for sym in symbols:
            code = _to_zhitu_code(sym)
            d = zhitu_top10(code)
            if not d:
                continue
            # 智兔 top10_holders 返回 {report_date, holders:[...]}
            rep_date = str(d.get("date") or d.get("report_date") or "")
            holders = d.get("holders") or d.get("data") or []
            for h in holders[:10]:
                try:
                    out.append(ShareholderItem(
                        report_date=rep_date,
                        symbol=sym.code,
                        holder_num=int(float(h.get("shares") or 0)),
                        change_num=int(float(h.get("change") or 0)),
                        change_ratio=float(h.get("ratio") or 0),
                    ))
                except (TypeError, ValueError):
                    continue
        return out


class ZhituFundamentalsVendor(FundamentalsVendor):
    name = "zhitu"
    supports_markets = {"CN"}

    def fetch(self, symbols: list[Symbol], config: dict) -> list[Fundamentals]:
        out: list[Fundamentals] = []
        for sym in symbols:
            code = _to_zhitu_code(sym)
            d = zhitu_finance_main(code)
            if not d:
                continue
            try:
                out.append(Fundamentals(
                    symbol=sym.code,
                    market=sym.market.value,
                    name="",
                    pe_ttm=float(d.get("pe") or d.get("syl") or 0),
                    pb=float(d.get("pb") or d.get("scl") or 0),
                    total_market_value=float(d.get("total_mv") or d.get("zgz") or 0),
                ))
            except (TypeError, ValueError):
                continue
        return out
