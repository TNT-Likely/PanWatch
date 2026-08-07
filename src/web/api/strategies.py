"""策略库 API: 列出/查看/应用借鉴 alphasift 的 YAML 策略到单只股票。

设计原则:
- 策略只用到可拿到的字段(实时或盘后)
- 单只股票评分(快速) + 全市场扫描(慢, 盘后)
- 字段缺失时显式标注, 不静默跳过
"""
import os
import logging
from pathlib import Path
from typing import Optional

import yaml
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

router = APIRouter()
logger = logging.getLogger(__name__)

# 策略 YAML 路径(借鉴 alphasift 的格式, 翻译为 PanWatch 可用字段子集)
STRATEGIES_FILE = Path(__file__).parent.parent.parent.parent / "strategies" / "panwatch_strategies.yaml"


def _load_strategies() -> dict:
    if not STRATEGIES_FILE.exists():
        raise HTTPException(503, f"策略文件不存在: {STRATEGIES_FILE}")
    try:
        data = yaml.safe_load(STRATEGIES_FILE.read_text(encoding="utf-8"))
        return data
    except Exception as e:
        raise HTTPException(500, f"策略文件解析失败: {e}")


@router.get("/list")
async def list_strategies():
    """列出所有可用策略。"""
    data = _load_strategies()
    completeness = data.get("data_completeness", {})

    items = []
    for key, cfg in data.items():
        if key == "data_completeness" or not isinstance(cfg, dict):
            continue
        strategy_data_status = completeness.get("strategy_data_status", {}).get(key, {})
        items.append({
            "id": key,
            "display_name": cfg.get("display_name", key),
            "description": cfg.get("description", ""),
            "category": cfg.get("category", "other"),
            "tags": cfg.get("tags", []),
            "ui_badge": cfg.get("ui_badge", ""),
            "source": cfg.get("source", ""),
            "filter": cfg.get("filter", {}),
            "eod_fields": list(_eod_fields(cfg)),  # 需要的盘后字段
            "data_window": strategy_data_status.get("available_in", "realtime"),
            "available_now": strategy_data_status.get("available_in", "realtime") == "realtime",
        })
    return {"items": items, "total": len(items)}


@router.get("/{strategy_id}")
async def get_strategy(strategy_id: str):
    """查看单个策略详情。"""
    data = _load_strategies()
    if strategy_id not in data:
        raise HTTPException(404, f"策略不存在: {strategy_id}")
    cfg = data[strategy_id]
    completeness = data.get("data_completeness", {}).get("strategy_data_status", {}).get(strategy_id, {})
    return {
        "id": strategy_id,
        "display_name": cfg.get("display_name", strategy_id),
        "description": cfg.get("description", ""),
        "category": cfg.get("category", "other"),
        "tags": cfg.get("tags", []),
        "filter": cfg.get("filter", {}),
        "ranking_factors": cfg.get("ranking_factors", {}),
        "eod_only_fields": list(_eod_fields(cfg)),
        "ui_badge": cfg.get("ui_badge", ""),
        "source": cfg.get("source", ""),
        "data_window": completeness.get("available_in", "realtime"),
    }


class ApplyRequest(BaseModel):
    strategy_id: str
    symbol: str
    market: str = "CN"


