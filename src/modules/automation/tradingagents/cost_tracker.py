"""TradingAgents 月度成本预算与单次成本估算。

成本数据来自 AnalysisHistory.raw_data["cost_usd"],SQL 聚合本月成功记录。
AnalysisHistory 是 JSON 列,适合存复杂结构;AgentRun.result 只是截断文本。
"""

from __future__ import annotations

import logging
from datetime import date, datetime, timezone

from src.web.database import SessionLocal
from src.web.models import AnalysisHistory

logger = logging.getLogger(__name__)


def check_budget(monthly_budget_usd: float, agent_name: str = "tradingagents") -> dict:
    """统计本月已用美元 + 剩余,供触发前校验。

    Returns:
        {
            "used": float,           # 本月已用(美元)
            "remaining": float,      # 剩余(美元)
            "limit": float,          # 配置上限
            "exceeded": bool,        # 是否超限
            "runs_this_month": int,  # 本月运行次数
        }
    """
    now = datetime.now(timezone.utc)
    # AnalysisHistory.analysis_date 是 "YYYY-MM-DD" 字符串
    month_prefix = now.strftime("%Y-%m")

    db = SessionLocal()
    try:
        records = (
            db.query(AnalysisHistory)
            .filter(
                AnalysisHistory.agent_name == agent_name,
                AnalysisHistory.analysis_date.like(f"{month_prefix}-%"),
            )
            .all()
        )

        total = 0.0
        for r in records:
            cost = _extract_cost(r.raw_data)
            if cost:
                total += cost

        used = round(total, 4)
        remaining = max(0.0, float(monthly_budget_usd) - used)
        return {
            "used": used,
            "remaining": round(remaining, 4),
            "limit": float(monthly_budget_usd),
            "exceeded": used >= float(monthly_budget_usd),
            "runs_this_month": len(records),
        }
    except Exception as e:
        logger.warning(f"[TA成本] 预算查询失败,默认放行: {e}")
        return {
            "used": 0.0,
            "remaining": float(monthly_budget_usd),
            "limit": float(monthly_budget_usd),
            "exceeded": False,
            "runs_this_month": 0,
        }
    finally:
        db.close()


def _extract_cost(raw_data) -> float:
    """从 AnalysisHistory.raw_data 提取 cost_usd。"""
    if not isinstance(raw_data, dict):
        return 0.0
    cost = raw_data.get("cost_usd")
    if cost is None:
        return 0.0
    try:
        return float(cost)
    except (TypeError, ValueError):
        return 0.0


def estimate_cost(
    *,
    debate_rounds: int,
    selected_analysts: list[str],
    model: str = "deepseek-chat",
) -> dict:
    """单次分析的成本估算(粗略,实际可能 ±50%)。

    用于触发前给用户预估。公式假设:
    - 每分析师 ~5k input + 2k output token
    - 辩论每轮 ~12k input + 4k output token
    - 风控 + PM ~15k input + 3k output token
    - LangGraph 累积上下文实际比理论高 2-5 倍
    """
    n_analysts = len(selected_analysts or [])
    prompt_tokens = n_analysts * 5000 + max(1, debate_rounds) * 12000 + 15000
    completion_tokens = n_analysts * 2000 + max(1, debate_rounds) * 4000 + 3000

    # 单价表(美元/百万 token)
    PRICING = {
        "deepseek-chat": (0.14, 0.28),
        "deepseek-reasoner": (0.55, 2.19),
        "gpt-4o-mini": (0.15, 0.60),
        "gpt-4o": (2.50, 10.00),
        "claude-sonnet-4": (3.00, 15.00),
        "glm-4-flash": (0.05, 0.20),
    }
    input_rate, output_rate = PRICING.get(model.lower(), PRICING["deepseek-chat"])
    cost = (prompt_tokens / 1_000_000 * input_rate) + (
        completion_tokens / 1_000_000 * output_rate
    )

    return {
        "model": model,
        "prompt_tokens_est": prompt_tokens,
        "completion_tokens_est": completion_tokens,
        "cost_low_usd": round(cost * 2, 4),
        "cost_high_usd": round(cost * 5, 4),
    }


def get_today_cache_key(symbol: str, market: str, debate_rounds: int, model: str) -> str:
    """生成同标的同日的缓存键,用于跳过重复 LLM 调用。"""
    today = date.today().isoformat()
    return f"{market}:{symbol}:{today}:r{debate_rounds}:{model}"
