"""TradingAgents 历史决策 vs 实际涨跌对比。

用法:
    build_history_comparison("601238", "CN", days=90)

返回:每条历史决策 + 1d/5d/20d 后实际涨跌 + 命中标记,以及总体命中率。

"对" 的定义:
- buy:之后涨 → hit=True
- sell:之后跌 → hit=True
- hold:涨跌幅 |x| < 2% → hit=True(横盘判定)

命中率用 20 日窗口为主(决策视野通常 1-2 周)。
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from src.collectors.kline_collector import KlineCollector
from src.models.market import MarketCode
from src.web.database import SessionLocal
from src.web.models import AnalysisHistory

logger = logging.getLogger(__name__)


def _resolve_market(market: str) -> MarketCode:
    code = (market or "CN").strip().upper()
    if code == "US":
        return MarketCode.US
    if code == "HK":
        return MarketCode.HK
    return MarketCode.CN


def _classify_hit(action: str, ret_pct: float | None) -> bool | None:
    """根据 action 和后续收益率判断决策是否"命中"。"""
    if ret_pct is None:
        return None
    if action == "buy":
        return ret_pct > 0
    if action == "sell":
        return ret_pct < 0
    if action == "hold":
        return abs(ret_pct) < 2.0
    return None


def _find_close_on_or_after(klines_by_date: dict[str, float], target: str) -> tuple[str, float] | None:
    """从 target 日期起向后找最近一个交易日的收盘价。最多回查 7 天(节假日)。"""
    base = date.fromisoformat(target)
    for offset in range(8):
        d = (base + timedelta(days=offset)).isoformat()
        if d in klines_by_date:
            return d, klines_by_date[d]
    return None


def _find_close_after_n_trading_days(
    sorted_dates: list[str],
    base_date: str,
    n: int,
    klines_by_date: dict[str, float],
) -> float | None:
    """从 base_date 之后 N 个交易日的收盘价。base_date 必须已是交易日。"""
    try:
        idx = sorted_dates.index(base_date)
    except ValueError:
        return None
    target_idx = idx + n
    if target_idx >= len(sorted_dates):
        return None
    return klines_by_date[sorted_dates[target_idx]]


def build_history_comparison(
    stock_symbol: str,
    market: str = "CN",
    days: int = 90,
) -> dict:
    """构建某只股票 TradingAgents 历史决策对比数据。

    Args:
        stock_symbol: 股票代码
        market: CN / US / HK
        days: 回溯多少天的 TA 历史

    Returns:
        {
            "items": [...],     # 按 analysis_date 倒序
            "stats": {...},     # 命中率 + 平均收益
        }
    """
    symbol = (stock_symbol or "").strip()
    if not symbol:
        return {"items": [], "stats": _empty_stats()}

    cutoff_date = (date.today() - timedelta(days=days)).isoformat()

    db = SessionLocal()
    try:
        records = (
            db.query(AnalysisHistory)
            .filter(
                AnalysisHistory.agent_name == "tradingagents",
                AnalysisHistory.stock_symbol == symbol,
                AnalysisHistory.analysis_date >= cutoff_date,
            )
            .order_by(AnalysisHistory.analysis_date.desc())
            .all()
        )
    except Exception as e:
        logger.warning(f"[TA history] 查询失败: {e}")
        db.close()
        return {"items": [], "stats": _empty_stats()}
    finally:
        db.close()

    if not records:
        return {"items": [], "stats": _empty_stats()}

    # 拉历史 K线(回溯天数 + 30 天缓冲让最早的决策也能算 20 日收益)
    try:
        collector = KlineCollector(_resolve_market(market))
        klines = collector.get_klines(symbol, days=days + 40)
    except Exception as e:
        logger.warning(f"[TA history] 拉 K线失败: {e}")
        klines = []

    klines_by_date = {k.date: k.close for k in klines}
    sorted_dates = sorted(klines_by_date.keys())

    items: list[dict] = []
    for r in records:
        raw = r.raw_data or {}
        sug = raw.get("suggestion") or {}
        action = (sug.get("action") or "hold").lower()
        confidence = sug.get("confidence")
        cost_usd = raw.get("cost_usd")
        # 分析价优先用落库时存的"分析时实时价"(立即显示),K线 close 作 fallback
        stored_price = raw.get("price_at_analysis")
        stored_price = round(float(stored_price), 2) if isinstance(stored_price, (int, float)) else None

        base = _find_close_on_or_after(klines_by_date, r.analysis_date)
        if base is None:
            items.append({
                "trace_id": "",
                "analysis_date": r.analysis_date,
                "action": action,
                "action_label": sug.get("action_label") or _action_to_label(action),
                "confidence": confidence,
                "cost_usd": cost_usd,
                "price_at_analysis": stored_price,
                "return_1d_pct": None,
                "return_5d_pct": None,
                "return_20d_pct": None,
                "hit_20d": None,
            })
            continue

        base_date, base_close = base
        ret = {}
        for n_days in (1, 5, 20):
            close_n = _find_close_after_n_trading_days(sorted_dates, base_date, n_days, klines_by_date)
            ret[n_days] = (
                round((close_n - base_close) / base_close * 100, 2) if close_n is not None else None
            )

        items.append({
            "trace_id": "",
            "analysis_date": r.analysis_date,
            "action": action,
            "action_label": sug.get("action_label") or _action_to_label(action),
            "confidence": confidence,
            "cost_usd": cost_usd,
            "price_at_analysis": stored_price if stored_price is not None else round(base_close, 2),
            "return_1d_pct": ret[1],
            "return_5d_pct": ret[5],
            "return_20d_pct": ret[20],
            "hit_20d": _classify_hit(action, ret[20]),
        })

    return {"items": items, "stats": _compute_stats(items)}


def _action_to_label(action: str) -> str:
    return {"buy": "买入", "sell": "卖出", "hold": "持有"}.get(action, action)


def _empty_stats() -> dict:
    return {
        "total": 0,
        "buy_count": 0,
        "sell_count": 0,
        "hold_count": 0,
        "buy_hit_rate": None,
        "sell_hit_rate": None,
        "hold_hit_rate": None,
        "overall_hit_rate": None,
        "avg_return_20d_pct": None,
    }


def _compute_stats(items: list[dict]) -> dict:
    """统计:仅基于已有 20 日收益的条目。"""
    scored = [x for x in items if x.get("return_20d_pct") is not None]
    if not scored:
        return {**_empty_stats(), "total": len(items)}

    def _rate(action: str) -> float | None:
        subset = [x for x in scored if x["action"] == action]
        if not subset:
            return None
        hits = sum(1 for x in subset if x.get("hit_20d"))
        return round(hits / len(subset), 3)

    overall_hits = sum(1 for x in scored if x.get("hit_20d"))
    avg_return = round(sum(x["return_20d_pct"] for x in scored) / len(scored), 2)

    return {
        "total": len(items),
        "buy_count": sum(1 for x in items if x["action"] == "buy"),
        "sell_count": sum(1 for x in items if x["action"] == "sell"),
        "hold_count": sum(1 for x in items if x["action"] == "hold"),
        "buy_hit_rate": _rate("buy"),
        "sell_hit_rate": _rate("sell"),
        "hold_hit_rate": _rate("hold"),
        "overall_hit_rate": round(overall_hits / len(scored), 3),
        "avg_return_20d_pct": avg_return,
    }