@router.post("/apply")
async def apply_strategy(req: ApplyRequest):
    """应用策略到单只股票: 硬过滤 + 因子打分。

    用现有 /api/quotes/{symbol} 拿实时字段。
    盘后字段(pe_ttm/pb_ratio/market_cap)如果有则用, 没有则跳过对应过滤项。
    """
    from src.core.marketdata_client import get_market_data
    from src.web.api.quotes import _parse_market

    data = _load_strategies()
    if req.strategy_id not in data:
        raise HTTPException(404, f"策略不存在: {req.strategy_id}")
    cfg = data[req.strategy_id]
    filter_cfg = cfg.get("filter", {})
    ranking = cfg.get("ranking_factors", {})

    market_code = _parse_market(req.market)

    # 拉取股票行情(用 quotes, 通用接口)
    try:
        quotes = get_market_data().quotes([req.symbol], market=req.market)
    except Exception as e:
        logger.warning(f"拉取行情失败 {req.symbol}: {e}")
        quotes = []

    q = None
    if quotes:
        # 归一化 Quote 对象 → dict
        first = quotes[0]
        if hasattr(first, "__dict__"):
            q = {k: v for k, v in vars(first).items() if not k.startswith("_")}
        elif isinstance(first, dict):
            q = first
        else:
            q = {"current_price": getattr(first, "current_price", None)}

    if not q:
        raise HTTPException(404, f"未找到行情: {req.symbol} ({req.market})")

    # 字段归一化
    def getf(key, default=None):
        if isinstance(q, dict):
            return q.get(key, default)
        return getattr(q, key, default)

    current_price = getf("current_price")
    change_pct = getf("change_pct")
    volume_ratio = getf("volume_ratio")
    turnover_rate = getf("turnover_rate")
    amount = getf("amount")  # 元
    open_p = getf("open")
    high = getf("high")
    low = getf("low")
    pe_ttm = getf("pe_ttm")
    pb_ratio = getf("pb_ratio")
    market_cap = getf("total_market_value")  # 亿(来自 eastmoney)

    # 硬过滤 + 标注缺失项
    passed = True
    failed_filters = []
    missing_fields = []

    def check(name, actual, op, threshold):
        nonlocal passed
        if actual is None:
            missing_fields.append(name)
            return
        ok = (
            (op == "min" and actual >= threshold)
            or (op == "max" and actual <= threshold)
        )
        if not ok:
            passed = False
            failed_filters.append({"field": name, "actual": actual, "required": op, "threshold": threshold})

    # 实时字段
    if "price_min" in filter_cfg and current_price is not None:
        check("current_price", current_price, "min", filter_cfg["price_min"])
    if "price_max" in filter_cfg and current_price is not None:
        check("current_price", current_price, "max", filter_cfg["price_max"])
    if "change_pct_min" in filter_cfg and change_pct is not None:
        check("change_pct", change_pct, "min", filter_cfg["change_pct_min"])
    if "change_pct_max" in filter_cfg and change_pct is not None:
        check("change_pct", change_pct, "max", filter_cfg["change_pct_max"])
    if "volume_ratio_min" in filter_cfg and volume_ratio is not None:
        check("volume_ratio", volume_ratio, "min", filter_cfg["volume_ratio_min"])
    if "volume_ratio_max" in filter_cfg and volume_ratio is not None:
        check("volume_ratio", volume_ratio, "max", filter_cfg["volume_ratio_max"])
    if "turnover_rate_min" in filter_cfg and turnover_rate is not None:
        check("turnover_rate", turnover_rate, "min", filter_cfg["turnover_rate_min"])
    if "turnover_rate_max" in filter_cfg and turnover_rate is not None:
        check("turnover_rate", turnover_rate, "max", filter_cfg["turnover_rate_max"])
    # 盘后字段(pe_ttm/pb_ratio/market_cap) — 既可能在 filter 里也可能在 cfg 顶层
    for prefix in ("pe_ttm", "pb", "market_cap"):
        for suffix in ("_min", "_max"):
            key = f"{prefix}{suffix}"
            threshold = filter_cfg.get(key)
            if threshold is None:
                threshold = cfg.get(key)  # 兜底从 cfg 顶层取(dual_low 写法)
            if threshold is None:
                continue
            actual_field = {"pe_ttm": "pe_ttm", "pb": "pb_ratio", "market_cap": "total_market_value"}[prefix]
            actual = getf(actual_field)
            if actual is None:
                missing_fields.append(actual_field)
            elif suffix == "_min" and actual < threshold:
                passed = False
                failed_filters.append({"field": actual_field, "actual": actual, "required": "min", "threshold": threshold})
            elif suffix == "_max" and actual > threshold:
                passed = False
                failed_filters.append({"field": actual_field, "actual": actual, "required": "max", "threshold": threshold})

    # 因子打分(简化版: 归一化到 0-100)
    score = 50.0
    score_breakdown = []
    if "low_pe" in ranking and pe_ttm is not None and pe_ttm > 0:
        # PE 越低分越高(PE=0 得 100, PE=30 得 0)
        s = max(0, min(100, 100 - pe_ttm * 3.3))
        score += (s - 50) * ranking["low_pe"]
        score_breakdown.append({"factor": "low_pe", "raw": pe_ttm, "score": round(s, 1), "weight": ranking["low_pe"]})
    if "low_pb" in ranking and pb_ratio is not None and pb_ratio > 0:
        s = max(0, min(100, 100 - pb_ratio * 25))
        score += (s - 50) * ranking["low_pb"]
        score_breakdown.append({"factor": "low_pb", "raw": pb_ratio, "score": round(s, 1), "weight": ranking["low_pb"]})
    if "volume_ratio" in ranking and volume_ratio is not None:
        # 量比 1.0 = 50分, 2.0+ = 100, 0.5 = 0
        s = max(0, min(100, volume_ratio * 50))
        score += (s - 50) * ranking["volume_ratio"]
        score_breakdown.append({"factor": "volume_ratio", "raw": volume_ratio, "score": round(s, 1), "weight": rounding_safe(ranking["volume_ratio"])})
    if "change_pct" in ranking and change_pct is not None:
        # 涨跌幅 -5~+5% 映射到 0~100
        s = max(0, min(100, 50 + change_pct * 10))
        score += (s - 50) * ranking["change_pct"]
        score_breakdown.append({"factor": "change_pct", "raw": change_pct, "score": round(s, 1), "weight": rounding_safe(ranking["change_pct"])})
    if "turnover_rate" in ranking and turnover_rate is not None:
        # 换手率 0~8% 映射到 0~100
        s = max(0, min(100, turnover_rate * 12.5))
        score += (s - 50) * ranking["turnover_rate"]
        score_breakdown.append({"factor": "turnover_rate", "raw": turnover_rate, "score": round(s, 1), "weight": rounding_safe(ranking["turnover_rate"])})
    if "stable_amount" in ranking and amount is not None and amount > 0:
        # 成交额 1亿=50, 5亿+=100, 0.1亿=0
        s = max(0, min(100, 25 + 15 * (amount ** 0.3)))
        score += (s - 50) * ranking["stable_amount"]
        score_breakdown.append({"factor": "stable_amount", "raw": amount, "score": round(s, 1), "weight": rounding_safe(ranking["stable_amount"])})
    if "stable" in ranking and turnover_rate is not None and volume_ratio is not None:
        # 稳定 = 换手率中等 + 量比稳定(1附近)
        stability = 100 - abs(turnover_rate - 2.0) * 20 - abs(volume_ratio - 1.0) * 15
        s = max(0, min(100, stability))
        score += (s - 50) * ranking["stable"]
        score_breakdown.append({"factor": "stable", "raw": f"turnover={turnover_rate}, vol_ratio={volume_ratio}", "score": round(s, 1), "weight": rounding_safe(ranking["stable"])})
    if "oversold" in ranking and change_pct is not None:
        # 跌越多(超卖)分越高, change_pct=-5 → 100, +5 → 0
        s = max(0, min(100, 50 - change_pct * 10))
        score += (s - 50) * ranking["oversold"]
        score_breakdown.append({"factor": "oversold", "raw": change_pct, "score": round(s, 1), "weight": rounding_safe(ranking["oversold"])})
    if "reversal" in ranking and change_pct is not None:
        # 反转信号: 跌得多 + 今日企稳
        s = 50 if change_pct >= 0 else max(0, 50 + change_pct * 10)
        score += (s - 50) * ranking["reversal"]
        score_breakdown.append({"factor": "reversal", "raw": change_pct, "score": round(s, 1), "weight": rounding_safe(ranking["reversal"])})

    score = max(0, min(100, round(score, 1)))

    return {
        "strategy_id": req.strategy_id,
        "symbol": req.symbol,
        "market": req.market,
        "passed": passed,
        "score": score,
        "score_breakdown": score_breakdown,
        "failed_filters": failed_filters,
        "missing_fields": missing_fields,
        "current_data": {
            "current_price": current_price,
            "change_pct": change_pct,
            "volume_ratio": volume_ratio,
            "turnover_rate": turnover_rate,
            "amount": amount,
            "pe_ttm": pe_ttm,
            "pb_ratio": pb_ratio,
            "market_cap": market_cap,
        },
    }


def _eod_fields(cfg: dict) -> set:
    """从 filter + 顶层 字段里识别哪些是盘后字段。"""
    eod_keys = {"pe_ttm_max", "pe_ttm_min", "pb_max", "pb_min", "market_cap_min", "market_cap_max"}
    f = cfg.get("filter", {})
    found = {k for k in f if k in eod_keys}
    # 顶层也可能有(dual_low 把 pe_ttm_max 放在 cfg 顶层)
    for k in cfg:
        if k in eod_keys:
            found.add(k)
    return found


def rounding_safe(v):
    if v is None:
        return 0
    try:
        return round(float(v), 2)
    except Exception:
        return 0