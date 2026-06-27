"""K线和技术指标采集器 - 基于腾讯 API（更稳定）"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import httpx
import json
import random
import threading
import time

from src.collectors.market_http import fetch_source, source_suffix
from src.core.cn_symbol import get_cn_prefix, is_cn_sh
from src.core.timezone import beijing_now
from src.models.market import MARKETS, MarketCode

logger = logging.getLogger(__name__)


def _finite_float(value) -> float | None:
    """转为 float；nan/inf 视为无效(None)，避免 JSON 序列化失败。"""
    if value is None:
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if math.isfinite(f) else None


def _filter_valid_klines(bars: list["KlineData"]) -> list["KlineData"]:
    """去掉 OHLC 含 nan/inf 的脏 K 线。"""
    out: list[KlineData] = []
    for k in bars or []:
        if all(
            _finite_float(x) is not None
            for x in (k.open, k.close, k.high, k.low)
        ):
            out.append(k)
    return out


def _slice_klines(bars: list["KlineData"], need: int) -> list["KlineData"]:
    clean = _filter_valid_klines(bars)
    return clean[-need:] if len(clean) > need else clean


def _sanitize_json_floats(value):
    """递归将 dict/list 中的 nan/inf 浮点转为 None。"""
    if isinstance(value, dict):
        return {k: _sanitize_json_floats(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_sanitize_json_floats(v) for v in value]
    if isinstance(value, float):
        return _finite_float(value)
    return value


# 腾讯日K线 API
TENCENT_KLINE_URL = "http://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
TENCENT_MINUTE_URL = "http://web.ifzq.gtimg.cn/appstock/app/minute/query"
EASTMONEY_KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
EASTMONEY_TRENDS_URL = "https://push2.eastmoney.com/api/qt/stock/trends2/get"


_STOOQ_CACHE: dict[str, tuple[float, list["KlineData"]]] = {}
_STOOQ_CACHE_TTL_SECONDS = 300
_EASTMONEY_CACHE: dict[str, tuple[float, int, list["KlineData"]]] = {}
_EASTMONEY_CACHE_TTL_SECONDS = 300
_EASTMONEY_FAIL_UNTIL: dict[str, float] = {}
_EASTMONEY_FAIL_COOLDOWN_S = 120.0
_YFINANCE_CACHE: dict[str, tuple[float, int, list["KlineData"]]] = {}
_YFINANCE_CACHE_TTL_SECONDS = 300


# 调用来源标记统一在 market_http(全项目共享一个 contextvar)。
# 保留 kline_source / _source_suffix 名称,兼容已有调用方(schedulers 等)。
kline_source = fetch_source
_source_suffix = source_suffix


# ── K线按市场状态缓存 ──────────────────────────────────────────────────────
# 日K一天只定稿一次(收盘后),但调度任务每轮都逐只重新联网拉 → 批量突发触发限流。
# 交易时段用短 TTL(末根K线盘中会动),收盘后用长 TTL(数据已定稿,无需重复拉)。
_KLINE_CACHE: dict[str, tuple[float, int, list["KlineData"]]] = {}
_KLINE_TTL_TRADING_S = 180
_KLINE_TTL_CLOSED_S = 1800

# 失败负缓存:源短暂故障(Server disconnected/限流)时,冷却窗口内不再联网。
# 复活的批量消费者(entry_candidates/strategy_engine/backtest/组合归因)会并发地
# 对同一批标的取数,空结果若不缓存则每个消费者每轮都重复打爆数据源。
_FAIL_UNTIL: dict[str, float] = {}
_FAIL_COOLDOWN_S = 60.0  # 交易时段:短冷却,便于尽快重试
_FAIL_COOLDOWN_CLOSED_S = 900.0  # 收盘后:数据已定稿,失败/不足时长冷却,避免批量任务反复刷屏


def _fail_cooldown(market: MarketCode) -> float:
    """取数失败/不足时的冷却时长:交易时段短(尽快重试),收盘后长(重试无意义且易刷屏)。"""
    try:
        md = MARKETS.get(market)
        if md and md.is_trading_time():
            return _FAIL_COOLDOWN_S
    except Exception:
        pass
    return _FAIL_COOLDOWN_CLOSED_S


# 同标的并发合并:同一 cache_key 的并发取数串行化,只联网一次,其余复用缓存。
_FETCH_LOCKS: dict[str, threading.Lock] = {}
_FETCH_LOCKS_GUARD = threading.Lock()


def _get_fetch_lock(cache_key: str) -> threading.Lock:
    """返回某 cache_key 的取数锁(进程内复用),用于合并同标的并发请求。"""
    with _FETCH_LOCKS_GUARD:
        lk = _FETCH_LOCKS.get(cache_key)
        if lk is None:
            lk = threading.Lock()
            _FETCH_LOCKS[cache_key] = lk
        return lk


def _kline_cache_ttl(market: MarketCode) -> float:
    try:
        md = MARKETS.get(market)
        if md and md.is_trading_time():
            return _KLINE_TTL_TRADING_S
    except Exception:
        pass
    return _KLINE_TTL_CLOSED_S


def clear_kline_cache() -> None:
    """清空 K线内存缓存与失败冷却标记(测试隔离用)。"""
    _KLINE_CACHE.clear()
    _FAIL_UNTIL.clear()
    _YFINANCE_CACHE.clear()
    _EASTMONEY_FAIL_UNTIL.clear()
    _BAOSTOCK_CACHE.clear()
    _BAOSTOCK_FAIL_UNTIL.clear()
    _INTRADAY_TRENDS_CACHE.clear()


def _us_kline_clearly_insufficient(bars: list["KlineData"], need: int) -> bool:
    """腾讯美股常只回 1-2 条脏数据,不应被负缓存长期当作有效结果。"""
    return len(bars) < max(10, min(need, 30))


def _cn_kline_insufficient_for_need(bars: list["KlineData"], need: int) -> bool:
    """A 股条数不足时不走负缓存短路,便于 Baostock 等后续兜底源重试。"""
    return len(bars) < max(10, min(need, int(need * 0.6)))


def _fetch_stooq_us_klines(symbol: str) -> list[KlineData]:
    """Fetch daily US kline from Stooq (CSV, free, no key).

    Endpoint: https://stooq.com/q/d/l/?s=aapl.us&i=d
    """

    sym = (symbol or "").strip().lower()
    if not sym:
        return []

    now = time.time()
    cached = _STOOQ_CACHE.get(sym)
    stale = cached[1] if cached else []
    if cached and (now - cached[0]) < _STOOQ_CACHE_TTL_SECONDS:
        return cached[1]

    # Stooq uses dot for class shares (e.g., brk.b). Keep as-is.
    stooq_sym = f"{sym}.us"
    url = "https://stooq.com/q/d/l/"
    params = {"s": stooq_sym, "i": "d"}
    headers = {"User-Agent": "AlphaMind/1.0 (+https://github.com/)"}
    last_err = None
    text = ""
    for attempt in range(3):
        try:
            timeout = 12 + attempt * 6
            with httpx.Client(
                follow_redirects=True,
                timeout=timeout,
                headers=headers,
                trust_env=False,  # 行情直连,绕过 env 代理(生产代理会拦行情接口)
            ) as client:
                resp = client.get(url, params=params)
                resp.raise_for_status()
                text = resp.text
            last_err = None
            break
        except Exception as e:
            last_err = e
            # Backoff a bit
            time.sleep(0.4 * (attempt + 1))

    if last_err is not None:
        logger.warning(f"Stooq 获取 {symbol} K线失败: {last_err}")
        # Return stale cache if we have any.
        return stale

    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if len(lines) <= 1:
        return []

    # Header: Date,Open,High,Low,Close,Volume
    out: list[KlineData] = []
    for ln in lines[1:]:
        parts = ln.split(",")
        if len(parts) < 6:
            continue
        date_s, o, h, l, c, v = parts[:6]
        if not date_s or date_s == "Date":
            continue
        try:
            out.append(
                KlineData(
                    date=date_s,
                    open=float(o),
                    close=float(c),
                    high=float(h),
                    low=float(l),
                    volume=float(v) if v else 0,
                )
            )
        except Exception:
            continue
    _STOOQ_CACHE[sym] = (now, out)
    return out


def _normalize_yfinance_symbol(symbol: str, market: MarketCode) -> str:
    sym = (symbol or "").strip()
    if market == MarketCode.US:
        return sym.upper().replace(".", "-")
    if market == MarketCode.HK:
        if sym.isdigit():
            return f"{int(sym):04d}.HK"
        return f"{sym}.HK" if not sym.upper().endswith(".HK") else sym.upper()
    return sym


def _yfinance_period(days: int) -> str:
    if days <= 30:
        return "1mo"
    if days <= 90:
        return "3mo"
    if days <= 180:
        return "6mo"
    if days <= 365:
        return "1y"
    if days <= 730:
        return "2y"
    if days <= 1825:
        return "5y"
    return "max"


def _fetch_yfinance_klines(
    symbol: str, market: MarketCode, days: int
) -> list[KlineData]:
    """Fetch daily kline via yfinance (US/HK fallback when domestic sources fail)."""
    if market not in (MarketCode.US, MarketCode.HK):
        return []

    sym = (symbol or "").strip()
    if not sym:
        return []

    need_days = max(1, int(days or 1))
    cache_key = f"{market.value}:{sym.upper()}"
    now = time.time()
    cached = _YFINANCE_CACHE.get(cache_key)
    if (
        cached
        and (now - cached[0]) < _YFINANCE_CACHE_TTL_SECONDS
        and cached[1] >= need_days
    ):
        bars = cached[2]
        return bars[-need_days:] if len(bars) > need_days else bars

    try:
        import yfinance as yf
    except ImportError:
        logger.debug("yfinance 未安装,跳过 yfinance K线回退")
        return []

    ticker = _normalize_yfinance_symbol(sym, market)
    period = _yfinance_period(need_days)
    last_err = None
    out: list[KlineData] = []
    for attempt in range(2):
        try:
            hist = yf.Ticker(ticker).history(
                period=period, interval="1d", auto_adjust=True
            )
            for idx, row in hist.iterrows():
                try:
                    o = float(row["Open"])
                    c = float(row["Close"])
                    h = float(row["High"])
                    l = float(row["Low"])
                    if not all(math.isfinite(x) for x in (o, c, h, l)):
                        continue
                    out.append(
                        KlineData(
                            date=idx.strftime("%Y-%m-%d"),
                            open=o,
                            close=c,
                            high=h,
                            low=l,
                            volume=float(row.get("Volume") or 0),
                        )
                    )
                except Exception:
                    continue
            if out:
                break
            last_err = "空响应"
        except Exception as e:
            last_err = e
            time.sleep(0.35 * (attempt + 1))

    if not out:
        if last_err is not None:
            logger.warning(
                f"yfinance 获取 {symbol} K线失败: {last_err}{_source_suffix()}"
            )
        stale = _YFINANCE_CACHE.get(cache_key)
        if stale:
            bars = stale[2]
            return bars[-need_days:] if len(bars) > need_days else bars
        return []

    _YFINANCE_CACHE[cache_key] = (now, len(out), out)
    return out[-need_days:] if len(out) > need_days else out


def _eastmoney_secid(symbol: str, market: MarketCode) -> str:
    if market == MarketCode.HK:
        return f"116.{symbol}"
    if market == MarketCode.US:
        return f"105.{symbol}"
    prefix = "1" if is_cn_sh(symbol) else "0"
    return f"{prefix}.{symbol}"


# 指数 secid(东财):指数与个股 secid 前缀规则不同,必须显式映射,否则按个股规则会取错标的。
# 美股指数东财K线不支持(_fetch_eastmoney_klines 仅 CN/HK),未列入 → get_index_klines 返回空,fail-soft。
INDEX_SECID: dict[str, str] = {
    "000300": "1.000300",   # 沪深300
    "000001": "1.000001",   # 上证指数
    "399001": "0.399001",   # 深证成指
    "399006": "0.399006",   # 创业板指
    "HSI": "100.HSI",       # 恒生指数
}


def get_index_klines(index_code: str, market: MarketCode, days: int = 120) -> list[KlineData]:
    """取大盘/指数日K(东财)。指数 secid 需显式映射;未映射(如美股指数)→ 空列表(fail-soft)。"""
    code = str(index_code or "").strip()
    secid = INDEX_SECID.get(code) or INDEX_SECID.get(code.upper())
    if not secid:
        return []
    try:
        return _fetch_eastmoney_klines(code, market, days, secid_override=secid)
    except Exception as e:
        logger.debug(f"指数K线获取失败 {index_code}: {e}")
        return []


def _eastmoney_referer(symbol: str, market: MarketCode) -> str:
    sym = (symbol or "").strip()
    if market == MarketCode.US:
        return f"https://quote.eastmoney.com/us/{sym}.html"
    if market == MarketCode.HK:
        return f"https://quote.eastmoney.com/hk/{sym}.html"
    return "https://quote.eastmoney.com/"


def _fetch_eastmoney_klines(
    symbol: str, market: MarketCode, days: int, *, secid_override: str | None = None
) -> list[KlineData]:
    """Fetch daily kline from Eastmoney as CN/HK/US fallback."""

    sym = (symbol or "").strip()
    if not sym:
        return []
    if market not in (MarketCode.CN, MarketCode.HK, MarketCode.US):
        return []

    need_days = max(1, int(days or 1))
    # secid 唯一标识标的(指数与个股前缀规则不同);以 secid 作缓存键,
    # 避免指数与同号个股(如 000001 既是平安银行又是上证指数)缓存串味。
    secid = secid_override or _eastmoney_secid(sym, market)
    cache_key = f"{market.value}:{secid}"
    now = time.time()
    if now < _EASTMONEY_FAIL_UNTIL.get(cache_key, 0.0):
        stale = _EASTMONEY_CACHE.get(cache_key)
        if stale:
            bars = stale[2]
            return bars[-need_days:] if len(bars) > need_days else bars
        return []
    cached = _EASTMONEY_CACHE.get(cache_key)
    if (
        cached
        and (now - cached[0]) < _EASTMONEY_CACHE_TTL_SECONDS
        and cached[1] >= need_days
    ):
        bars = cached[2]
        return bars[-need_days:] if len(bars) > need_days else bars

    min_lmt = 120 if market == MarketCode.US else 1200
    params = {
        "secid": secid,
        "klt": "101",  # 1日K
        "fqt": "1",  # 前复权
        "lmt": str(min(max(need_days, min_lmt), 20000)),
        "end": "20500101",
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56",
        "ut": "fa5fd1943c7b386f172d6893dbfba10b",
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": _eastmoney_referer(sym, market),
    }

    last_err = None
    best: list[KlineData] = []
    for attempt in range(2):
        _throttle_eastmoney()
        try:
            with httpx.Client(
                follow_redirects=True,
                timeout=12 + attempt * 6,
                headers=headers,
                trust_env=False,  # 行情直连,绕过 env 代理(生产代理会拦 push2his.eastmoney)
            ) as client:
                resp = client.get(EASTMONEY_KLINE_URL, params=params)
                resp.raise_for_status()
                payload = resp.json()

            raw = (
                (payload or {}).get("data", {}).get("klines", [])
                if isinstance(payload, dict)
                else []
            )
            out: list[KlineData] = []
            for row in raw or []:
                # row format: "YYYY-MM-DD,open,close,high,low,volume,..."
                parts = str(row).split(",")
                if len(parts) < 6:
                    continue
                try:
                    out.append(
                        KlineData(
                            date=parts[0],
                            open=float(parts[1]),
                            close=float(parts[2]),
                            high=float(parts[3]),
                            low=float(parts[4]),
                            volume=float(parts[5]),
                        )
                    )
                except Exception:
                    continue
            if len(out) > len(best):
                best = out
            if best:
                break
        except Exception as e:
            last_err = e
            time.sleep(0.35 * (attempt + 1))

    if not best and last_err is not None:
        _EASTMONEY_FAIL_UNTIL[cache_key] = now + _EASTMONEY_FAIL_COOLDOWN_S
        logger.warning(
            f"Eastmoney 获取 {symbol} K线失败: {last_err}{_source_suffix()}"
        )
        stale = _EASTMONEY_CACHE.get(cache_key)
        if stale:
            bars = stale[2]
            return bars[-need_days:] if len(bars) > need_days else bars
        return []

    _EASTMONEY_FAIL_UNTIL.pop(cache_key, None)
    _EASTMONEY_CACHE[cache_key] = (now, len(best), best)
    return best[-need_days:] if len(best) > need_days else best


# 东财分钟 K 周期: klt=5/30/60
_EASTMONEY_INTRADAY_KLT = {
    "m5": 5,
    "m30": 30,
    "m60": 60,
}


def _fetch_eastmoney_intraday_klines(
    symbol: str,
    market: MarketCode,
    interval: str,
    count: int,
) -> list[KlineData]:
    """东财分钟 K 线兜底(CN/HK),腾讯分钟线失败时使用。"""
    iv = (interval or "m30").lower()
    klt = _EASTMONEY_INTRADAY_KLT.get(iv)
    if not klt or market not in (MarketCode.CN, MarketCode.HK):
        return []

    sym = (symbol or "").strip()
    if not sym:
        return []

    need = max(10, min(int(count or 240), 2000))
    secid = _eastmoney_secid(sym, market)
    cache_key = f"{market.value}:{secid}:klt{klt}"
    now = time.time()
    if now < _EASTMONEY_FAIL_UNTIL.get(cache_key, 0.0):
        stale = _EASTMONEY_CACHE.get(cache_key)
        if stale:
            bars = stale[2]
            return bars[-need:] if len(bars) > need else bars
        return []
    cached = _EASTMONEY_CACHE.get(cache_key)
    if (
        cached
        and (now - cached[0]) < _EASTMONEY_CACHE_TTL_SECONDS
        and cached[1] >= need
    ):
        bars = cached[2]
        return bars[-need:] if len(bars) > need else bars

    params = {
        "secid": secid,
        "klt": str(klt),
        "fqt": "1",
        "lmt": str(need),
        "end": "20500101",
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56",
        "ut": "fa5fd1943c7b386f172d6893dbfba10b",
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": _eastmoney_referer(sym, market),
    }

    last_err = None
    best: list[KlineData] = []
    for attempt in range(2):
        _throttle_eastmoney()
        try:
            with httpx.Client(
                follow_redirects=True,
                timeout=12 + attempt * 6,
                headers=headers,
                trust_env=False,
            ) as client:
                resp = client.get(EASTMONEY_KLINE_URL, params=params)
                resp.raise_for_status()
                payload = resp.json()

            raw = (
                (payload or {}).get("data", {}).get("klines", [])
                if isinstance(payload, dict)
                else []
            )
            out: list[KlineData] = []
            for row in raw or []:
                parts = str(row).split(",")
                if len(parts) < 6:
                    continue
                try:
                    out.append(
                        KlineData(
                            date=parts[0],
                            open=float(parts[1]),
                            close=float(parts[2]),
                            high=float(parts[3]),
                            low=float(parts[4]),
                            volume=float(parts[5]),
                        )
                    )
                except Exception:
                    continue
            if len(out) > len(best):
                best = out
            if best:
                break
        except Exception as e:
            last_err = e
            time.sleep(0.35 * (attempt + 1))

    if not best and last_err is not None:
        _EASTMONEY_FAIL_UNTIL[cache_key] = now + _EASTMONEY_FAIL_COOLDOWN_S
        logger.warning(
            f"Eastmoney 获取 {symbol} {iv} K线失败: {last_err}{_source_suffix()}"
        )
        stale = _EASTMONEY_CACHE.get(cache_key)
        if stale:
            bars = stale[2]
            return bars[-need:] if len(bars) > need else bars
        return []

    _EASTMONEY_FAIL_UNTIL.pop(cache_key, None)
    _EASTMONEY_CACHE[cache_key] = (now, len(best), best)
    return best[-need:] if len(best) > need else best


# Baostock 分钟频率(A 股专用,CN only)
_BAOSTOCK_INTRADAY_FREQ = {
    "m5": "5",
    "m30": "30",
    "m60": "60",
}
_BAOSTOCK_CACHE: dict[str, tuple[float, int, list["KlineData"]]] = {}
_BAOSTOCK_FAIL_UNTIL: dict[str, float] = {}
_BAOSTOCK_FAIL_COOLDOWN_S = 120.0
_BAOSTOCK_LOCK = threading.Lock()
_BAOSTOCK_LOGGED_IN = False


def _baostock_code(symbol: str) -> str | None:
    """A 股代码 → baostock 格式 sh.600519 / sz.000725 / bj.920xxx。"""
    sym = (symbol or "").strip()
    if not sym.isdigit() or len(sym) != 6:
        return None
    prefix = get_cn_prefix(sym)
    return f"{prefix}.{sym}"


def _baostock_lookback_days(interval: str, count: int) -> int:
    """按周期估算 start_date 回溯日历天数。"""
    iv = (interval or "m30").lower()
    need = max(10, int(count or 240))
    if iv == "m5":
        trading_days = max(3, (need + 47) // 48)
        return int(trading_days * 1.6) + 5
    if iv == "m30":
        trading_days = max(3, (need + 7) // 8)
        return int(trading_days * 1.6) + 10
    return 45


def _format_baostock_datetime(date: str, time_str: str) -> str:
    t = (time_str or "").strip()
    if len(t) >= 12:
        return f"{date} {t[8:10]}:{t[10:12]}"
    return date


def _baostock_ensure_login() -> bool:
    global _BAOSTOCK_LOGGED_IN
    with _BAOSTOCK_LOCK:
        if _BAOSTOCK_LOGGED_IN:
            return True
        try:
            import baostock as bs
        except ImportError:
            logger.debug("baostock 未安装,跳过 Baostock K线回退")
            return False
        lg = bs.login()
        if lg.error_code != "0":
            logger.warning(f"Baostock login 失败: {lg.error_msg}{_source_suffix()}")
            return False
        _BAOSTOCK_LOGGED_IN = True
        return True


def _fetch_baostock_intraday_klines(
    symbol: str,
    interval: str,
    count: int,
) -> list[KlineData]:
    """Baostock 分钟 K 线兜底(仅 A 股),东财/腾讯分钟线失败时使用。"""
    iv = (interval or "m30").lower()
    freq = _BAOSTOCK_INTRADAY_FREQ.get(iv)
    code = _baostock_code(symbol)
    if not freq or not code:
        return []

    need = max(10, min(int(count or 240), 2000))
    cache_key = f"CN:{code}:{freq}:{need}"
    now = time.time()
    if now < _BAOSTOCK_FAIL_UNTIL.get(cache_key, 0.0):
        stale = _BAOSTOCK_CACHE.get(cache_key)
        if stale:
            bars = stale[2]
            return bars[-need:] if len(bars) > need else bars
        return []
    cached = _BAOSTOCK_CACHE.get(cache_key)
    if (
        cached
        and (now - cached[0]) < _EASTMONEY_CACHE_TTL_SECONDS
        and cached[1] >= need
    ):
        bars = cached[2]
        return bars[-need:] if len(bars) > need else bars

    if not _baostock_ensure_login():
        return []

    try:
        import baostock as bs
        from datetime import datetime, timedelta

        end = beijing_now().strftime("%Y-%m-%d")
        start = (beijing_now() - timedelta(days=_baostock_lookback_days(iv, need))).strftime(
            "%Y-%m-%d"
        )
        rs = bs.query_history_k_data_plus(
            code,
            "date,time,open,high,low,close,volume",
            start_date=start,
            end_date=end,
            frequency=freq,
            adjustflag="2",
        )
        if rs.error_code != "0":
            raise RuntimeError(rs.error_msg or rs.error_code)

        out: list[KlineData] = []
        while rs.error_code == "0" and rs.next():
            row = rs.get_row_data()
            if not row or len(row) < 7:
                continue
            date_s, time_s, o, h, l, c, v = row[:7]
            try:
                out.append(
                    KlineData(
                        date=_format_baostock_datetime(date_s, time_s),
                        open=float(o),
                        close=float(c),
                        high=float(h),
                        low=float(l),
                        volume=float(v or 0),
                    )
                )
            except Exception:
                continue
    except Exception as e:
        _BAOSTOCK_FAIL_UNTIL[cache_key] = now + _BAOSTOCK_FAIL_COOLDOWN_S
        logger.warning(
            f"Baostock 获取 {symbol} {iv} K线失败: {e}{_source_suffix()}"
        )
        stale = _BAOSTOCK_CACHE.get(cache_key)
        if stale:
            bars = stale[2]
            return bars[-need:] if len(bars) > need else bars
        return []

    _BAOSTOCK_FAIL_UNTIL.pop(cache_key, None)
    _BAOSTOCK_CACHE[cache_key] = (now, len(out), out)
    return out[-need:] if len(out) > need else out


def _fetch_baostock_daily_klines(symbol: str, days: int) -> list[KlineData]:
    """Baostock 日 K 兜底(仅 A 股),腾讯/东财日 K 失败时使用。"""
    code = _baostock_code(symbol)
    if not code:
        return []

    need = max(1, min(int(days or 60), 5000))
    cache_key = f"CN:{code}:d:{need}"
    now = time.time()
    if now < _BAOSTOCK_FAIL_UNTIL.get(cache_key, 0.0):
        stale = _BAOSTOCK_CACHE.get(cache_key)
        if stale:
            bars = stale[2]
            return bars[-need:] if len(bars) > need else bars
        return []
    cached = _BAOSTOCK_CACHE.get(cache_key)
    if (
        cached
        and (now - cached[0]) < _EASTMONEY_CACHE_TTL_SECONDS
        and cached[1] >= need
    ):
        bars = cached[2]
        return bars[-need:] if len(bars) > need else bars

    if not _baostock_ensure_login():
        return []

    try:
        import baostock as bs
        from datetime import datetime, timedelta

        end = beijing_now().strftime("%Y-%m-%d")
        lookback = max(int(need * 1.6) + 30, 365)
        start = (beijing_now() - timedelta(days=lookback)).strftime("%Y-%m-%d")
        rs = bs.query_history_k_data_plus(
            code,
            "date,open,high,low,close,volume",
            start_date=start,
            end_date=end,
            frequency="d",
            adjustflag="2",
        )
        if rs.error_code != "0":
            raise RuntimeError(rs.error_msg or rs.error_code)

        out: list[KlineData] = []
        while rs.error_code == "0" and rs.next():
            row = rs.get_row_data()
            if not row or len(row) < 6:
                continue
            date_s, o, h, l, c, v = row[:6]
            try:
                out.append(
                    KlineData(
                        date=date_s,
                        open=float(o),
                        close=float(c),
                        high=float(h),
                        low=float(l),
                        volume=float(v or 0),
                    )
                )
            except Exception:
                continue
    except Exception as e:
        _BAOSTOCK_FAIL_UNTIL[cache_key] = now + _BAOSTOCK_FAIL_COOLDOWN_S
        logger.warning(
            f"Baostock 获取 {symbol} 日K失败: {e}{_source_suffix()}"
        )
        stale = _BAOSTOCK_CACHE.get(cache_key)
        if stale:
            bars = stale[2]
            return bars[-need:] if len(bars) > need else bars
        return []

    _BAOSTOCK_FAIL_UNTIL.pop(cache_key, None)
    _BAOSTOCK_CACHE[cache_key] = (now, len(out), out)
    return out[-need:] if len(out) > need else out


@dataclass
class KlineData:
    """K线数据"""

    date: str
    open: float
    close: float
    high: float
    low: float
    volume: float


@dataclass
class IntradayTrendPoint:
    """当日分时点(逐分钟价格)"""

    time: str
    price: float
    avg_price: float | None = None
    volume: float = 0
    turnover: float = 0


@dataclass
class IntradayTrendsResult:
    """当日分时曲线"""

    symbol: str
    market: str
    trade_date: str
    pre_close: float | None
    points: list[IntradayTrendPoint]
    updated_at: str


_INTRADAY_TRENDS_CACHE: dict[str, tuple[float, IntradayTrendsResult]] = {}
_INTRADAY_TRENDS_TTL_TRADING_S = 15.0
_INTRADAY_TRENDS_TTL_CLOSED_S = 300.0


@dataclass
class TechnicalIndicators:
    """技术指标"""

    # 均线
    ma5: float | None = None
    ma10: float | None = None
    ma20: float | None = None
    ma60: float | None = None
    # MACD
    macd_dif: float | None = None
    macd_dea: float | None = None
    macd_hist: float | None = None
    macd_cross: str | None = None  # 金叉/死叉
    macd_cross_days: int | None = None  # 距离上次交叉天数
    # RSI
    rsi6: float | None = None
    rsi12: float | None = None
    rsi24: float | None = None
    # KDJ
    kdj_k: float | None = None
    kdj_d: float | None = None
    kdj_j: float | None = None
    kdj_cross: str | None = None  # 金叉/死叉
    # 布林带
    boll_upper: float | None = None
    boll_mid: float | None = None
    boll_lower: float | None = None
    boll_width: float | None = None  # 带宽百分比
    # 量能
    volume_ratio: float | None = None  # 量比（今日成交量/5日均量）
    volume_ma5: float | None = None
    volume_ma10: float | None = None
    volume_trend: str | None = None  # 放量/缩量/平量
    # 涨跌幅
    change_5d: float | None = None
    change_20d: float | None = None
    # 振幅
    amplitude: float | None = None  # 今日振幅
    amplitude_avg5: float | None = None  # 5日平均振幅
    # 波动率(ATR)
    atr: float | None = None  # 平均真实波幅(绝对值)
    atr_pct: float | None = None  # ATR / 最新收盘 * 100(相对波动率%)
    # 支撑压力（多级别）
    support_s: float | None = None  # 短期支撑（5日）
    support_m: float | None = None  # 中期支撑（20日）
    support_l: float | None = None  # 长期支撑（60日）
    resistance_s: float | None = None  # 短期压力
    resistance_m: float | None = None  # 中期压力
    resistance_l: float | None = None  # 长期压力
    # 兼容旧字段
    support: float | None = None
    resistance: float | None = None
    # K线形态
    kline_pattern: str | None = None  # 十字星/锤子线/吞没等


def _tencent_symbol(symbol: str, market: MarketCode) -> str:
    """转换为腾讯 API 格式"""
    if market == MarketCode.HK:
        return f"hk{symbol}"
    if market == MarketCode.US:
        return f"us{symbol}"
    return get_cn_prefix(symbol) + symbol


def _calculate_ma(closes: list[float], period: int) -> float | None:
    if len(closes) < period:
        return None
    return sum(closes[-period:]) / period


def _ema(data: list[float], period: int) -> list[float]:
    """计算 EMA"""
    if not data:
        return []
    result = [data[0]]
    multiplier = 2 / (period + 1)
    for price in data[1:]:
        result.append((price - result[-1]) * multiplier + result[-1])
    return result


def _calculate_atr(klines: list[KlineData], period: int = 14) -> float | None:
    """计算 ATR(平均真实波幅)。

    TR = max(high-low, |high-prevClose|, |low-prevClose|)。
    与本模块其它指标一致,取最近 period 个 TR 的简单均值(非 Wilder 递归平滑),
    便于复现与手算校验。

    需要至少 period+1 根 K 线(才能算出 period 个含前收的 TR);
    数据不足或异常一律返回 None,不抛异常(fail-soft)。
    """
    try:
        if not klines or len(klines) < period + 1:
            return None
        trs: list[float] = []
        for i in range(1, len(klines)):
            cur = klines[i]
            prev_close = klines[i - 1].close
            tr = max(
                cur.high - cur.low,
                abs(cur.high - prev_close),
                abs(cur.low - prev_close),
            )
            trs.append(tr)
        if len(trs) < period:
            return None
        return sum(trs[-period:]) / period
    except Exception:
        return None


def _calculate_macd(
    closes: list[float], fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[list[float], list[float], list[float]] | None:
    """计算 MACD，返回完整序列用于判断交叉"""
    if len(closes) < slow + signal:
        return None

    ema_fast = _ema(closes, fast)
    ema_slow = _ema(closes, slow)
    dif = [f - s for f, s in zip(ema_fast, ema_slow)]
    dea = _ema(dif, signal)
    macd_hist = [(d - e) * 2 for d, e in zip(dif, dea)]
    return dif, dea, macd_hist


def _calculate_rsi(closes: list[float], period: int) -> float | None:
    """计算 RSI"""
    if len(closes) < period + 1:
        return None

    gains = []
    losses = []
    for i in range(1, len(closes)):
        change = closes[i] - closes[i - 1]
        if change > 0:
            gains.append(change)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(change))

    # 使用最近 period 天计算
    avg_gain = sum(gains[-period:]) / period
    avg_loss = sum(losses[-period:]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _calculate_kdj(
    klines: list[KlineData], n: int = 9, m1: int = 3, m2: int = 3
) -> tuple[list[float], list[float], list[float]] | None:
    """计算 KDJ，返回完整序列"""
    if len(klines) < n:
        return None

    k_values = []
    d_values = []
    j_values = []

    for i in range(n - 1, len(klines)):
        period_klines = klines[i - n + 1 : i + 1]
        highest = max(k.high for k in period_klines)
        lowest = min(k.low for k in period_klines)
        close = klines[i].close

        if highest == lowest:
            rsv = 50
        else:
            rsv = (close - lowest) / (highest - lowest) * 100

        if not k_values:
            k = 50
            d = 50
        else:
            k = (2 / 3) * k_values[-1] + (1 / 3) * rsv
            d = (2 / 3) * d_values[-1] + (1 / 3) * k

        j = 3 * k - 2 * d

        k_values.append(k)
        d_values.append(d)
        j_values.append(j)

    return k_values, d_values, j_values


def _calculate_boll(
    closes: list[float], period: int = 20, num_std: int = 2
) -> tuple[float, float, float, float] | None:
    """计算布林带：上轨、中轨、下轨、带宽"""
    if len(closes) < period:
        return None

    recent = closes[-period:]
    mid = sum(recent) / period
    variance = sum((x - mid) ** 2 for x in recent) / period
    std = variance**0.5

    upper = mid + num_std * std
    lower = mid - num_std * std
    width = (upper - lower) / mid * 100 if mid > 0 else 0

    return upper, mid, lower, width


def _detect_kline_pattern(klines: list[KlineData]) -> str | None:
    """检测 K 线形态"""
    if len(klines) < 2:
        return None

    curr = klines[-1]
    prev = klines[-2]

    body = abs(curr.close - curr.open)
    upper_shadow = curr.high - max(curr.close, curr.open)
    lower_shadow = min(curr.close, curr.open) - curr.low
    total_range = curr.high - curr.low

    if total_range == 0:
        return None

    body_ratio = body / total_range

    # 十字星：实体很小
    if body_ratio < 0.1:
        if upper_shadow > body * 2 and lower_shadow > body * 2:
            return "十字星"
        elif upper_shadow > body * 3:
            return "倒T字"
        elif lower_shadow > body * 3:
            return "T字线"

    # 锤子线：下影线很长，实体在上方
    if lower_shadow > body * 2 and upper_shadow < body * 0.5:
        if curr.close > curr.open:
            return "锤子线(阳)"
        else:
            return "锤子线(阴)"

    # 倒锤子：上影线很长
    if upper_shadow > body * 2 and lower_shadow < body * 0.5:
        if curr.close > curr.open:
            return "倒锤子(阳)"
        else:
            return "射击之星"

    # 吞没形态
    prev_body = abs(prev.close - prev.open)
    if body > prev_body * 1.5:
        if prev.close < prev.open and curr.close > curr.open:  # 前阴后阳
            if curr.close > prev.open and curr.open < prev.close:
                return "看涨吞没"
        elif prev.close > prev.open and curr.close < curr.open:  # 前阳后阴
            if curr.open > prev.close and curr.close < prev.open:
                return "看跌吞没"

    # 大阳线/大阴线
    if body_ratio > 0.7:
        change_pct = (curr.close - curr.open) / curr.open * 100 if curr.open > 0 else 0
        if change_pct > 3:
            return "大阳线"
        elif change_pct < -3:
            return "大阴线"

    return None


def _find_cross_days(
    series1: list[float], series2: list[float], cross_type: str
) -> int | None:
    """找到最近一次交叉距今的天数"""
    if len(series1) < 2 or len(series2) < 2:
        return None

    for i in range(len(series1) - 2, -1, -1):
        if cross_type == "金叉":
            # 金叉：series1 从下方穿越 series2
            if series1[i] <= series2[i] and series1[i + 1] > series2[i + 1]:
                return len(series1) - 1 - i
        else:
            # 死叉：series1 从上方穿越 series2
            if series1[i] >= series2[i] and series1[i + 1] < series2[i + 1]:
                return len(series1) - 1 - i

    return None


# 腾讯 gtimg 在批量/并发突发下会限流回空 body —— 进程级最小间隔节流 + 重试退避兜底。
_TENCENT_MIN_INTERVAL_S = 0.15
_TENCENT_THROTTLE_LOCK = threading.Lock()
_tencent_last_call = [0.0]


def _throttle_tencent() -> None:
    """进程级限速:保证腾讯行情请求间隔 ≥ _TENCENT_MIN_INTERVAL_S,平滑顺序/并发突发。"""
    with _TENCENT_THROTTLE_LOCK:
        wait = _TENCENT_MIN_INTERVAL_S - (time.time() - _tencent_last_call[0])
        if wait > 0:
            time.sleep(wait)
        _tencent_last_call[0] = time.time()


# 东方财富 push2his 在批量突发下会连接级丢弃(Server disconnected) —— 同样做进程级节流。
_EASTMONEY_MIN_INTERVAL_S = 0.35
_EASTMONEY_THROTTLE_LOCK = threading.Lock()
_eastmoney_last_call = [0.0]


def _throttle_eastmoney() -> None:
    """进程级限速:东方财富兜底请求间隔 ≥ _EASTMONEY_MIN_INTERVAL_S,缓解批量突发被连接级拒绝。"""
    with _EASTMONEY_THROTTLE_LOCK:
        wait = _EASTMONEY_MIN_INTERVAL_S - (time.time() - _eastmoney_last_call[0])
        if wait > 0:
            time.sleep(wait)
        _eastmoney_last_call[0] = time.time()


_INTRADAY_INTERVAL_MAP = {
    "m5": ("m5", "qfqm5", 240),
    "m30": ("m30", "qfqm30", 240),
    "m60": ("m60", "qfqm60", 240),
}


def _fetch_tencent_klines_interval(
    symbol: str,
    market: MarketCode,
    interval: str,
    count: int,
) -> list[KlineData]:
    """腾讯 K 线：day / m5 / m30 等周期。"""
    tencent_sym = _tencent_symbol(symbol, market)
    iv = (interval or "day").lower()
    if iv == "day":
        param_iv = "day"
        var_name = "kline_dayqfq"
        data_keys = ("day", "qfqday")
    else:
        mapped = _INTRADAY_INTERVAL_MAP.get(iv)
        if not mapped:
            return []
        param_iv, qfq_key, default_count = mapped
        count = min(max(10, int(count or default_count)), 640)
        var_name = f"kline_{qfq_key}"
        data_keys = (param_iv, qfq_key)

    params = {
        "param": f"{tencent_sym},{param_iv},,,{count},qfq",
        "_var": var_name,
    }
    klines: list[KlineData] = []
    last_err = None
    for attempt in range(3):
        _throttle_tencent()
        try:
            with httpx.Client(
                follow_redirects=True, timeout=10 + attempt * 4, trust_env=False
            ) as client:
                resp = client.get(TENCENT_KLINE_URL, params=params)
                text = resp.text
            klines = _parse_tencent_kline_text(
                text, tencent_sym, data_keys=data_keys
            )
            if klines:
                break
            last_err = "空响应"
        except Exception as e:
            last_err = e
        if attempt < 2:
            time.sleep(0.4 * (attempt + 1) + random.uniform(0, 0.25))

    if not klines and last_err is not None:
        logger.warning(
            f"腾讯 {param_iv} K线获取失败(已重试)symbol={symbol}: {last_err}{_source_suffix()}"
        )
    return klines


def _fetch_tencent_klines(
    symbol: str, market: MarketCode, days: int
) -> list[KlineData]:
    """腾讯主路径取日K:进程级节流 + 空响应/异常退避重试(gtimg 批量突发常限流回空 body,重试可自愈)。"""
    return _fetch_tencent_klines_interval(symbol, market, "day", days)


def _intraday_trends_cache_ttl(market: MarketCode) -> float:
    try:
        md = MARKETS.get(market)
        if md and md.is_trading_time():
            return _INTRADAY_TRENDS_TTL_TRADING_S
    except Exception:
        pass
    return _INTRADAY_TRENDS_TTL_CLOSED_S


def _parse_tencent_minute_time(trade_date: str, hhmm: str) -> str | None:
    td = (trade_date or "").strip()
    hm = (hhmm or "").strip()
    if len(hm) < 4 or not hm[:4].isdigit():
        return None
    hh, mm = hm[:2], hm[2:4]
    if len(td) == 8 and td.isdigit():
        td = f"{td[:4]}-{td[4:6]}-{td[6:8]}"
    if td and len(td) >= 10 and td[4] == "-":
        return f"{td[:10]} {hh}:{mm}"
    today = beijing_now().strftime("%Y-%m-%d")
    return f"{today} {hh}:{mm}"


def _fetch_tencent_intraday_trends(
    symbol: str, market: MarketCode
) -> IntradayTrendsResult | None:
    """腾讯当日分时(逐分钟),CN/HK/US 通用。"""
    tencent_sym = _tencent_symbol(symbol, market)
    params = {"code": tencent_sym}
    last_err = None
    for attempt in range(3):
        _throttle_tencent()
        try:
            with httpx.Client(
                follow_redirects=True, timeout=10 + attempt * 4, trust_env=False
            ) as client:
                resp = client.get(TENCENT_MINUTE_URL, params=params)
                payload = resp.json()
            if not isinstance(payload, dict) or payload.get("code") not in (0, None):
                last_err = payload.get("msg") or "无效响应"
                continue
            block = (payload.get("data") or {}).get(tencent_sym) or {}
            minute_block = block.get("data") or {}
            raw_rows = minute_block.get("data") or []
            trade_date = str(minute_block.get("date") or "").strip()
            qt_row = (block.get("qt") or {}).get(tencent_sym) or []
            pre_close = None
            if isinstance(qt_row, list) and len(qt_row) > 4:
                try:
                    pre_close = float(qt_row[4])
                except (TypeError, ValueError):
                    pre_close = None
            points: list[IntradayTrendPoint] = []
            prev_cum_volume = 0.0
            for row in raw_rows or []:
                parts = str(row).split()
                if len(parts) < 2:
                    continue
                t = _parse_tencent_minute_time(trade_date, parts[0])
                if not t:
                    continue
                try:
                    price = float(parts[1])
                    cum_volume = float(parts[2]) if len(parts) > 2 else 0.0
                    turnover = float(parts[3]) if len(parts) > 3 else 0.0
                except (TypeError, ValueError):
                    continue
                volume = cum_volume
                if prev_cum_volume > 0 and cum_volume >= prev_cum_volume:
                    volume = cum_volume - prev_cum_volume
                prev_cum_volume = cum_volume
                avg_price = None
                if cum_volume > 0 and turnover > 0:
                    # 腾讯分时 turnover/volume 为累计值,均价用累计额/累计量
                    lot_size = 100.0 if market in (MarketCode.CN, MarketCode.HK) else 1.0
                    shares = cum_volume * lot_size
                    if shares > 0:
                        avg_price = turnover / shares
                points.append(
                    IntradayTrendPoint(
                        time=t,
                        price=price,
                        avg_price=avg_price,
                        volume=volume,
                        turnover=turnover,
                    )
                )
            if not trade_date and points:
                trade_date = points[0].time.split(" ", 1)[0]
            if len(trade_date) == 8 and trade_date.isdigit():
                trade_date = f"{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:8]}"
            if not points:
                last_err = "空分时"
                continue
            now_iso = beijing_now().isoformat(timespec="seconds")
            return IntradayTrendsResult(
                symbol=symbol,
                market=market.value,
                trade_date=trade_date or beijing_now().strftime("%Y-%m-%d"),
                pre_close=pre_close,
                points=points,
                updated_at=now_iso,
            )
        except Exception as e:
            last_err = e
        if attempt < 2:
            time.sleep(0.35 * (attempt + 1) + random.uniform(0, 0.2))
    if last_err is not None:
        logger.warning(
            f"腾讯分时获取失败 symbol={symbol}: {last_err}{_source_suffix()}"
        )
    return None


def _fetch_eastmoney_intraday_trends(
    symbol: str, market: MarketCode
) -> IntradayTrendsResult | None:
    """东财当日分时兜底(CN/HK)。"""
    if market not in (MarketCode.CN, MarketCode.HK):
        return None
    sym = (symbol or "").strip()
    if not sym:
        return None
    secid = _eastmoney_secid(sym, market)
    params = {
        "secid": secid,
        "fields1": "f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12,f13",
        "fields2": "f51,f52,f53,f54,f55,f56,f57,f58",
        "iscr": "0",
        "ndays": "1",
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": _eastmoney_referer(sym, market),
    }
    last_err = None
    for attempt in range(2):
        _throttle_eastmoney()
        try:
            with httpx.Client(
                follow_redirects=True,
                timeout=12 + attempt * 6,
                headers=headers,
                trust_env=False,
            ) as client:
                resp = client.get(EASTMONEY_TRENDS_URL, params=params)
                resp.raise_for_status()
                payload = resp.json()
            data = (payload or {}).get("data") if isinstance(payload, dict) else None
            if not isinstance(data, dict):
                last_err = "无效响应"
                continue
            pre_close = None
            try:
                if data.get("preClose") is not None:
                    pre_close = float(data["preClose"])
            except (TypeError, ValueError):
                pre_close = None
            points: list[IntradayTrendPoint] = []
            trade_date = ""
            for row in data.get("trends") or []:
                parts = str(row).split(",")
                if len(parts) < 6:
                    continue
                t = parts[0].strip()
                if not trade_date and " " in t:
                    trade_date = t.split(" ", 1)[0]
                try:
                    price = float(parts[2])
                    volume = float(parts[5]) if len(parts) > 5 else 0.0
                    turnover = float(parts[6]) if len(parts) > 6 else 0.0
                    avg_price = float(parts[7]) if len(parts) > 7 and parts[7] else None
                except (TypeError, ValueError):
                    continue
                points.append(
                    IntradayTrendPoint(
                        time=t,
                        price=price,
                        avg_price=avg_price,
                        volume=volume,
                        turnover=turnover,
                    )
                )
            if not points:
                last_err = "空分时"
                continue
            now_iso = beijing_now().isoformat(timespec="seconds")
            return IntradayTrendsResult(
                symbol=symbol,
                market=market.value,
                trade_date=trade_date or beijing_now().strftime("%Y-%m-%d"),
                pre_close=pre_close,
                points=points,
                updated_at=now_iso,
            )
        except Exception as e:
            last_err = e
            time.sleep(0.35 * (attempt + 1))
    if last_err is not None:
        logger.warning(
            f"东财分时获取失败 symbol={symbol}: {last_err}{_source_suffix()}"
        )
    return None


def _parse_tencent_kline_text(
    text: str,
    tencent_sym: str,
    data_keys: tuple[str, ...] = ("day", "qfqday"),
) -> list[KlineData]:
    """解析腾讯 K 线 JS 变量响应(kline_dayqfq={...})为 KlineData;空/异常返回 []。"""
    if not text or "=" not in text:
        return []
    json_str = text.split("=", 1)[1].strip()
    if json_str.endswith(";"):
        json_str = json_str[:-1]
    try:
        data = json.loads(json_str)
    except Exception:
        return []
    raw_data = data.get("data", {}) if isinstance(data, dict) else {}
    day_data = []
    if isinstance(raw_data, dict):
        stock_data = raw_data.get(tencent_sym, {})
        if isinstance(stock_data, dict):
            day_data = []
            for key in data_keys:
                raw = stock_data.get(key)
                if raw:
                    day_data = raw
                    break
    elif isinstance(raw_data, list):
        day_data = raw_data
    out: list[KlineData] = []
    for item in day_data or []:
        if len(item) >= 5:
            try:
                out.append(
                    KlineData(
                        date=item[0],
                        open=float(item[1]),
                        close=float(item[2]),
                        high=float(item[3]),
                        low=float(item[4]),
                        volume=float(item[5]) if len(item) > 5 else 0,
                    )
                )
            except Exception:
                continue
    return out


class KlineCollector:
    """K线数据采集器（腾讯 API）"""

    def __init__(self, market: MarketCode):
        self.market = market

    def get_klines(self, symbol: str, days: int = 60) -> list[KlineData]:
        """获取日K线数据。

        正缓存(按市场状态 TTL)+ 同标的并发合并(只联网一次)+ 失败负缓存
        (源短暂故障时冷却窗口内不再联网),避免多消费者并发把数据源打爆。
        """
        cache_key = f"{self.market.value}:{symbol}"
        need = max(1, int(days or 1))

        # 1) 快路径:命中新鲜正缓存,无需加锁
        hit = self._cache_hit(cache_key, need)
        if hit is not None:
            return _slice_klines(hit, need)

        # 2) 同标的并发合并:仅一个线程实际联网,其余等待后复用结果
        with _get_fetch_lock(cache_key):
            hit = self._cache_hit(cache_key, need)
            if hit is not None:
                return _slice_klines(hit, need)

            now = time.time()
            # 3) 负缓存:刚失败过的标的,冷却窗口内返回陈旧/空,不再联网
            if now < _FAIL_UNTIL.get(cache_key, 0.0):
                stale = _KLINE_CACHE.get(cache_key)
                bars = stale[2] if stale else []
                if not (
                    (
                        self.market == MarketCode.US
                        and _us_kline_clearly_insufficient(bars, need)
                    )
                    or (
                        self.market == MarketCode.CN
                        and _cn_kline_insufficient_for_need(bars, need)
                    )
                ):
                    return _slice_klines(bars, need)

            klines = _filter_valid_klines(self._fetch_all_sources(symbol, days))
            if klines and len(klines) >= need:
                # 成功且条数足够:固化正缓存并清除冷却标记
                _KLINE_CACHE[cache_key] = (now, len(klines), list(klines))
                _FAIL_UNTIL.pop(cache_key, None)
            else:
                # 空 或 拿到部分但不足 need(常见:HK 腾讯不足 + eastmoney 补全失败,
                # 正缓存因 count<need 永不命中 → 每轮重打补全源刷屏)→ 固化冷却。
                # 部分结果仍缓存下来,冷却窗口内直接服务,避免反复联网。
                # 美股腾讯 1-2 条脏数据除外:不缓存、不长期负缓存,便于 yfinance 兜底重试。
                us_junk = (
                    self.market == MarketCode.US
                    and klines
                    and _us_kline_clearly_insufficient(klines, need)
                )
                if klines and not us_junk:
                    _KLINE_CACHE[cache_key] = (now, len(klines), list(klines))
                if not klines or us_junk:
                    _FAIL_UNTIL[cache_key] = now + min(_fail_cooldown(self.market), 15.0)
                else:
                    _FAIL_UNTIL[cache_key] = now + _fail_cooldown(self.market)
            return _slice_klines(klines, need)

    def _cache_hit(self, cache_key: str, need: int) -> list[KlineData] | None:
        """命中新鲜正缓存(TTL 内且条数足够)则返回切片,否则 None。"""
        cached = _KLINE_CACHE.get(cache_key)
        if (
            cached
            and (time.time() - cached[0]) < _kline_cache_ttl(self.market)
            and cached[1] >= need
        ):
            bars = cached[2]
            return bars[-need:] if len(bars) > need else bars
        return None

    def _fetch_all_sources(self, symbol: str, days: int) -> list[KlineData]:
        """tencent → eastmoney/yfinance(US) / eastmoney(CN/HK) / stooq(US) 链路取数。"""
        klines = _fetch_tencent_klines(symbol, self.market, days)

        min_us_bars = max(10, min(days, 30))
        # 美股:腾讯常只回 1-2 条;东财 push2his 批量突发易断连,直接走 yfinance/stooq。
        if self.market == MarketCode.US and len(klines) < min_us_bars:
            yf_bars = _fetch_yfinance_klines(symbol, self.market, max(days, 120))
            if len(yf_bars) > len(klines):
                klines = yf_bars
            if len(klines) < min_us_bars:
                fallback = _fetch_stooq_us_klines(symbol)
                if fallback:
                    klines = fallback

        # CN/HK: Tencent 不足时用 Eastmoney 补全更长历史(仅当确实不足)
        if self.market in (MarketCode.CN, MarketCode.HK):
            if len(klines) < max(120, int(days * 0.6)):
                em = _fetch_eastmoney_klines(
                    symbol, self.market, min(max(days, 3000), 20000)
                )
                if len(em) > len(klines):
                    klines = em

        # A 股:腾讯/东财均失败时用 Baostock 日 K 兜底
        if self.market == MarketCode.CN:
            need = max(1, int(days or 60))
            if len(klines) < max(10, min(need, int(days * 0.6))):
                bs = _fetch_baostock_daily_klines(symbol, need)
                if len(bs) > len(klines):
                    klines = bs

        return klines

    def get_technical_indicators(
        self, symbol: str = "", klines: list[KlineData] | None = None
    ) -> TechnicalIndicators:
        """计算技术指标(可传入已取的 klines 复用,避免重复联网)。"""
        if klines is None:
            klines = self.get_klines(symbol, days=120)

        if not klines:
            return TechnicalIndicators()

        closes = [k.close for k in klines]
        volumes = [k.volume for k in klines]

        # 均线
        ma5 = _calculate_ma(closes, 5)
        ma10 = _calculate_ma(closes, 10)
        ma20 = _calculate_ma(closes, 20)
        ma60 = _calculate_ma(closes, 60)

        # MACD
        macd_result = _calculate_macd(closes)
        macd_dif, macd_dea, macd_hist = None, None, None
        macd_cross, macd_cross_days = None, None
        if macd_result:
            dif_list, dea_list, hist_list = macd_result
            macd_dif = dif_list[-1]
            macd_dea = dea_list[-1]
            macd_hist = hist_list[-1]
            # 判断金叉/死叉
            if macd_dif > macd_dea:
                macd_cross = "金叉"
                macd_cross_days = _find_cross_days(dif_list, dea_list, "金叉")
            else:
                macd_cross = "死叉"
                macd_cross_days = _find_cross_days(dif_list, dea_list, "死叉")

        # RSI
        rsi6 = _calculate_rsi(closes, 6)
        rsi12 = _calculate_rsi(closes, 12)
        rsi24 = _calculate_rsi(closes, 24)

        # KDJ
        kdj_k, kdj_d, kdj_j = None, None, None
        kdj_cross = None
        kdj_result = _calculate_kdj(klines)
        if kdj_result:
            k_list, d_list, j_list = kdj_result
            kdj_k = k_list[-1]
            kdj_d = d_list[-1]
            kdj_j = j_list[-1]
            if kdj_k > kdj_d:
                kdj_cross = "金叉"
            else:
                kdj_cross = "死叉"

        # 布林带
        boll_upper, boll_mid, boll_lower, boll_width = None, None, None, None
        boll_result = _calculate_boll(closes)
        if boll_result:
            boll_upper, boll_mid, boll_lower, boll_width = boll_result

        # 量能分析
        volume_ma5 = _calculate_ma(volumes, 5) if volumes else None
        volume_ma10 = _calculate_ma(volumes, 10) if volumes else None
        volume_ratio = None
        volume_trend = None
        if volumes and volume_ma5 and volume_ma5 > 0:
            volume_ratio = volumes[-1] / volume_ma5
            if volume_ratio > 1.5:
                volume_trend = "放量"
            elif volume_ratio < 0.7:
                volume_trend = "缩量"
            else:
                volume_trend = "平量"

        # 涨跌幅
        change_5d = None
        change_20d = None
        if len(closes) >= 6:
            change_5d = (closes[-1] - closes[-6]) / closes[-6] * 100
        if len(closes) >= 21:
            change_20d = (closes[-1] - closes[-21]) / closes[-21] * 100

        # 振幅
        amplitude = None
        amplitude_avg5 = None
        if klines:
            curr = klines[-1]
            if curr.low > 0:
                amplitude = (curr.high - curr.low) / curr.low * 100
            if len(klines) >= 5:
                amps = []
                for k in klines[-5:]:
                    if k.low > 0:
                        amps.append((k.high - k.low) / k.low * 100)
                if amps:
                    amplitude_avg5 = sum(amps) / len(amps)

        # ATR(波动率):个股自身波动基准,供自适应异动判定使用
        atr = _calculate_atr(klines, period=14)
        atr_pct = None
        if atr is not None and closes and closes[-1]:
            atr_pct = round(atr / closes[-1] * 100, 2)

        # 多级支撑压力位
        support_s, support_m, support_l = None, None, None
        resistance_s, resistance_m, resistance_l = None, None, None
        if len(klines) >= 5:
            support_s = min(k.low for k in klines[-5:])
            resistance_s = max(k.high for k in klines[-5:])
        if len(klines) >= 20:
            support_m = min(k.low for k in klines[-20:])
            resistance_m = max(k.high for k in klines[-20:])
        if len(klines) >= 60:
            support_l = min(k.low for k in klines[-60:])
            resistance_l = max(k.high for k in klines[-60:])

        # 兼容旧字段
        support = support_m
        resistance = resistance_m

        # K线形态
        kline_pattern = _detect_kline_pattern(klines)

        return TechnicalIndicators(
            ma5=ma5,
            ma10=ma10,
            ma20=ma20,
            ma60=ma60,
            macd_dif=macd_dif,
            macd_dea=macd_dea,
            macd_hist=macd_hist,
            macd_cross=macd_cross,
            macd_cross_days=macd_cross_days,
            rsi6=rsi6,
            rsi12=rsi12,
            rsi24=rsi24,
            kdj_k=kdj_k,
            kdj_d=kdj_d,
            kdj_j=kdj_j,
            kdj_cross=kdj_cross,
            boll_upper=boll_upper,
            boll_mid=boll_mid,
            boll_lower=boll_lower,
            boll_width=boll_width,
            volume_ratio=volume_ratio,
            volume_ma5=volume_ma5,
            volume_ma10=volume_ma10,
            volume_trend=volume_trend,
            change_5d=change_5d,
            change_20d=change_20d,
            amplitude=amplitude,
            amplitude_avg5=amplitude_avg5,
            atr=atr,
            atr_pct=atr_pct,
            support_s=support_s,
            support_m=support_m,
            support_l=support_l,
            resistance_s=resistance_s,
            resistance_m=resistance_m,
            resistance_l=resistance_l,
            support=support,
            resistance=resistance,
            kline_pattern=kline_pattern,
        )

    def get_intraday_klines(
        self,
        symbol: str,
        interval: str = "m30",
        count: int = 240,
    ) -> list[KlineData]:
        """获取分钟级 K 线（m5/m30），用于多级别缠论分析。"""
        iv = (interval or "m30").lower()
        if iv not in _INTRADAY_INTERVAL_MAP:
            return []
        if self.market not in (MarketCode.CN, MarketCode.HK):
            # 美股分钟线暂用日 K 降级
            need = max(30, min(int(count or 60), 120))
            daily = self.get_klines(symbol, days=need)
            return daily[-need:] if daily else []
        cache_key = f"{self.market.value}:{symbol}:{iv}:{count}"
        now = time.time()
        cached = _KLINE_CACHE.get(cache_key)
        need = max(10, int(count or 240))
        if cached and (now - cached[0]) < _KLINE_TTL_TRADING_S and cached[1] >= need:
            bars = cached[2]
            return bars[-need:] if len(bars) > need else bars
        with _get_fetch_lock(cache_key):
            cached = _KLINE_CACHE.get(cache_key)
            if cached and (now - cached[0]) < _KLINE_TTL_TRADING_S and cached[1] >= need:
                bars = cached[2]
                return bars[-need:] if len(bars) > need else bars
            bars = _fetch_tencent_klines_interval(symbol, self.market, iv, need)
            if len(bars) < need:
                em = _fetch_eastmoney_intraday_klines(
                    symbol, self.market, iv, need
                )
                if len(em) > len(bars):
                    bars = em
            if len(bars) < need and self.market == MarketCode.CN:
                bs_bars = _fetch_baostock_intraday_klines(symbol, iv, need)
                if len(bs_bars) > len(bars):
                    bars = bs_bars
            if bars:
                _KLINE_CACHE[cache_key] = (time.time(), len(bars), list(bars))
            return bars[-need:] if len(bars) > need else bars

    def get_kline_summary(self, symbol: str) -> dict:
        """获取 K 线摘要（用于 prompt 和前端展示）"""
        klines = _filter_valid_klines(self.get_klines(symbol, days=120))
        if not klines:
            return {"error": "无K线数据"}
        indicators = self.get_technical_indicators(klines=klines)

        # 最近5日表现
        recent_5 = klines[-5:] if len(klines) >= 5 else klines
        up_days = sum(
            1
            for i, k in enumerate(recent_5)
            if i > 0 and k.close > recent_5[i - 1].close
        )

        # 趋势判断
        trend = "数据不足"
        if indicators.ma5 and indicators.ma10 and indicators.ma20:
            if indicators.ma5 > indicators.ma10 > indicators.ma20:
                trend = "多头排列"
            elif indicators.ma5 < indicators.ma10 < indicators.ma20:
                trend = "空头排列"
            else:
                trend = "均线交织"

        # MACD 状态（更详细）
        macd_status = "无数据"
        if indicators.macd_cross:
            days_str = (
                f"({indicators.macd_cross_days}日)"
                if indicators.macd_cross_days
                else ""
            )
            macd_status = f"{indicators.macd_cross}{days_str}"

        # RSI 状态
        rsi_status = None
        if indicators.rsi6 is not None:
            if indicators.rsi6 > 80:
                rsi_status = "超买"
            elif indicators.rsi6 > 70:
                rsi_status = "偏强"
            elif indicators.rsi6 < 20:
                rsi_status = "超卖"
            elif indicators.rsi6 < 30:
                rsi_status = "偏弱"
            else:
                rsi_status = "中性"

        # KDJ 状态
        kdj_status = None
        if indicators.kdj_k is not None and indicators.kdj_d is not None:
            if indicators.kdj_j is not None and indicators.kdj_j > 100:
                kdj_status = f"{indicators.kdj_cross}/超买"
            elif indicators.kdj_j is not None and indicators.kdj_j < 0:
                kdj_status = f"{indicators.kdj_cross}/超卖"
            else:
                kdj_status = indicators.kdj_cross

        # 布林带状态
        boll_status = None
        last_close = _finite_float(klines[-1].close) if klines else None
        if last_close and indicators.boll_upper and indicators.boll_lower:
            if last_close > indicators.boll_upper:
                boll_status = "突破上轨"
            elif last_close < indicators.boll_lower:
                boll_status = "跌破下轨"
            elif indicators.boll_width:
                if indicators.boll_width < 5:
                    boll_status = "收口窄幅"
                elif indicators.boll_width > 15:
                    boll_status = "开口放大"
                else:
                    boll_status = "正常波动"

        last_date = klines[-1].date if klines else None
        now = datetime.now(timezone.utc).isoformat()

        summary = {
            # meta
            "timeframe": "1d",
            "computed_at": now,
            "asof": last_date,
            "params": {
                "ma": [5, 10, 20, 60],
                "macd": {"fast": 12, "slow": 26, "signal": 9},
                "rsi": {"periods": [6, 12, 24]},
                "kdj": {"n": 9, "m1": 3, "m2": 3},
                "boll": {"period": 20, "num_std": 2},
                "support_resistance": {"windows": [5, 20, 60]},
            },
            "last_close": last_close,
            "recent_5_up": up_days,
            "trend": trend,
            # MACD
            "macd_status": macd_status,
            "macd_cross": indicators.macd_cross,
            "macd_cross_days": indicators.macd_cross_days,
            "macd_hist": indicators.macd_hist,
            # RSI
            "rsi6": indicators.rsi6,
            "rsi_status": rsi_status,
            # KDJ
            "kdj_k": indicators.kdj_k,
            "kdj_d": indicators.kdj_d,
            "kdj_j": indicators.kdj_j,
            "kdj_status": kdj_status,
            # 布林带
            "boll_upper": indicators.boll_upper,
            "boll_mid": indicators.boll_mid,
            "boll_lower": indicators.boll_lower,
            "boll_width": indicators.boll_width,
            "boll_status": boll_status,
            # 量能
            "volume_ratio": indicators.volume_ratio,
            "volume_trend": indicators.volume_trend,
            # 均线
            "ma5": indicators.ma5,
            "ma10": indicators.ma10,
            "ma20": indicators.ma20,
            "ma60": indicators.ma60,
            # 涨跌幅
            "change_5d": indicators.change_5d,
            "change_20d": indicators.change_20d,
            # 振幅
            "amplitude": indicators.amplitude,
            "amplitude_avg5": indicators.amplitude_avg5,
            # 波动率(ATR)
            "atr": indicators.atr,
            "atr_pct": indicators.atr_pct,
            # 多级支撑压力
            "support_s": indicators.support_s,
            "support_m": indicators.support_m,
            "support_l": indicators.support_l,
            "resistance_s": indicators.resistance_s,
            "resistance_m": indicators.resistance_m,
            "resistance_l": indicators.resistance_l,
            # 兼容旧字段
            "support": indicators.support,
            "resistance": indicators.resistance,
            # K线形态
            "kline_pattern": indicators.kline_pattern,
        }
        return _sanitize_json_floats(summary)

    def get_intraday_trends(self, symbol: str) -> IntradayTrendsResult:
        """获取当日分时曲线(逐分钟),交易时段短 TTL 缓存。"""
        sym = (symbol or "").strip()
        cache_key = f"trends:{self.market.value}:{sym}"
        ttl = _intraday_trends_cache_ttl(self.market)
        now = time.time()
        cached = _INTRADAY_TRENDS_CACHE.get(cache_key)
        if cached and (now - cached[0]) < ttl:
            return cached[1]

        with _get_fetch_lock(cache_key):
            cached = _INTRADAY_TRENDS_CACHE.get(cache_key)
            if cached and (time.time() - cached[0]) < ttl:
                return cached[1]

            result = _fetch_tencent_intraday_trends(sym, self.market)
            if result is None or len(result.points) < 2:
                em = _fetch_eastmoney_intraday_trends(sym, self.market)
                if em is not None and (
                    result is None or len(em.points) > len(result.points)
                ):
                    result = em
            if result is None:
                result = IntradayTrendsResult(
                    symbol=sym,
                    market=self.market.value,
                    trade_date=beijing_now().strftime("%Y-%m-%d"),
                    pre_close=None,
                    points=[],
                    updated_at=beijing_now().isoformat(timespec="seconds"),
                )
            _INTRADAY_TRENDS_CACHE[cache_key] = (time.time(), result)
            return result
