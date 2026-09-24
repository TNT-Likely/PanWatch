"""全球指数 vendor(市场级,symbols 恒空):腾讯 HTTP 主源 + Yahoo/akshare 备源。

三个 provider 各自独立 Vendor 子类,共用一套"跨源统一指数键"(DJI/IXIC/INX/HSI/HSTECH/
N225/KS11/USDX…),便于调用方在多源间交叉比对。腾讯符号映射只收录可确认的代码:
- usDJI/usIXIC/usINX/hkHSI:与 client.INDEX_TENCENT、市场模块既有用法一致(仓内已验证);
- hkHSTECH(恒生科技)/whUSDX(美元指数,wh=外汇前缀):2026-09-23 对 qt.gtimg.cn
  实抓确认,名称/价格字段正常返回;注意 wh* 行是 n=22 的外汇布局(字段位与股票行不同),
  过不了 _parse_line 的 n>=35 门槛,走 _parse_wh_line 专用解析;
- 日经225/KOSPI 的腾讯代码经两轮候选代码实抓(jpN225/neN225/N225/krKS11/KS11 等)
  均返回 pv_none_match,无法确认,不硬编码(由 yahoo_global(^N225/^KS11)与
  akshare_global(东财全球指数)覆盖)。
"""

from __future__ import annotations

import logging

from marketdata.errors import VendorError
from marketdata.http import record_error
from marketdata.types import GlobalIndexQuote
from marketdata.vendors.base import GlobalMarketsVendor

logger = logging.getLogger(__name__)

# 跨源统一键 → 腾讯原始符号。顺序即默认抓取顺序。
TENCENT_GLOBAL_SYMBOLS: dict[str, str] = {
    "DJI": "usDJI",       # 道琼斯
    "IXIC": "usIXIC",     # 纳斯达克
    "INX": "usINX",       # 标普500
    "HSI": "hkHSI",       # 恒生指数
    "HSTECH": "hkHSTECH",  # 恒生科技(2026-09-23 实抓确认)
    "USDX": "whUSDX",     # 美元指数(2026-09-23 实抓确认)
}

# 跨源统一键 → Yahoo 符号。^N225/^KS11/^HSI 为规格指定;其余为 Yahoo 标准指数代码。
YAHOO_GLOBAL_SYMBOLS: dict[str, str] = {
    "N225": "^N225",      # 日经225
    "KS11": "^KS11",      # 韩国KOSPI
    "HSI": "^HSI",        # 恒生指数
    "SPX": "^GSPC",       # 标普500
    "DJI": "^DJI",        # 道琼斯
    "IXIC": "^IXIC",      # 纳斯达克
    "USDX": "DX-Y.NYB",   # 美元指数(ICE)
}

# 东财全球指数代码 → 跨源统一键(仅覆盖重合指数;未映射的原样透传)。
EM_CODE_TO_KEY: dict[str, str] = {
    "DJIA": "DJI",
    "SPX": "SPX",
    "NDX": "IXIC",
    "HSI": "HSI",
    "HSCEI": "HSCEI",
    "N225": "N225",
    "KS11": "KS11",
    "UDI": "USDX",
}


def _to_float(value) -> float | None:
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if f == f else None   # 过滤 NaN


def _filter_keys(mapping: dict[str, str], config: dict) -> dict[str, str]:
    """config.symbols 指定子集(如 ["N225","KS11"])则过滤,否则返回全量。"""
    wanted = config.get("symbols")
    if not wanted:
        return mapping
    return {k: v for k, v in mapping.items() if k in set(wanted)}


def _parse_wh_line(line: str) -> tuple[str, float, float | None, float | None] | None:
    """腾讯外汇行(wh 前缀)专用轻解析:返回 (名称, 最新价, 昨收, 涨跌幅%)。

    外汇布局与股票行情字段位不同且总字段数 n=22,过不了 _parse_line 的 n>=35 门槛
    (会被整行拒绝 → 美元指数静默缺失)。实抓 2026-09-23:
    310~美元指数~USDX~100.78~0~时间戳~100.53(昨收)~100.54(开)~高~低~…~0.25(涨跌额)~0.25(涨跌幅%)…
    涨跌幅字段位缺/非数时由 price/close 计算,close 也无效则 None(不臆造)。
    """
    if '=""' in line or not line.strip():
        return None
    try:
        _, value = line.split('="', 1)
        parts = value.rstrip('";').split("~")
        if len(parts) < 7:
            return None
        name = parts[1]
        price = _to_float(parts[3])
        prev = _to_float(parts[6])
        if price is None or price <= 0:
            return None
        pct = _to_float(parts[13]) if len(parts) > 13 else None
        if pct is None and prev:
            pct = (price - prev) / prev * 100
        return name, price, (prev if prev and prev > 0 else None), pct
    except (ValueError, IndexError):
        return None


