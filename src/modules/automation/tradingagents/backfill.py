"""把 analysis_history 里历史 TradingAgents 记录回填到 stock_suggestions。

背景:
Phase B 加 save_suggestion 之前的 TA 运行没写 stock_suggestions,所以「AI 建议」
panel 看不到旧 TA 决策。本模块在服务启动时自动跑一次,把最近 7 天的 TA 历史
回填到建议池(save_suggestion 内部有 dedupe,跑多次安全)。

幂等性:
- save_suggestion 用 (stock_symbol, agent_name, action, dedupe_window) 去重
- 同一条历史记录回填多次不会重复
"""

from __future__ import annotations

import logging
from datetime import date, timedelta

from src.web.database import SessionLocal
from src.web.models import AnalysisHistory, StockSuggestion

logger = logging.getLogger(__name__)


def backfill_tradingagents_suggestions(days: int = 7) -> dict:
    """把最近 N 天 analysis_history 里的 tradingagents 记录回填到 stock_suggestions。

    Returns:
        {"checked": int, "written": int, "skipped": int}
    """
    from src.core.suggestion_pool import save_suggestion

    cutoff_date = (date.today() - timedelta(days=days)).isoformat()

    db = SessionLocal()
    checked = written = skipped = 0
    try:
        records = (
            db.query(AnalysisHistory)
            .filter(
                AnalysisHistory.agent_name == "tradingagents",
                AnalysisHistory.analysis_date >= cutoff_date,
            )
            .all()
        )

        for r in records:
            checked += 1
            raw = r.raw_data or {}
            sug = raw.get("suggestion") or {}
            action = (sug.get("action") or "hold").lower()
            action_label = sug.get("action_label") or "持有"
            confidence = sug.get("confidence")

            # 检查 stock_suggestions 中是否已有(同股票 + 同 agent + 同 action + 近 24h)
            # 简化:直接尝试 save,save_suggestion 会判重
            existing = (
                db.query(StockSuggestion)
                .filter(
                    StockSuggestion.stock_symbol == r.stock_symbol,
                    StockSuggestion.agent_name == "tradingagents",
                    StockSuggestion.action == action,
                )
                .first()
            )
            if existing:
                skipped += 1
                continue

            confidence_text = (
                f" (置信度 {confidence:.1f}/10)"
                if isinstance(confidence, (int, float))
                else ""
            )

            # 推断 market(分析记录里没存,从 stock_symbol 简单推断)
            symbol = r.stock_symbol
            if symbol.isdigit() and len(symbol) == 6:
                market = "CN"
            elif symbol.isalpha():
                market = "US"
            elif symbol.isdigit() and len(symbol) == 5:
                market = "HK"
            else:
                market = "CN"

            # 从 AnalysisHistory record 拿股票名(如果存在)
            stock_name = ""
            try:
                from src.web.models import Stock
                stk = db.query(Stock).filter(Stock.symbol == symbol).first()
                if stk:
                    stock_name = stk.name or ""
                    market = stk.market or market
            except Exception:
                pass

            ok = save_suggestion(
                stock_symbol=symbol,
                stock_name=stock_name or symbol,
                stock_market=market,
                action=action,
                action_label=f"{action_label}{confidence_text}",
                agent_name="tradingagents",
                agent_label="TradingAgents 深度",
                signal=(sug.get("signal") or "")[:500],
                reason=(sug.get("reason") or "")[:1000],
                expires_hours=24,
                ai_response=(r.content or "")[:2000],
                meta={
                    "cost_usd": raw.get("cost_usd", 0),
                    "decision": raw.get("decision", "HOLD"),
                    "confidence": confidence,
                    "backfilled_at": str(date.today()),
                },
            )
            if ok:
                written += 1
            else:
                skipped += 1

        logger.info(
            f"[TA backfill] 检查 {checked} 条历史记录,写入 {written} 条建议,跳过 {skipped} 条"
        )
        return {"checked": checked, "written": written, "skipped": skipped}
    except Exception as e:
        logger.warning(f"[TA backfill] 失败,跳过: {e}")
        return {"checked": checked, "written": written, "skipped": skipped, "error": str(e)}
    finally:
        db.close()
