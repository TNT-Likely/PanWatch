"""K线和技术指标采集器 - 基于腾讯 API（更稳定）"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone

import httpx
import time

from src.core.cn_symbol import get_cn_prefix, is_cn_sh
from src.models.market import MarketCode

logger = logging.getLogger(__name__)

# 腾讯日K线 API
TENCENT_KLINE_URL = "http://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
EASTMONEY_KLINE_URL = "https://push2his.eastmoney.com/api/qt/stock/kline/get"


_STOOQ_CACHE: dict[str, tuple[float, list["KlineData"]]] = {}
_STOOQ_CACHE_TTL_SECONDS = 300
_EASTMONEY_CACHE: dict[str, tuple[float, int, list["KlineData"]]] = {}
_EASTMONEY_CACHE_TTL_SECONDS = 300


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
                follow_redirects=True, timeout=timeout, headers=headers
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


def _fetch_eastmoney_klines(
    symbol: str, market: MarketCode, days: int
) -> list[KlineData]:
    """Fetch daily kline from Eastmoney as CN/HK long-history fallback."""

    sym = (symbol or "").strip()
    if not sym:
        return []
    if market not in (MarketCode.CN, MarketCode.HK):
        return []

    need_days = max(1, int(days or 1))
    cache_key = f"{market.value}:{sym}"
    now = time.time()
    cached = _EASTMONEY_CACHE.get(cache_key)
    if (
        cached
        and (now - cached[0]) < _EASTMONEY_CACHE_TTL_SECONDS
        and cached[1] >= need_days
    ):
        bars = cached[2]
        return bars[-need_days:] if len(bars) > need_days else bars

    secid = _eastmoney_secid(sym, market)
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
        try:
            with httpx.Client(
                follow_redirects=True,
                timeout=12 + attempt * 6,
                headers=headers,
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
        logger.warning(f"Eastmoney 获取 {symbol} K线失败: {last_err}")
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


class KlineCollector:
    """K线数据采集器（腾讯 API）"""

    def __init__(self, market: MarketCode):
        self.market = market

    def get_klines(self, symbol: str, days: int = 60) -> list[KlineData]:
        """获取日K线数据"""
        tencent_sym = _tencent_symbol(symbol, self.market)

        params = {
            "param": f"{tencent_sym},day,,,{days},qfq",
            "_var": "kline_dayqfq",
        }

        try:
            with httpx.Client(follow_redirects=True, timeout=10) as client:
                resp = client.get(TENCENT_KLINE_URL, params=params)
                text = resp.text

            # 解析 JS 变量格式: kline_dayqfq={...}
            if "=" not in text:
                logger.warning(f"获取 {symbol} K线数据失败: 格式错误")
                return []

            json_str = text.split("=", 1)[1].strip()
            if json_str.endswith(";"):
                json_str = json_str[:-1]

            import json

            data = json.loads(json_str)

            # 解析数据 - 兼容多种 API 格式
            raw_data = data.get("data", {})
            day_data = []

            if isinstance(raw_data, dict):
                # 旧格式: data.{symbol}.day 或 data.{symbol}.qfqday
                stock_data = raw_data.get(tencent_sym, {})
                if isinstance(stock_data, dict):
                    day_data = stock_data.get("day") or stock_data.get("qfqday") or []
            elif isinstance(raw_data, list):
                # 新格式: data 直接是 K 线数组
                day_data = raw_data

            if not day_data:
                logger.warning(
                    f"K线数据为空 - symbol: {symbol}, code: {data.get('code')}, msg: {data.get('msg')}, data长度: {len(raw_data) if isinstance(raw_data, list) else 'N/A'}"
                )

            klines = []
            for item in day_data:
                if len(item) >= 5:
                    klines.append(
                        KlineData(
                            date=item[0],
                            open=float(item[1]),
                            close=float(item[2]),
                            high=float(item[3]),
                            low=float(item[4]),
                            volume=float(item[5]) if len(item) > 5 else 0,
                        )
                    )

            # Tencent 对部分美股返回的 day 数据异常偏少（仅 1-2 条），此时使用 Stooq 回退。
            if self.market == MarketCode.US and len(klines) < max(10, min(days, 30)):
                fallback = _fetch_stooq_us_klines(symbol)
                if fallback:
                    # Stooq 返回全量历史，这里取最后 days 条
                    return fallback[-days:]

            # CN/HK: Tencent 在高 days 时可能只返回近几年，尝试 Eastmoney 补全更长历史
            if self.market in (MarketCode.CN, MarketCode.HK):
                need_em = days >= 500 or len(klines) < max(120, int(days * 0.6))
                if need_em:
                    # 额外放大窗口，提升拿到更长历史的概率
                    em_target_days = min(max(days, 3000), 20000)
                    em = _fetch_eastmoney_klines(symbol, self.market, em_target_days)
                    if len(em) > len(klines):
                        return em[-days:] if len(em) > days else em

            return klines

        except Exception as e:
            logger.error(f"获取 {symbol} K线数据失败: {e}")
            # 美股回退到 Stooq
            if self.market == MarketCode.US:
                fb = _fetch_stooq_us_klines(symbol)
                return fb[-days:] if fb else []
            # CN/HK 回退到 Eastmoney
            if self.market in (MarketCode.CN, MarketCode.HK):
                fb = _fetch_eastmoney_klines(symbol, self.market, min(max(days, 3000), 20000))
                return fb[-days:] if fb else []
            return []

    def get_technical_indicators(self, symbol: str) -> TechnicalIndicators:
        """计算技术指标"""
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
        klines = self.get_klines(symbol, days=30)
        indicators = self.get_technical_indicators(symbol)

        if not klines:
            return {"error": "无K线数据"}

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
