"""宏观指标 vendor(市场级,symbols 恒空):akshare 宏观接口,取各指标最新一期。

覆盖 PMI/CPI/PPI/LPR/社融/M2/USDCNY,接口名与列名按本机 akshare(1.18.x)实测源码固定:
- macro_china_pmi(东财):月份/制造业-指数/制造业-同比增长/非制造业-指数/…
- macro_china_cpi(东财):月份/全国-同比增长/…
- macro_china_ppi(东财):月份/当月/当月同比增长/累计
- macro_china_lpr(东财):TRADE_DATE/LPR1Y/LPR5Y
- macro_china_shrzgm(商务部):月份/社会融资规模增量/…
- macro_china_money_supply(东财):月份/货币和准货币(M2)-数量(亿元)/货币和准货币(M2)-同比增长/…
- macro_china_rmb(金十中间价):日期/美元/人民币_中间价/美元/人民币_涨跌幅

行序归一(实抓 2026-09-23 akshare 1.18.97):cpi/pmi/ppi/money_supply 表为**降序**
(head=2026年08月份,tail=2006-2008年),lpr/shrzgm 为升序 —— 不能盲目取末行,
必须把 period 归一为 (年,月,日) 可排序键后取最大行,并在该行内对齐取 period+value
(period 与 value 同行,防错期归因);period 全不可解析时不猜序,该表记失败 fail-soft。

逐项 fail-soft:单项接口缺列/空表/抛错只跳过该项并记 record_error,不产出该条、
绝不编造;全部失败返回 []。akshare 惰性 import,缺库抛 VendorError。
"""

from __future__ import annotations

import logging
import re
from datetime import date, datetime
from typing import Callable

from marketdata.errors import VendorError
from marketdata.http import record_error
from marketdata.types import MacroIndicator
from marketdata.vendors.base import MacroVendor

logger = logging.getLogger(__name__)

# period 原始值 → 可排序 (年, 月, 日)。覆盖仓内实测的全部格式:
# '2026年08月份'/'2026年8月'(东财月度表)、'202604'/'20260415'(商务部数字期)、
# '2026-09-20'(lpr date 对象 str 化/rmb 日期)与 date/datetime/Timestamp 对象。
_PERIOD_PATTERNS: tuple[tuple[re.Pattern, Callable] | tuple[re.Pattern, Callable], ...] = (
    (re.compile(r"^(\d{4})年(\d{1,2})月"),
     lambda m: (int(m.group(1)), int(m.group(2)), 0)),
    (re.compile(r"^(\d{4})[-/](\d{1,2})[-/](\d{1,2})"),
     lambda m: (int(m.group(1)), int(m.group(2)), int(m.group(3)))),
    (re.compile(r"^(\d{4})(\d{2})(\d{2})?$"),
     lambda m: (int(m.group(1)), int(m.group(2)), int(m.group(3) or 0))),
)


def _period_sort_key(value) -> tuple[int, int, int] | None:
    """把 period 原始值归一为可排序 (年,月,日);解析不了返回 None(该行不参与选最新)。"""
    if value is None:
        return None
    if isinstance(value, datetime):
        return (value.year, value.month, value.day)
    if isinstance(value, date):
        return (value.year, value.month, value.day)
    s = str(value).strip()
    if not s:
        return None
    for pat, extract in _PERIOD_PATTERNS:
        m = pat.match(s)
        if m:
            return extract(m)
    return None


def _latest_row(df, period_col: str):
    """返回最新一行的行标签:按 period 归一键取最大(并列取先出现行)。

    表序无关 —— 实测 cpi/pmi/ppi/money_supply 降序、lpr/shrzgm 升序,盲取末行
    会把 18-20 年前旧值当'最新值'。无 period 列/全行不可解析时返回 None,
    调用方按该表失败处理(不猜序、不编造)。
    """
    if period_col not in df.columns:
        return None
    best_idx = None
    best_key: tuple[int, int, int] | None = None
    for idx, value in df[period_col].items():
        key = _period_sort_key(value)
        if key is None:
            continue
        if best_key is None or key > best_key:
            best_key, best_idx = key, idx
    return best_idx


def _txt(value) -> str:
    return "" if value is None else str(value).strip()


def _num_at(row, column: str) -> float | None:
    """取指定行某列的数值(与整列 to_numeric 同等强制程度,NaN/非数返回 None,0 按有效处理)。"""
    import pandas as pd

    if column not in row.index:
        return None
    v = pd.to_numeric(pd.Series([row[column]]), errors="coerce").iloc[0]
    v = float(v) if v == v else None   # 过滤 NaN
    return v