class TencentGlobalVendor(GlobalMarketsVendor):
    """腾讯 qt.gtimg.cn 全球指数(GBK,免 key)。复用 tencent.py 的共用取数核。

    股指行(us*/hk*,n=73/78)走 tencent._parse_line;外汇行(wh*,n=22)字段位不同,
    走 _parse_wh_line 专用解析(按行头 code 前缀路由)。
    """

    name = "tencent_global"
    supports_markets = set()   # 市场级,不过滤

    def fetch(self, symbols: list, config: dict) -> list[GlobalIndexQuote]:
        # 延迟到 fetch 内导入,便于测试 monkeypatch marketdata.vendors.tencent.market_get
        from marketdata.vendors.tencent import _fetch_lines, _parse_line

        mapping = _filter_keys(TENCENT_GLOBAL_SYMBOLS, config)
        if not mapping:
            return []

        # 腾讯响应行形如 v_usDJI="...";用行头变量名回对统一键,不依赖返回顺序
        code_to_key = {code: key for key, code in mapping.items()}
        out: list[GlobalIndexQuote] = []
        for line in _fetch_lines(list(mapping.values())):
            head = line.split("=", 1)[0].strip()
            code = head[2:] if head.startswith("v_") else head
            key = code_to_key.get(code)
            if key is None:
                continue
            if code.startswith("wh"):
                parsed = _parse_wh_line(line)
                if parsed is None:
                    continue
                name, price, prev, pct = parsed
                out.append(GlobalIndexQuote(
                    symbol=key,
                    name=name,
                    price=price,
                    change_pct=pct,
                    close=prev,
                    source_tag=self.name,
                ))
                continue
            q = _parse_line(line, "")
            if q is None or q.current_price <= 0:
                continue
            out.append(GlobalIndexQuote(
                symbol=key,
                name=q.name,
                price=q.current_price,
                change_pct=q.change_pct,
                close=q.prev_close or None,
                source_tag=self.name,
            ))
        return out


class YahooGlobalVendor(GlobalMarketsVendor):
    """Yahoo Finance 全球指数(yfinance,可选)。惰性 import,缺库抛 VendorError。"""

    name = "yahoo_global"
    supports_markets = set()

    def fetch(self, symbols: list, config: dict) -> list[GlobalIndexQuote]:
        try:
            import yfinance as yf
        except ImportError as e:
            raise VendorError("yfinance 未安装,执行 `pip install yfinance` 后启用") from e

        mapping = _filter_keys(YAHOO_GLOBAL_SYMBOLS, config)
        out: list[GlobalIndexQuote] = []
        for key, ysym in mapping.items():
            try:
                info = yf.Ticker(ysym).fast_info
                last = _to_float(info.get("last_price")) if info else None
                if last is None:
                    record_error(f"yfinance {ysym}: 返回空(last_price 缺失,可能被限流/需代理)")
                    continue
                prev = _to_float(info.get("previous_close")) if info else None
                pct = ((last - prev) / prev * 100) if prev else None
                out.append(GlobalIndexQuote(
                    symbol=key,
                    name="",
                    price=last,
                    change_pct=pct,
                    close=prev,
                    source_tag=self.name,
                ))
            except Exception as e:
                logger.debug(f"yfinance 拉取 {ysym} 失败: {e}")
                record_error(f"yfinance {ysym}: {type(e).__name__}: {e}")
        return out


class AkshareGlobalVendor(GlobalMarketsVendor):
    """东财全球指数实时(akshare index_global_spot_em)。惰性 import。"""

    name = "akshare_global"
    supports_markets = set()

    #: 东财返回列(akshare 1.18.x 实测源码):代码/名称/最新价/涨跌幅/昨收价
    _COLS = ("代码", "名称", "最新价", "涨跌幅", "昨收价")

    def fetch(self, symbols: list, config: dict) -> list[GlobalIndexQuote]:
        try:
            import akshare as ak
        except ImportError as e:
            raise VendorError("akshare 未安装,无法抓取东财全球指数") from e

        df = ak.index_global_spot_em()
        if df is None or df.empty:
            return []
        missing = [c for c in self._COLS if c not in df.columns]
        if missing:
            raise VendorError(f"东财全球指数返回缺列: {missing}")

        wanted = set(config.get("symbols") or [])
        out: list[GlobalIndexQuote] = []
        for _, row in df.iterrows():
            raw_code = str(row["代码"]).strip()
            key = EM_CODE_TO_KEY.get(raw_code, raw_code)
            if wanted and key not in wanted and raw_code not in wanted:
                continue
            price = _to_float(row["最新价"])
            if price is None or price <= 0:
                continue
            out.append(GlobalIndexQuote(
                symbol=key,
                name=str(row["名称"] or "").strip(),
                price=price,
                change_pct=_to_float(row["涨跌幅"]),
                close=_to_float(row["昨收价"]),
                source_tag=self.name,
            ))
        return out
