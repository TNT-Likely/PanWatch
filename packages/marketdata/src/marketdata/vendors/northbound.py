"""北向资金 vendor:同花顺(ths/hexin)当日分钟累计净买入,市场级(symbols 恒空)。

背景:东财 datacenter/push2 的北向资金接口(kamt)自 2024-08 起断供(返回 NaN/0),
不可用。改走同花顺 hexin 私有接口 `data.hexin.cn/market/hsgtApi/method/dayChart/`,
返回当日分钟级累计净买入序列,`hgt`(沪股通)/`sgt`(深股通),单位均为"亿元"。

**待实抓校准**:沙箱代理会拦截 hexin,无法实抓验证真实响应结构。以下解析按背景描述
("响应含当日分钟序列,每点有时间 + hgt/sgt 累计值")尽力构造 + 逐层防御 `.get()`,
拿不到就返回 []。真实结构上线前需用真实响应复核。

已知坑(SKILL 标注):`sgt`(深股通)近期数据不可靠,可能是 NaN 或量级异常(远超合理的
"亿元"范围),必须容错——异常时 sgt_net=None,不参与 total_net 计算,也不让异常值污染
hgt_net。绝不用无参 now()/time()/random 填充缺失的 date/time。
"""
from __future__ import annotations

from marketdata.http import market_get
from marketdata.symbol import Symbol
from marketdata.types import NorthboundItem
from marketdata.vendors.base import NorthboundVendor as _NorthboundVendorBase

_HEXIN_URL = "https://data.hexin.cn/market/hsgtApi/method/dayChart/"
_HEXIN_HOST = "data.hexin.cn"

_UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/122.0.0.0 Safari/537.36"
)

_HEADERS = {
    "Host": _HEXIN_HOST,
    "Referer": "https://data.hexin.cn/",
    "User-Agent": _UA,
}

# sgt(深股通)近期不可靠,可能出现量级异常(远超合理"亿元"净买入范围)的脏值;
# 超过此绝对值阈值一律视为异常丢弃。阈值本身是防御性经验值,非精确业务规则。
_SGT_MAX_ABS = 2000.0


def _to_float(value) -> float | None:
    """宽松转 float;None/无法转换/NaN 一律 None(NaN 用 f != f 判定,不额外 import math)。"""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    if f != f:  # NaN
        return None
    return f


def _sgt_valid(value) -> float | None:
    """sgt 专用:在 _to_float 基础上再做量级容错(近期不可靠,可能 NaN/异常大)。"""
    f = _to_float(value)
    if f is None:
        return None
    if abs(f) > _SGT_MAX_ABS:
        return None
    return f


def _unwrap_payload(resp) -> dict:
    """防御性剥离外层包裹:hexin 响应可能是 {"data": {...}} 或再套一层
    {"data": {"data": {...}}}——具体结构待实抓校准,逐层 .get() 兜底,拿不到就 {}。
    """
    if not isinstance(resp, dict):
        return {}
    layer = resp.get("data")
    if not isinstance(layer, dict):
        return {}
    inner = layer.get("data")
    if isinstance(inner, dict):
        return inner
    return layer


def _last_point(series) -> tuple[object, object]:
    """从分钟序列取末值(当日最新累计净买入)。序列元素可能是 [time, value] 或
    {"time":.., "value":..}(键名待实抓校准,防御多种常见键名)。取不到返回 (None, None)。
    """
    if not isinstance(series, (list, tuple)) or not series:
        return None, None
    last = series[-1]
    if isinstance(last, (list, tuple)) and len(last) >= 2:
        return last[0], last[1]
    if isinstance(last, dict):
        t = last.get("time") or last.get("t") or last.get("x")
        v = last.get("value") or last.get("v") or last.get("y") or last.get("net")
        return t, v
    return None, None


class HexinNorthboundVendor(_NorthboundVendorBase):
    """北向资金(同花顺 hexin):市场级,fetch 忽略 symbols。取当日分钟序列末值组装 1 条。"""

    name = "ths"
    supports_markets = {"CN"}

    def fetch(self, symbols: list[Symbol], config: dict) -> list[NorthboundItem]:
        data = market_get(
            _HEXIN_URL,
            host_key=_HEXIN_HOST,
            headers=_HEADERS,
            parse="json",
            retries=2,
            timeout=8,
            log_label="北向资金",
        )
        if not data:
            return []

        payload = _unwrap_payload(data)
        if not payload:
            return []

        hgt_time, hgt_raw = _last_point(payload.get("hgt"))
        sgt_time, sgt_raw = _last_point(payload.get("sgt"))
        hgt_net = _to_float(hgt_raw)
        sgt_net = _sgt_valid(sgt_raw)
        if hgt_net is None and sgt_net is None:
            return []

        total_net = hgt_net + sgt_net if (hgt_net is not None and sgt_net is not None) else None
        date = str(
            (data.get("date") if isinstance(data, dict) else None)
            or payload.get("date")
            or (config or {}).get("date")
            or ""
        )
        time_point = hgt_time if hgt_time is not None else sgt_time
        time_str = str(time_point) if time_point is not None else ""

        return [
            NorthboundItem(
                date=date,
                hgt_net=hgt_net,
                sgt_net=sgt_net,
                total_net=total_net,
                time=time_str,
            )
        ]