def _indicators_from(df, period_col: str, source: str, items: list[tuple[str, str, str]]) -> list[MacroIndicator]:
    """从一张表抽多个指标:name/value列/unit 三元组。

    period 与各 value 一律取自排序后的**最新同一行**(同行对齐,防 period 取最新、
    value 却来自旧行的错期归因)。
    """
    idx = _latest_row(df, period_col)
    if idx is None:
        return []
    row = df.loc[idx]
    period = _txt(row.get(period_col))
    out: list[MacroIndicator] = []
    for name, col, unit in items:
        value = _num_at(row, col)
        if value is None:
            continue
        out.append(MacroIndicator(name=name, value=value, period=period, unit=unit, source=source))
    return out


class AkshareMacroVendor(MacroVendor):
    name = "akshare"
    supports_markets = set()

    #: 各宏观接口的抽取规格:akshare 函数名 → (period 列, [(指标名, 值列, 单位)])
    _SPECS: dict[str, tuple[str, list[tuple[str, str, str]]]] = {
        "macro_china_pmi": ("月份", [
            ("制造业PMI", "制造业-指数", "%"),
            ("非制造业PMI", "非制造业-指数", "%"),
        ]),
        "macro_china_cpi": ("月份", [
            ("CPI同比", "全国-同比增长", "%"),
        ]),
        "macro_china_ppi": ("月份", [
            ("PPI同比", "当月同比增长", "%"),
        ]),
        "macro_china_lpr": ("TRADE_DATE", [
            ("LPR(1Y)", "LPR1Y", "%"),
            ("LPR(5Y)", "LPR5Y", "%"),
        ]),
        "macro_china_shrzgm": ("月份", [
            ("社融增量", "社会融资规模增量", "亿元"),
        ]),
        "macro_china_money_supply": ("月份", [
            ("M2", "货币和准货币(M2)-数量(亿元)", "亿元"),
            ("M2同比", "货币和准货币(M2)-同比增长", "%"),
        ]),
    }

    def fetch(self, symbols: list, config: dict) -> list[MacroIndicator]:
        try:
            import akshare as ak
        except ImportError as e:
            raise VendorError("akshare 未安装,无法抓取宏观指标") from e

        out: list[MacroIndicator] = []

        # USDCNY:金十人民币中间价末行(注意 akshare 会把缺失填 0,0 视为无效跳过)
        out.extend(self._fetch_usdcny(ak))

        for fn_name, (period_col, items) in self._SPECS.items():
            try:
                fn: Callable = getattr(ak, fn_name, None)
                if fn is None:
                    raise VendorError(f"akshare 无接口 {fn_name}")
                df = fn()
                if df is None or df.empty:
                    raise VendorError(f"{fn_name} 返回空表")
                got = _indicators_from(df, period_col, fn_name, items)
                if not got:
                    raise VendorError(f"{fn_name} 无有效指标列或 period 不可解析")
                out.extend(got)
            except Exception as e:
                # 逐项 fail-soft:单项失败只跳过,不影响其他指标
                logger.warning(f"宏观指标 {fn_name} 获取失败: {e}")
                record_error(f"macro/{fn_name}: {type(e).__name__}: {e}")
        return out

    def _fetch_usdcny(self, ak) -> list[MacroIndicator]:
        try:
            fn = getattr(ak, "macro_china_rmb", None)
            if fn is None:
                raise VendorError("akshare 无接口 macro_china_rmb")
            df = fn()
            if df is None or df.empty:
                raise VendorError("macro_china_rmb 返回空表")
            idx = _latest_row(df, "日期")   # 表序未知/可能变动,按日期归一键取最新行
            if idx is None:
                raise VendorError("macro_china_rmb 日期列不可解析")
            row = df.loc[idx]
            value = _num_at(row, "美元/人民币_中间价")
            if value is None or value <= 0:   # akshare 缺失填 0 → 视为无效
                raise VendorError("美元/人民币_中间价 无有效值")
            return [MacroIndicator(
                name="USDCNY中间价",
                value=value,
                period=_txt(row.get("日期")),
                unit="CNY",
                source="macro_china_rmb",
            )]
        except Exception as e:
            logger.warning(f"宏观指标 macro_china_rmb 获取失败: {e}")
            record_error(f"macro/macro_china_rmb: {type(e).__name__}: {e}")
            return []
