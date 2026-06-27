"""持仓变动流水上下文（供 AI 对话与 Agent 使用）。"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from src.core.timezone import beijing_now, format_beijing, to_beijing
from src.web.models import Account, Position, PositionTrade, Stock


def is_today_beijing(dt: datetime | None) -> bool:
    if not dt:
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return to_beijing(dt).date() == beijing_now().date()


def format_trade_line(
    trade: PositionTrade,
    *,
    stock_name: str,
    symbol: str,
    market: str,
    account_name: str,
) -> str:
    side_label = "买入" if trade.side == "buy" else "卖出"
    time_str = format_beijing(trade.traded_at, "%m-%d %H:%M") if trade.traded_at else ""
    today_mark = "【今日】" if is_today_beijing(trade.traded_at) else ""
    note = f" 备注:{trade.note}" if trade.note else ""
    after = ""
    if trade.qty_after is not None and trade.cost_after is not None:
        after = f" → 持仓{trade.qty_after}股 成本{trade.cost_after:.4f}"
    return (
        f"- {today_mark}{side_label} {stock_name}({market}:{symbol}) "
        f"{trade.quantity}股 @{trade.price}{after} "
        f"({account_name}, {time_str}){note}"
    )


def serialize_trade_row(
    trade: PositionTrade,
    *,
    stock_name: str = "",
    symbol: str = "",
    market: str = "",
    account_name: str = "",
) -> dict:
    return {
        "id": trade.id,
        "position_id": trade.position_id,
        "side": trade.side,
        "price": trade.price,
        "quantity": trade.quantity,
        "amount": trade.amount,
        "cost_before": trade.cost_before,
        "qty_before": trade.qty_before,
        "cost_after": trade.cost_after,
        "qty_after": trade.qty_after,
        "note": trade.note,
        "traded_at": trade.traded_at.isoformat() if trade.traded_at else None,
        "stock_name": stock_name,
        "symbol": symbol,
        "market": market,
        "account_name": account_name,
        "is_today": is_today_beijing(trade.traded_at),
    }


def fetch_recent_trades(
    db: Session,
    *,
    symbol: str | None = None,
    market: str | None = None,
    today_only: bool = False,
    limit: int = 20,
) -> list[dict]:
    """查询最近持仓变动流水，返回序列化字典列表。"""
    lim = max(1, min(int(limit), 50))
    rows = (
        db.query(PositionTrade, Position, Stock, Account)
        .join(Position, PositionTrade.position_id == Position.id)
        .join(Stock, Position.stock_id == Stock.id)
        .join(Account, Position.account_id == Account.id)
        .order_by(PositionTrade.traded_at.desc(), PositionTrade.id.desc())
        .limit(lim * 3)
        .all()
    )
    out: list[dict] = []
    for trade, _pos, stock, account in rows:
        if symbol and stock.symbol != symbol:
            continue
        if market and stock.market != market:
            continue
        if today_only and not is_today_beijing(trade.traded_at):
            continue
        out.append(
            serialize_trade_row(
                trade,
                stock_name=stock.name,
                symbol=stock.symbol,
                market=stock.market,
                account_name=account.name if account else "账户",
            )
        )
        if len(out) >= lim:
            break
    return out


def build_trades_context_text(
    db: Session,
    *,
    symbol: str | None = None,
    market: str | None = None,
    today_only: bool = False,
    limit: int = 20,
) -> str:
    """构建最近持仓变动流水摘要文本。"""
    lim = max(1, min(int(limit), 50))
    rows = (
        db.query(PositionTrade, Position, Stock, Account)
        .join(Position, PositionTrade.position_id == Position.id)
        .join(Stock, Position.stock_id == Stock.id)
        .join(Account, Position.account_id == Account.id)
        .order_by(PositionTrade.traded_at.desc(), PositionTrade.id.desc())
        .limit(lim * 3)
        .all()
    )
    lines: list[str] = []
    for trade, _pos, stock, account in rows:
        if symbol and stock.symbol != symbol:
            continue
        if market and stock.market != market:
            continue
        if today_only and not is_today_beijing(trade.traded_at):
            continue
        lines.append(
            format_trade_line(
                trade,
                stock_name=stock.name,
                symbol=stock.symbol,
                market=stock.market,
                account_name=account.name if account else "账户",
            )
        )
        if len(lines) >= lim:
            break

    if not lines:
        return ""
    title = "今日持仓变动" if today_only else "最近持仓变动"
    return f"{title}：\n" + "\n".join(lines)


def fetch_today_trades_by_position_ids(
    db: Session,
    position_ids: list[int],
) -> dict[int, list[PositionTrade]]:
    """批量查询各持仓今日流水（按成交时间升序）。"""
    if not position_ids:
        return {}
    rows = (
        db.query(PositionTrade)
        .filter(PositionTrade.position_id.in_(position_ids))
        .order_by(PositionTrade.traded_at.asc(), PositionTrade.id.asc())
        .all()
    )
    grouped: dict[int, list[PositionTrade]] = {pid: [] for pid in position_ids}
    for trade in rows:
        if not is_today_beijing(trade.traded_at):
            continue
        grouped.setdefault(trade.position_id, []).append(trade)
    return grouped


def day_start_qty_from_today_trades(
    today_trades: list[PositionTrade],
    current_qty: int,
) -> int:
    """推断今日首笔流水前的持仓股数。"""
    if not today_trades:
        return current_qty
    first = today_trades[0]
    if first.qty_before is None:
        return 0
    return max(0, int(first.qty_before))


def summarize_today_trades(
    db: Session,
    *,
    symbol: str,
    market: str,
) -> dict:
    """汇总今日某标的买卖流水，供建议去重与降级。"""
    rows = fetch_recent_trades(
        db, symbol=symbol, market=market, today_only=True, limit=20
    )
    buy_qty = 0
    sell_qty = 0
    has_buy_today = False
    has_sell_today = False
    for row in rows:
        qty = int(row.get("quantity") or 0)
        if row.get("side") == "buy":
            has_buy_today = True
            buy_qty += qty
        elif row.get("side") == "sell":
            has_sell_today = True
            sell_qty += qty

    return {
        "has_buy_today": has_buy_today,
        "has_sell_today": has_sell_today,
        "buy_qty": buy_qty,
        "sell_qty": sell_qty,
        "net_qty": buy_qty - sell_qty,
        "context": build_trades_context_text(
            db, symbol=symbol, market=market, today_only=True, limit=10
        ),
    }
