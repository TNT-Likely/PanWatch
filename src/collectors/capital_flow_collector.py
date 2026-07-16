"""资金流向采集器 - 基于东方财富 API"""
import logging
import time
from dataclasses import dataclass

from src.collectors.market_http import TTLCache, market_get, source_suffix
from src.core.cn_symbol import is_cn_sh
from src.models.market import MarketCode

logger = logging.getLogger(__name__)

# 东方财富资金流向 API
EASTMONEY_FLOW_URL = "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"

# 资金流为日级数据、变动慢:中等 TTL 缓存 + 按 host 节流,避免直连调用每轮重复拉。
_FLOW_HOST = "push2his.eastmoney.com"
_FLOW_MIN_INTERVAL_S = 0.2
_FLOW_CACHE = TTLCache(default_ttl_sec=600.0)


@dataclass
class CapitalFlow:
    """资金流向数据"""
    symbol: str
    name: str

    # 今日资金流（单位：元）
    main_net_inflow: float      # 主力净流入
    main_net_inflow_pct: float  # 主力净流入占比
    super_net_inflow: float     # 超大单净流入
    big_net_inflow: float       # 大单净流入
    mid_net_inflow: float       # 中单净流入
    small_net_inflow: float     # 小单净流入

    # 5日资金流
    main_net_5d: float | None = None  # 5日主力净流入


def _get_eastmoney_secid(symbol: str, market: MarketCode) -> str:
    """转换为东方财富的 secid 格式"""
    if market == MarketCode.HK:
        return f"116.{symbol}"
    if market == MarketCode.US:
        return f"105.{symbol}"
    prefix = "1" if is_cn_sh(symbol) else "0"
    return f"{prefix}.{symbol}"

def _safe_float(value):
    """将字符串或数字安全转换为 float，无效值返回 0.0"""
    if value is None or value == '' or value == '-':
        return 0.0
    try:
        return float(value)
    except (ValueError, TypeError):
        return 0.0


def _use_marketdata_cf() -> bool:
    """读灰度开关(独立函数便于测试 monkeypatch)。"""
    from src.config import Settings
    return bool(Settings().use_marketdata)


def get_market_data():
    """惰性导入,避免模块加载时的循环依赖(便于测试 monkeypatch)。"""
    from src.core.marketdata_client import get_market_data as _g
    return _g()


