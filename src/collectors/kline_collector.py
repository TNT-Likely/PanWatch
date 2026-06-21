"""K线和技术指标采集器 - 基于腾讯 API（更稳定）"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx
import json
import random
import threading
import time

from src.collectors.market_http import fetch_source, source_suffix
from src.core.cn_symbol import get_cn_prefix, is_cn_sh
from src.models.market import MARKETS, MarketCode

logger = logging.getLogger(__name__)

# 腾讯日K线 API
TENCENT_KLINE_URL = "http://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
EASTMONEY_KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"


_STOOQ_CACHE: dict[str, tuple[float, list["KlineData"]]] = {}
_STOOQ_CACHE_TTL_SECONDS = 300
_EASTMONEY_CACHE: dict[str, tuple[float, int, list["KlineData"]]] = {}
_EASTMONEY_CACHE_TTL_SECONDS = 300


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
    headers = {"User-Agent": "PanWatch/1.0 (+https://github.com/)"}
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


def _fetch_eastmoney_klines(
    symbol: str, market: MarketCode, days: int, *, secid_override: str | None = None
) -> list[KlineData]:
    """Fetch daily kline from Eastmoney as CN/HK long-history fallback."""

    sym = (symbol or "").strip()
    if not sym:
        return []
    if market not in (MarketCode.CN, MarketCode.HK):
        return []

    need_days = max(1, int(days or 1))
    # secid 唯一标识标的(指数与个股前缀规则不同);以 secid 作缓存键,
    # 避免指数与同号个股(如 000001 既是平安银行又是上证指数)缓存串味。
    secid = secid_override or _eastmoney_secid(sym, market)
    cache_key = f"{market.value}:{secid}"
    now = time.time()
    cached = _EASTMONEY_CACHE.get(cache_key)
    if (
        cached
        and (now - cached[0]) < _EASTMONEY_CACHE_TTL_SECONDS
        and cached[1] >= need_days
    ):
        bars = cached[2]
        return bars[-need_days:] if len(bars) > need_days else bars

    params = {
        "secid": secid,
        "klt": "101",  # 1日K
        "fqt": "1",  # 前复权
        "lmt": str(min(max(need_days, 1200), 20000)),
        "end": "20500101",
        "fields1": "f1,f2,f3,f4,f5,f6",
        "fields2": "f51,f52,f53,f54,f55,f56",
        "ut": "fa5fd1943c7b386f172d6893dbfba10b",
    }
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Referer": "https://quote.eastmoney.com/",
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
        logger.warning(
            f"Eastmoney 获取 {symbol} K线失败: {last_err}{_source_suffix()}"
        )
        stale = _EASTMONEY_CACHE.get(cache_key)
        if stale:
            bars = stale[2]
            return bars[-need_days:] if len(bars) > need_days else bars
        return []

    _EASTMONEY_CACHE[cache_key] = (now, len(best), best)
    return best[-need_days:] if len(best) > need_days else best


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
_EASTMONEY_MIN_INTERVAL_S = 0.2
_EASTMONEY_THROTTLE_LOCK = threading.Lock()
_eastmoney_last_call = [0.0]


def _throttle_eastmoney() -> None:
    """进程级限速:东方财富兜底请求间隔 ≥ _EASTMONEY_MIN_INTERVAL_S,缓解批量突发被连接级拒绝。"""
    with _EASTMONEY_THROTTLE_LOCK:
        wait = _EASTMONEY_MIN_INTERVAL_S - (time.time() - _eastmoney_last_call[0])
        if wait > 0:
            time.sleep(wait)
        _eastmoney_last_call[0] = time.time()


def _fetch_tencent_klines(
    symbol: str, market: MarketCode, days: int
) -> list[KlineData]:
    """腾讯主路径取日K:进程级节流 + 空响应/异常退避重试(gtimg 批量突发常限流回空 body,重试可自愈)。"""
    tencent_sym = _tencent_symbol(symbol, market)
    params = {
        "param": f"{tencent_sym},day,,,{days},qfq",
        "_var": "kline_dayqfq",
    }
    klines: list[KlineData] = []
    last_err = None
    for attempt in range(3):
        _throttle_tencent()
        try:
            with httpx.Client(
                follow_redirects=True, timeout=10 + attempt * 4, trust_env=False
            ) as client:  # 行情直连,绕过 env 代理(生产代理会拦行情接口)
                resp = client.get(TENCENT_KLINE_URL, params=params)
                text = resp.text
            klines = _parse_tencent_kline_text(text, tencent_sym)
            if klines:
                break
            last_err = "空响应"  # gtimg 突发限流常回空 body,退避后重试
        except Exception as e:
            last_err = e
        if attempt < 2:
            time.sleep(0.4 * (attempt + 1) + random.uniform(0, 0.25))

    if not klines and last_err is not None:
        logger.warning(
            f"腾讯 K线获取失败(已重试)symbol={symbol}: {last_err}{_source_suffix()}"
        )
    return klines


def _parse_tencent_kline_text(text: str, tencent_sym: str) -> list[KlineData]:
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
            day_data = stock_data.get("day") or stock_data.get("qfqday") or []
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
            return hit

        # 2) 同标的并发合并:仅一个线程实际联网,其余等待后复用结果
        with _get_fetch_lock(cache_key):
            hit = self._cache_hit(cache_key, need)
            if hit is not None:
                return hit

            now = time.time()
            # 3) 负缓存:刚失败过的标的,冷却窗口内返回陈旧/空,不再联网
            if now < _FAIL_UNTIL.get(cache_key, 0.0):
                stale = _KLINE_CACHE.get(cache_key)
                bars = stale[2] if stale else []
                return bars[-need:] if len(bars) > need else bars

            klines = self._fetch_all_sources(symbol, days)
            if klines and len(klines) >= need:
                # 成功且条数足够:固化正缓存并清除冷却标记
                _KLINE_CACHE[cache_key] = (now, len(klines), list(klines))
                _FAIL_UNTIL.pop(cache_key, None)
            else:
                # 空 或 拿到部分但不足 need(常见:HK 腾讯不足 + eastmoney 补全失败,
                # 正缓存因 count<need 永不命中 → 每轮重打补全源刷屏)→ 固化冷却。
                # 部分结果仍缓存下来,冷却窗口内直接服务,避免反复联网。
                if klines:
                    _KLINE_CACHE[cache_key] = (now, len(klines), list(klines))
                _FAIL_UNTIL[cache_key] = now + _fail_cooldown(self.market)
            return klines[-need:] if len(klines) > need else klines

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
        """tencent → stooq(US) / eastmoney(CN/HK) 链路取数(不含缓存/合并逻辑)。"""
        klines = _fetch_tencent_klines(symbol, self.market, days)

        # Tencent 对部分美股返回的 day 数据异常偏少（仅 1-2 条），使用 Stooq 回退。
        if self.market == MarketCode.US and len(klines) < max(10, min(days, 30)):
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

    def get_kline_summary(self, symbol: str) -> dict:
        """获取 K 线摘要（用于 prompt 和前端展示）"""
        klines = self.get_klines(symbol, days=120)
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
        last_close = klines[-1].close if klines else None
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

        return {
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
