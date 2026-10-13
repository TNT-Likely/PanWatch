"""模拟盘跟单通知：建仓/平仓实时推送、盘前计划、日终摘要。"""

from __future__ import annotations

import logging
from typing import Any

from src.platform.notifications.notifier import NotifierManager
from src.platform.scheduling import trading_calendar as calendar
from src.platform.persistence.database import SessionLocal
from src.platform.persistence.models import (
    AppSettings,
    NotifyChannel,
    PaperTradingAccount,
    PaperTradingPosition,
    PaperTradingTrade,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 配置读取
# ---------------------------------------------------------------------------

_CONFIG_KEYS = {
    "pt_notify_enabled": "false",
    "pt_notify_channel_ids": "",
    "pt_notify_realtime": "true",
    "pt_notify_premarket": "true",
    "pt_notify_summary": "true",
}


def _load_config() -> dict[str, str]:
    """从 AppSettings 表读取 pt_notify_* 配置。"""
    db = SessionLocal()
    try:
        rows = (
            db.query(AppSettings)
            .filter(AppSettings.key.in_(_CONFIG_KEYS.keys()))
            .all()
        )
        cfg = dict(_CONFIG_KEYS)  # defaults
        for r in rows:
            cfg[r.key] = r.value or _CONFIG_KEYS.get(r.key, "")
        return cfg
    finally:
        db.close()


def _is_enabled() -> bool:
    cfg = _load_config()
    return cfg.get("pt_notify_enabled", "").lower() == "true"


def _is_mode_enabled(mode_key: str) -> bool:
    cfg = _load_config()
    if cfg.get("pt_notify_enabled", "").lower() != "true":
        return False
    return cfg.get(mode_key, "").lower() == "true"


# ---------------------------------------------------------------------------
# 渠道构建
# ---------------------------------------------------------------------------

def _build_notifier() -> NotifierManager | None:
    """根据配置构建 NotifierManager，无可用渠道时返回 None。"""
    cfg = _load_config()
    if cfg.get("pt_notify_enabled", "").lower() != "true":
        return None

    db = SessionLocal()
    try:
        channel_ids_str = cfg.get("pt_notify_channel_ids", "").strip()
        if channel_ids_str:
            ids = [int(x.strip()) for x in channel_ids_str.split(",") if x.strip().isdigit()]
            channels = (
                db.query(NotifyChannel)
                .filter(NotifyChannel.id.in_(ids), NotifyChannel.enabled.is_(True))
                .all()
            )
        else:
            # 未指定渠道时使用默认渠道
            channels = (
                db.query(NotifyChannel)
                .filter(NotifyChannel.enabled.is_(True), NotifyChannel.is_default.is_(True))
                .all()
            )

        if not channels:
            logger.debug("[模拟盘通知] 无可用通知渠道")
            return None

        mgr = NotifierManager()
        for ch in channels:
            mgr.add_channel(ch.type, ch.config or {})
        return mgr
    finally:
        db.close()


# ---------------------------------------------------------------------------
# 消息格式化
# ---------------------------------------------------------------------------

EXIT_REASON_LABELS = {
    "stop_loss": "止损",
    "target_price": "止盈",
    "signal_reversal": "信号反转",
    "manual": "手动平仓",
}
EXIT_REASON_LABELS_EN = {
    "stop_loss": "Stop loss",
    "target_price": "Take profit",
    "signal_reversal": "Signal reversal",
    "manual": "Manual close",
}

STRATEGY_NAME_MAP = {
    "qlib": "Qlib 冻结模型组合",
    "trend_follow": "趋势延续",
    "macd_golden": "MACD金叉",
    "volume_breakout": "放量突破",
    "pullback": "回踩确认",
    "rebound": "超跌反弹",
    "watchlist_agent": "Agent建议",
    "market_scan": "市场扫描",
    "momentum": "动量策略",
}
STRATEGY_NAME_MAP_EN = {
    "qlib": "Qlib frozen model portfolio",
    "trend_follow": "Trend continuation",
    "macd_golden": "MACD golden cross",
    "volume_breakout": "Volume breakout",
    "pullback": "Pullback confirmation",
    "rebound": "Oversold rebound",
    "watchlist_agent": "Agent suggestion",
    "market_scan": "Market scan",
    "momentum": "Momentum",
}


def _report_is_english() -> bool:
    """Resolve the AI/report preference, including follow-interface mode."""
    db = SessionLocal()
    try:
        from src.platform.language import resolve_report_language

        return resolve_report_language(db) == "en-US"
    except Exception:
        return False
    finally:
        db.close()


def _strategy_label(code: str, english: bool = False) -> str:
    return (STRATEGY_NAME_MAP_EN if english else STRATEGY_NAME_MAP).get(code, code)


def _stock_display(symbol: str, market: str, name: str = "") -> str:
    """生成带链接的股票显示文本，点击代码跳转到行情页。"""
    from src.modules.administration.stock_link import stock_link_markdown
    label = f"{name} " if name else ""
    return f"{label}({stock_link_markdown(symbol, market)})"


def _format_entry_message(pos: dict, sig: dict | None, english: bool = False) -> tuple[str, str]:
    name = pos.get("stock_name") or pos["stock_symbol"]
    title = f"[Paper trading entry] {name}" if english else f"【模拟盘建仓】{name}"
    stock = _stock_display(pos["stock_symbol"], pos["stock_market"], name)
    price = pos["entry_price"]
    lines = [f"Stock: {stock}" if english else f"股票: {stock}",
             f"Entry: {price:.2f} | Quantity: {pos['quantity']}" if english else f"入场: {price:.2f} | 数量: {pos['quantity']}",
             f"Strategy: {_strategy_label(pos.get('strategy_code', ''), english)}" if english else f"策略: {_strategy_label(pos.get('strategy_code', ''), english)}"]
    if pos.get("stop_loss") and pos.get("target_price"):
        lines.append(f"{pos['stop_loss']:.2f} / {pos['target_price']:.2f}")
    research = pos.get("research") or {}
    if research:
        lines.append("Virtual next-open fill, not a broker execution" if english else "下一开盘虚拟成交，非券商实盘")
        lines.append(f"run={research['run_id']} · model={research['model_id']} · decision={research['decision_id']}")
    return title, "\n".join(lines)


def _format_exit_message(pos: dict, trade: dict, english: bool = False) -> tuple[str, str]:
    """格式化平仓通知，返回 (title, body)。pos/trade 为序列化后的 dict。"""
    name = pos.get("stock_name") or pos["stock_symbol"]
    pnl = trade["pnl"]
    pnl_sign = "+" if pnl >= 0 else ""
    title = f"[Paper trading close] {name} {pnl_sign}{pnl:.2f}" if english else f"【模拟盘平仓】{name} {pnl_sign}{pnl:.2f}"

    stock_info = _stock_display(pos["stock_symbol"], pos["stock_market"], name)
    reason_map = EXIT_REASON_LABELS_EN if english else EXIT_REASON_LABELS
    reason = reason_map.get(trade["exit_reason"], trade["exit_reason"])
    body = (
        f"Stock: {stock_info}\nExit reason: {reason}\n"
        f"Entry: {trade['entry_price']:.2f} → Exit: {trade['exit_price']:.2f}\n"
        f"P/L: {pnl_sign}{pnl:.2f} ({pnl_sign}{trade['pnl_pct']:.2f}%) | Held: {trade['holding_days']} days"
        if english else
        f"股票: {stock_info}\n平仓原因: {reason}\n"
        f"买入价: {trade['entry_price']:.2f} → 卖出价: {trade['exit_price']:.2f}\n"
        f"盈亏: {pnl_sign}{pnl:.2f} ({pnl_sign}{trade['pnl_pct']:.2f}%) | 持仓: {trade['holding_days']}天"
    )
    return title, body





def _funding_lines(account: PaperTradingAccount, funds: dict | None, english: bool) -> list[str]:
    lines = [f"Cash balance: {account.current_capital:,.2f}" if english else f"现金余额: {account.current_capital:,.2f}"]
    if funds is not None:
        lines.append(f"Buying power: {funds['buying_power']:,.2f}" if english else f"购买力: {funds['buying_power']:,.2f}")
        lines.append(f"Unsettled sale proceeds: {funds['unsettled_cash']:,.2f}" if english else f"待交收卖出款: {funds['unsettled_cash']:,.2f}")
    return lines


def _account_funds(db, account: PaperTradingAccount) -> dict:
    from src.modules.paper_trading.paper_settlement import cash_summary
    from src.modules.paper_trading.paper_trading_engine import account_buying_power

    funds = cash_summary(db, account.current_capital)
    funds["buying_power"] = round(account_buying_power(db, account), 2)
    return funds


def _format_premarket_plan(decisions, account, english=False, funds=None):
    title = "[Qlib paper pre-market plan]" if english else "【Qlib 模拟盘盘前计划】"
    lines = _funding_lines(account, funds, english)
    for decision in decisions:
        payload = decision.payload
        lines.append(f"{payload['execution_date']} · run={payload['run_id']} · model={payload['model_id']}")
        lines.append(", ".join(payload["symbols"]))
    if not decisions:
        lines.append("No approved decision" if english else "暂无已审批的冻结决策")
    return title, "\n".join(lines)


def _format_daily_summary(
    trades: list[PaperTradingTrade],
    positions: list[PaperTradingPosition],
    account: PaperTradingAccount,
    english: bool = False,
    total_equity: float | None = None,
    funds: dict | None = None,
) -> tuple[str, str]:
    """格式化日终摘要，返回 (title, body)。"""
    # 总资产
    positions_value = sum((p.current_price or p.entry_price) * p.quantity for p in positions)
    if total_equity is None:
        total_equity = account.current_capital + positions_value
    unrealized = sum(p.unrealized_pnl or 0 for p in positions)

    title = "[Paper trading daily summary]" if english else "【模拟盘日终摘要】"
    lines = [f"Total equity: {total_equity:,.2f}" if english else f"总资产: {total_equity:,.2f}"]

    # 当日平仓
    if trades:
        day_pnl = sum(t.pnl for t in trades)
        pnl_sign = "+" if day_pnl >= 0 else ""
        lines.append(f"\nClosed today: {len(trades)} trades, P/L: {pnl_sign}{day_pnl:,.2f}" if english else f"\n当日平仓 {len(trades)} 笔, 盈亏: {pnl_sign}{day_pnl:,.2f}")
        for t in trades:
            s = "+" if t.pnl >= 0 else ""
            reason = (EXIT_REASON_LABELS_EN if english else EXIT_REASON_LABELS).get(t.exit_reason, t.exit_reason)
            lines.append(f"  · {t.stock_name or t.stock_symbol}: {s}{t.pnl:,.2f} ({s}{t.pnl_pct:.2f}%) [{reason}]")
    else:
        lines.append("\nNo positions closed today" if english else "\n当日无平仓操作")

    # 持仓浮盈
    if positions:
        u_sign = "+" if unrealized >= 0 else ""
        lines.append(f"\nOpen positions: {len(positions)}, unrealized P/L: {u_sign}{unrealized:,.2f}" if english else f"\n持仓中 {len(positions)} 只, 浮动盈亏: {u_sign}{unrealized:,.2f}")
        for p in positions:
            pnl = p.unrealized_pnl or 0
            s = "+" if pnl >= 0 else ""
            lines.append(f"  · {p.stock_name or p.stock_symbol}: {s}{pnl:,.2f}")
    else:
        lines.append("\nNo open positions" if english else "\n当前无持仓")

    lines.extend(_funding_lines(account, funds, english))
    return title, "\n".join(lines)


# ---------------------------------------------------------------------------
# 触发函数
# ---------------------------------------------------------------------------

async def notify_entry(pos: dict, sig: dict | None) -> None:
    """建仓通知（异步，失败仅日志）。pos/sig 为序列化后的 dict。"""
    try:
        if not _is_mode_enabled("pt_notify_realtime"):
            return
        mgr = _build_notifier()
        if not mgr:
            return
        title, body = _format_entry_message(pos, sig, english=_report_is_english())
        await mgr.notify(title, body)
    except Exception:
        logger.exception("[模拟盘通知] 建仓通知发送失败")


async def notify_exit(pos: dict, trade: dict) -> None:
    """平仓通知（异步，失败仅日志）。pos/trade 为序列化后的 dict。"""
    try:
        if not _is_mode_enabled("pt_notify_realtime"):
            return
        mgr = _build_notifier()
        if not mgr:
            return
        title, body = _format_exit_message(pos, trade, english=_report_is_english())
        await mgr.notify(title, body)
    except Exception:
        logger.exception("[模拟盘通知] 平仓通知发送失败")


async def send_premarket_plan(*, markets: list[str] | None = None) -> None:
    """盘前计划通知。"""
    try:
        if not _is_mode_enabled("pt_notify_premarket"):
            return
        mgr = _build_notifier()
        if not mgr:
            return

        db = SessionLocal()
        try:
            account = db.query(PaperTradingAccount).first()
            if not account or not account.enabled:
                return

            from src.modules.paper_trading.paper_trading_engine import market_allocations_or_default
            alloc = market_allocations_or_default(account)
            for market in calendar.eligible_markets(markets):
                if alloc.get(market, 0.0) <= 0:
                    continue
                if market != "CN":
                    continue
                from src.platform.persistence.qlib_models import QlibRecord
                day = calendar._resolve_date(calendar._to_market_code(market), None)
                decisions = [item for item in db.query(QlibRecord).filter_by(kind="decision").all()
                             if item.payload["execution_date"] == day.isoformat()]
                title, body = _format_premarket_plan(decisions, account, english=_report_is_english(),
                    funds=_account_funds(db, account))
                day = calendar._resolve_date(calendar._to_market_code(market), None)
                await mgr.notify(f"{title} {market} · {day}", body)
        finally:
            db.close()
    except Exception:
        logger.exception("[模拟盘通知] 盘前计划发送失败")


async def send_daily_summary(*, markets: list[str] | None = None) -> None:
    """日终摘要通知。"""
    try:
        if not _is_mode_enabled("pt_notify_summary"):
            return
        mgr = _build_notifier()
        if not mgr:
            return

        db = SessionLocal()
        try:
            account = db.query(PaperTradingAccount).first()
            if not account or not account.enabled:
                return

            from src.modules.paper_trading.paper_trading_engine import market_allocations_or_default
            alloc = market_allocations_or_default(account)
            all_positions = db.query(PaperTradingPosition).filter(PaperTradingPosition.status == "open").all()
            total_equity = account.current_capital + sum((p.current_price or p.entry_price) * p.quantity for p in all_positions)
            for market in calendar.eligible_markets(markets):
                start, end = calendar.local_day_bounds(market)
                trades = (db.query(PaperTradingTrade).filter(
                    PaperTradingTrade.stock_market == market,
                    PaperTradingTrade.closed_at >= start,
                    PaperTradingTrade.closed_at < end,
                ).order_by(PaperTradingTrade.closed_at.desc()).all())
                positions = [p for p in all_positions if p.stock_market == market]
                if alloc.get(market, 0.0) <= 0 and not positions and not trades:
                    continue
                title, body = _format_daily_summary(trades, positions, account,
                    english=_report_is_english(), total_equity=total_equity,
                    funds=_account_funds(db, account))
                day = calendar._resolve_date(calendar._to_market_code(market), None)
                await mgr.notify(f"{title} {market} · {day}", body)
        finally:
            db.close()
    except Exception:
        logger.exception("[模拟盘通知] 日终摘要发送失败")


async def send_test_notification() -> dict:
    """发送测试通知，返回结果。"""
    mgr = _build_notifier()
    if not mgr:
        return {"success": False, "error": "通知未启用或无可用渠道"}
    english = _report_is_english()
    result = await mgr.notify_with_result(
        "[Paper trading test]" if english else "【模拟盘测试】",
        "This is a test notification to confirm that the channel is configured correctly." if english else "这是一条测试通知，确认通知渠道配置正常。",
    )
    return result