class CapitalFlowCollector:
    """资金流向采集器"""

    def __init__(self, market: MarketCode):
        self.market = market

    def get_capital_flow(self, symbol: str) -> CapitalFlow | None:
        """获取单只股票的资金流向(直连 + 节流 + 退避重试 + TTL缓存)。"""
        cache_key = f"{self.market.value}:{symbol}"
        cached = _FLOW_CACHE.get(cache_key)
        if cached is not None:
            return cached

        if _use_marketdata_cf():
            md_cf = get_market_data().capital_flow(symbol, market=self.market.value)
            if md_cf is None:
                return None
            capital_flow = CapitalFlow(
                symbol=md_cf.symbol,
                name=md_cf.name,
                main_net_inflow=md_cf.main_net_inflow,
                main_net_inflow_pct=md_cf.main_net_inflow_pct,
                super_net_inflow=md_cf.super_net_inflow,
                big_net_inflow=md_cf.big_net_inflow,
                mid_net_inflow=md_cf.mid_net_inflow,
                small_net_inflow=md_cf.small_net_inflow,
                main_net_5d=md_cf.main_net_5d,
            )
            _FLOW_CACHE.set(cache_key, capital_flow)
            return capital_flow

        # ↓↓↓ flag 关:原逻辑一字不动 ↓↓↓
        secid = _get_eastmoney_secid(symbol, self.market)

        params = {
            "lmt": "0",
            "klt": "101",
            "secid": secid,
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
            EASTMONEY_FLOW_URL,
            host_key=_FLOW_HOST,
            params=params,
            headers=headers,
            min_interval_s=_FLOW_MIN_INTERVAL_S,
            timeout=8,
            retries=2,
            parse="json",
            symbol=symbol,
            log_label="资金流",
        )
        if not data:
            return None

        try:
            if data.get("data") is None:
                logger.warning(f"没有 data 数据 获取 {symbol} 资金流向失败: 无数据")
                return None

            d = data["data"]
            klines = d.get('klines')
            if not klines:
                logger.warning(f"没有 klines 数据 获取 {symbol} 资金流向失败: 无数据")
                return None

            # 1. 获取最新一条（最后一条）数据
            last_line = klines[-1]
            # 字段索引（从0开始）：
            # 0:日期, 1:主力净额, 2:小单净额, 3:中单净额, 4:大单净额, 5:超大单净额,
            # 6:主力占比, 7:小单占比, 8:中单占比, 9:大单占比, 10:超大单占比,
            # 11:收盘价, 12:涨跌幅, 13:成交量, 14:成交额
            parts = last_line.split(',')
            if len(parts) < 13:
                logger.warning(f"klines 字段不足，实际长度 {len(parts)} 获取 {symbol} 资金流向失败: 无数据")
                return None

            # 2. 计算5日主力净流入（最后5条的主力净额之和）
            # 注意：klines 顺序是从旧到新，最后5条即 klines[-5:]
            last_five = klines[-5:] if len(klines) >= 5 else klines  # 不足5条则用全部
            main_net_5d = 0.0
            for line in last_five:
                line_parts = line.split(',')
                if len(line_parts) >= 2:
                    main_net_5d += _safe_float(line_parts[1])

            capital_flow = CapitalFlow(
                symbol=str(d["code"]),
                name=str(d["name"]),
                main_net_inflow=_safe_float(parts[1]),  # 主力净流入
                main_net_inflow_pct=_safe_float(parts[6]),  # 主力净流入占比
                super_net_inflow=_safe_float(parts[5]),  # 超大单净流入
                big_net_inflow=_safe_float(parts[4]),  # 大单净流入
                mid_net_inflow=_safe_float(parts[3]),  # 中单净流入
                small_net_inflow=_safe_float(parts[2]),  # 小单净流入
                main_net_5d=main_net_5d,  # 5日主力净流入
            )

            # print(f"代码: {capital_flow.symbol}")
            # print(f"名称: {capital_flow.name}")
            # print(f"主力净流入: {capital_flow.main_net_inflow:,.2f} 元")
            # print(f"主力净流入占比: {capital_flow.main_net_inflow_pct}%")
            # print(f"超大单净流入: {capital_flow.super_net_inflow:,.2f} 元")
            # print(f"大单净流入: {capital_flow.big_net_inflow:,.2f} 元")
            # print(f"中单净流入: {capital_flow.mid_net_inflow:,.2f} 元")
            # print(f"小单净流入: {capital_flow.small_net_inflow:,.2f} 元")
            # print(f"5日主力净流入: {capital_flow.main_net_5d:,.2f} 元")

            _FLOW_CACHE.set(cache_key, capital_flow)
            return capital_flow

        except Exception as e:
            logger.error(f"解析 {symbol} 资金流向失败: {e}{source_suffix()}")
            return None

    def get_capital_flow_summary(self, symbol: str) -> dict:
        """获取资金流向摘要（用于 prompt）"""
        flow = self.get_capital_flow(symbol)

        if not flow:
            return {"error": "无资金流向数据"}

        # 判断资金状态
        if flow.main_net_inflow > 0:
            if flow.main_net_inflow_pct > 10:
                status = "主力大幅流入"
            elif flow.main_net_inflow_pct > 5:
                status = "主力明显流入"
            else:
                status = "主力小幅流入"
        elif flow.main_net_inflow < 0:
            if flow.main_net_inflow_pct < -10:
                status = "主力大幅流出"
            elif flow.main_net_inflow_pct < -5:
                status = "主力明显流出"
            else:
                status = "主力小幅流出"
        else:
            status = "主力资金平衡"

        # 5日趋势
        trend_5d = "无数据"
        if flow.main_net_5d is not None:
            if flow.main_net_5d > 0:
                trend_5d = f"5日净流入{flow.main_net_5d/1e8:.2f}亿"
            else:
                trend_5d = f"5日净流出{abs(flow.main_net_5d)/1e8:.2f}亿"

        return {
            "status": status,
            "main_net_inflow": flow.main_net_inflow,
            "main_net_inflow_pct": flow.main_net_inflow_pct,
            "super_net_inflow": flow.super_net_inflow,
            "big_net_inflow": flow.big_net_inflow,
            "mid_net_inflow": flow.mid_net_inflow,
            "small_net_inflow": flow.small_net_inflow,
            "trend_5d": trend_5d,
        }
