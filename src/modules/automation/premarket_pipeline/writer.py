"""盘前决策流水线 阶段5:落库推送(WriteSet 两段式:先组装,后单事务执行)。

写入物(全部候选 → suggestions;仅核验通过 → signals + alerts):
1. 先删当日本 agent 产物(stock_suggestions by agent_name+当日 /
   strategy_signal_runs by strategy_code+当日 / price_alert_rules by 名称前缀)
   —— 保证同日重跑幂等;
2. 再全部写入:建议(直写,绕开建议池去重窗口以保证重跑可覆盖)、
   StrategySignalRun 直写(upsert 模式仿 tradingagents.maybe_emit,
   source_candidate_id=0 哨兵)、提醒规则(create_alert_rule 三条/候选,
   失败捕获进数据声明不阻断);
3. save_agent_prediction_outcome horizons 1/3/5/10 与 save_analysis 独立落库。
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time as dtime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from src.modules.automation import premarket_config as pcfg
from src.modules.automation.premarket_pipeline.types import (
    AlertPlan,
    MacroCards,
    PickCandidate,
    SectorForecast,
    WriteSet,
)
from src.platform.persistence.json_safe import to_jsonable

logger = logging.getLogger(__name__)


def alert_date_prefix(snapshot_date: str) -> str:
    return pcfg.ALERT_NAME_PREFIX.format(date=snapshot_date)


def _alert_expire_at(snapshot_date: str) -> datetime:
    day = date.fromisoformat(snapshot_date)
    hh, mm = pcfg.ALERT_EXPIRE_TIME.split(":")
    return datetime.combine(day, dtime(int(hh), int(mm)))


def build_write_set(
    *,
    snapshot_date: str,
    cards: MacroCards,
    forecast: SectorForecast,
    candidates: list[PickCandidate],
    report_md: str,
    notify_content: str,
    declarations: list[dict],
    stage_errors: list[dict] | None = None,
) -> WriteSet:
    """组装段:全部写入物先落成 WriteSet,便于审查/测试(不碰 DB)。"""
    ws = WriteSet(snapshot_date=snapshot_date)
    ws.report_md = report_md
    ws.notify_content = notify_content
    ws.declarations = list(declarations or [])
    prefix = alert_date_prefix(snapshot_date)
    from src.platform.scheduling.timezone import utc_now

    # 与 suggestion_pool 存储口径一致:aware UTC(suggestion_pool.save_suggestion 同式)
    expires = utc_now() + timedelta(hours=pcfg.SUGGESTION_EXPIRES_HOURS)

    group_id = f"{pcfg.AGENT_NAME}-{snapshot_date}"
    for c in candidates:
        if c.veto_reason or c.filters_dropped:
            continue  # 一票否决/硬过滤淘汰的候选不产生任何写入物
        verified = bool(c.verified)
        action = c.action if (c.v11_pass and verified) else "watch"
        if c.v11_pass and verified:
            action_label = c.action_label
        elif c.v11_pass:
            action_label = "观望(核验未过)"
        else:
            action_label = c.action_label
        reason_parts = [
            f"板块:{c.board_name}",
            f"三高:{(c.triple_high or {}).get('composite')}",
            f"政策关联:{c.policy_score}",
            f"估值:{(c.valuation or {}).get('source')}",
        ]
        ws.suggestions.append(
            {
                "stock_symbol": c.symbol,
                "stock_market": "CN",
                "stock_name": c.stock_name,
                "action": action,
                "action_label": action_label,
                "signal": f"入场[{c.entry_low},{c.entry_high}] 止损{c.stop_loss} 止盈{c.target_price}",
                "reason": ";".join(str(p) for p in reason_parts)[:900],
                "agent_name": pcfg.AGENT_NAME,
                "agent_label": pcfg.SUGGESTION_AGENT_LABEL,
                "expires_at": expires,
                "meta": {
                    "snapshot_date": snapshot_date,
                    "verified": verified,
                    "grade": c.grade,
                    "rr": c.rr,
                    "leader_score": c.leader_score,
                    "budget_exhausted": c.budget_exhausted,
                },
            }
        )
        if not (c.v11_pass and verified):
            continue  # 仅核验通过的候选写信号行/提醒
        evidence = [
            f"板块逻辑:{c.board_name} {c.board_code}",
            "三卡溯源:" + ";".join(
                str(card.get("title") or "") for card in (cards.cards or [])
            ),
            f"板块预测:{c.board_name} {(forecast.items[0].stage if forecast.items else '')}",
        ]
        ws.signals.append(
            {
                "snapshot_date": snapshot_date,
                "strategy_code": pcfg.STRATEGY_CODE,
                "stock_symbol": c.symbol,
                "stock_market": "CN",
                "stock_name": c.stock_name,
                "action": action,
                "action_label": action_label,
                "signal": f"入场[{c.entry_low},{c.entry_high}] 止损{c.stop_loss} 止盈{c.target_price}",
                "reason": ";".join(str(p) for p in reason_parts)[:900],
                "score": round(c.leader_score * 100, 2),
                "rank_score": round(c.leader_score * 100, 2),
                "confidence": round((c.confidence or 5.0) / 10.0, 3),
                "entry_low": c.entry_low,
                "entry_high": c.entry_high,
                "stop_loss": c.stop_loss,
                "target_price": c.target_price,
                "evidence": evidence,
                "payload": {
                    "verification": c.verification,
                    "triple_high": c.triple_high,
                    "policy_score": c.policy_score,
                    "valuation": c.valuation,
                    "final_review": c.final_review,
                    "llm_ok": c.llm_ok,
                    "missing_dims": c.missing_dims,
                    "stage_errors": stage_errors or [],
                },
            }
        )
        if c.entry_high is not None:
            ws.alert_plans.append(
                AlertPlan(
                    stock_symbol=c.symbol,
                    stock_name=c.stock_name,
                    rules=[
                        {
                            "name": f"{prefix} {c.symbol} 触及买区",
                            "kind": "entry",
                            "op": "<=",
                            "value": c.entry_high,
                        },
                        {
                            "name": f"{prefix} {c.symbol} 止损",
                            "kind": "stop",
                            "op": "<=",
                            "value": c.stop_loss,
                        },
                        {
                            "name": f"{prefix} {c.symbol} 止盈",
                            "kind": "target",
                            "op": ">=",
                            "value": c.target_price,
                        },
                    ],
                )
            )
        for horizon in pcfg.PREDICTION_HORIZONS:
            ws.prediction_outcomes.append(
                {
                    "stock_symbol": c.symbol,
                    "stock_market": "CN",
                    "prediction_date": snapshot_date,
                    "horizon_days": horizon,
                    "prediction_group_id": group_id,
                    "action": action,
                    "action_label": action_label,
                    "confidence": round((c.confidence or 5.0) / 10.0, 3),
                    "trigger_price": c.current_price,
                }
            )
    ws.title = f"【{pcfg.SUGGESTION_AGENT_LABEL}】{snapshot_date}"
    return ws


def execute_write_set(db: Session, ws: WriteSet, *, auto_create_alerts: bool = True) -> dict:
    """执行段:先删当日产物再全部写入(同日重跑幂等),返回统计。

    事务边界:删除+建议+信号行在**同一事务**提交;提醒规则单独成段
    (create_alert_rule 内部自带 commit,失败回滚不能牵连主产物)。
    """
    from src.platform.persistence.models import (
        PriceAlertRule,
        StockSuggestion,
        StrategySignalRun,
    )

    # created_at 列存 UTC naive;suggestions 的"当日"按北京日历切,
    # 北京零点 = UTC 前一日 16:00(固定 +8,无夏令时)。
    day_start = datetime.fromisoformat(ws.snapshot_date + "T00:00:00") - timedelta(hours=8)
    deleted = {"suggestions": 0, "signals": 0, "alerts": 0}

    old_suggestions = (
        db.query(StockSuggestion)
        .filter(
            StockSuggestion.agent_name == pcfg.AGENT_NAME,
            StockSuggestion.created_at >= day_start,
        )
        .all()
    )
    deleted["suggestions"] = len(old_suggestions)
    for row in old_suggestions:
        db.delete(row)

    old_signals = (
        db.query(StrategySignalRun)
        .filter(
            StrategySignalRun.strategy_code == pcfg.STRATEGY_CODE,
            StrategySignalRun.snapshot_date == ws.snapshot_date,
        )
        .all()
    )
    deleted["signals"] = len(old_signals)
    for row in old_signals:
        db.delete(row)

    prefix = alert_date_prefix(ws.snapshot_date)
    if auto_create_alerts:
        old_alerts = (
            db.query(PriceAlertRule).filter(PriceAlertRule.name.like(f"{prefix}%")).all()
        )
        deleted["alerts"] = len(old_alerts)
        for row in old_alerts:
            db.delete(row)

    for sug in ws.suggestions:
        row_values = dict(sug)
        # meta 走 JSON 安全化;expires_at 需保持 datetime(与 suggestion_pool 存储口径一致)
        row_values["meta"] = to_jsonable(row_values.get("meta") or {})
        db.add(StockSuggestion(**row_values))
    for sig in ws.signals:
        _upsert_signal(db, sig)
    db.commit()

    created_alerts = 0
    if auto_create_alerts:
        created_alerts = _create_alert_rules(db, ws)

    stats = {
        "snapshot_date": ws.snapshot_date,
        "deleted": deleted,
        "suggestions": len(ws.suggestions),
        "signals": len(ws.signals),
        "alert_plans": len(ws.alert_plans),
        "alerts_created": created_alerts,
        "alert_errors": list(ws.alert_errors),
    }
    logger.info(
        "[盘前流水线][阶段5] 写入完成: %s", {k: v for k, v in stats.items() if k != "alert_errors"}
    )
    return stats


def _upsert_signal(db: Session, sig: dict[str, Any]) -> None:
    """StrategySignalRun 直写 upsert(仿 maybe_emit;哨兵 source_candidate_id=0)。"""
    from src.platform.persistence.models import StrategySignalRun

    existing = (
        db.query(StrategySignalRun)
        .filter(
            StrategySignalRun.snapshot_date == sig["snapshot_date"],
            StrategySignalRun.stock_symbol == sig["stock_symbol"],
            StrategySignalRun.stock_market == sig["stock_market"],
            StrategySignalRun.strategy_code == pcfg.STRATEGY_CODE,
            StrategySignalRun.source_candidate_id == pcfg.SENTINEL_CANDIDATE_ID,
        )
        .first()
    )
    if existing:
        for key, value in sig.items():
            setattr(existing, key, to_jsonable(value))
        existing.strategy_name = pcfg.SUGGESTION_AGENT_LABEL
        existing.strategy_version = "v1"
        existing.risk_level = "medium"
        existing.source_pool = "watchlist"
        existing.status = "active"
        existing.holding_days = 3
        existing.invalidation = "价格跌破止损位 / 核验项转 FAIL / 板块方向反转"
        existing.plan_quality = 75
        existing.source_agent = pcfg.AGENT_NAME
        existing.source_candidate_id = pcfg.SENTINEL_CANDIDATE_ID
        return
    row = StrategySignalRun(
        strategy_name=pcfg.SUGGESTION_AGENT_LABEL,
        strategy_version="v1",
        risk_level="medium",
        source_pool="watchlist",
        status="active",
        holding_days=3,
        invalidation="价格跌破止损位 / 核验项转 FAIL / 板块方向反转",
        plan_quality=75,
        source_agent=pcfg.AGENT_NAME,
        source_candidate_id=pcfg.SENTINEL_CANDIDATE_ID,
        trace_id="",
        is_holding_snapshot=False,
        **to_jsonable(sig),
    )
    db.add(row)


def _create_alert_rules(db: Session, ws: WriteSet) -> int:
    """每候选三条提醒(触及买区/止损/止盈);失败捕获进 alert_errors 不阻断。"""
    from src.modules.market.price_alert_service import create_alert_rule
    from src.platform.persistence.models import Stock

    created = 0
    expire_at = _alert_expire_at(ws.snapshot_date)
    for plan in ws.alert_plans:
        try:
            stock = (
                db.query(Stock)
                .filter(Stock.symbol == plan.stock_symbol, Stock.market == "CN")
                .first()
            )
            if stock is None:
                stock = Stock(symbol=plan.stock_symbol, name=plan.stock_name or plan.stock_symbol, market="CN")
                db.add(stock)
                db.flush()
            for rule in plan.rules:
                create_alert_rule(
                    db,
                    stock_id=stock.id,
                    name=rule["name"],
                    condition_group={"op": "and", "items": [{"type": "price", "op": rule["op"], "value": rule["value"]}]},
                    cooldown_minutes=pcfg.ALERT_COOLDOWN_MINUTES,
                    max_triggers_per_day=pcfg.ALERT_MAX_TRIGGERS_PER_DAY,
                    repeat_mode="repeat",
                    expire_at=expire_at,
                )
                created += 1
        except Exception as e:
            db.rollback()
            msg = f"{plan.stock_symbol} 提醒创建失败: {type(e).__name__}: {e}"
            ws.alert_errors.append(msg)
            logger.warning("[盘前流水线][阶段5] %s", msg)
    return created
