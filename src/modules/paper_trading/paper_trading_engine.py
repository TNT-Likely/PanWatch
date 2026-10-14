"""模拟盘引擎：执行已确认的 Qlib 决策，保留独立的历史账本。"""

from __future__ import annotations

import asyncio
import logging
import math
from functools import wraps
from threading import RLock
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import func
from sqlalchemy.orm import Session

from src.platform.marketdata.marketdata_client import md_quote_rows
from src.platform.marketdata.models import MarketCode
from src.platform.marketdata.quote_display import quote_date_is_current
from src.platform.persistence.database import SessionLocal
from src.platform.persistence.models import (
    PaperTradingAccount,
    PaperTradingPosition,
    PaperTradingSettlement,
    PaperTradingTrade,
)
from src.platform.execution.cost_model import CostModel
from src.modules.qlib_research.costs import QlibCostModel
from src.modules.paper_trading.paper_settlement import (
    buying_power, consume_cn_proceeds, record_sale, settle_due, settlement_date,
)

logger = logging.getLogger(__name__)

# The scheduler and manual requests share one cash account in the server process.
# Serialize reads and writes together so concurrent scans cannot spend one balance twice.
ACCOUNT_LOCK = RLock()


def _account_mutation(method):
    @wraps(method)
    def guarded(*args, **kwargs):
        with ACCOUNT_LOCK:
            return method(*args, **kwargs)
    return guarded

# 模拟盘交易成本(A股口径,Phase 1)。与回测共用同一成本模型。
COST_MODEL = CostModel()




def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


SELL_BLOCK_MESSAGES = {
    "paper_trading_t1_locked": "A 股实行 T+1：当日买入的持仓须到下一交易日才能卖出",
    "paper_trading_open_time_missing": "持仓买入时间缺失，无法确认可卖数量",
}


def sell_block_reason(pos: PaperTradingPosition, *, now: datetime | None = None) -> str | None:
    """Settlement eligibility only; fills still require an open market and valid quote.

    SQLite stores naive UTC timestamps. T+1 uses the Shanghai purchase date,
    not elapsed 24-hour periods. The existing calendar guards enforce sessions.
    """
    if pos.stock_market != "CN":
        return None
    if pos.opened_at is None:
        return "paper_trading_open_time_missing"
    opened = pos.opened_at
    if opened.tzinfo is None:
        opened = opened.replace(tzinfo=timezone.utc)
    current = now if now is not None else _utc_now()
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    shanghai = ZoneInfo("Asia/Shanghai")
    if opened.astimezone(shanghai).date() >= current.astimezone(shanghai).date():
        return "paper_trading_t1_locked"
    return None


def _to_market(market: str) -> MarketCode:
    try:
        return MarketCode(market)
    except Exception:
        return MarketCode.CN


def _is_trading_time(market: str) -> bool:
    from src.platform.scheduling.trading_calendar import market_status
    return market_status(market) == "trading"


def _safe_float(v: Any) -> float | None:
    try:
        if v is None:
            return None
        value = float(v)
        return value if math.isfinite(value) else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 分市场资金配置（投资比例 → 子池现金）
# ---------------------------------------------------------------------------

ALL_MARKETS: tuple[str, ...] = ("CN", "HK", "US")
DEFAULT_ALLOCATIONS: dict[str, float] = {"CN": 0.5, "HK": 0.3, "US": 0.2}


def normalize_allocations(raw: dict | None) -> dict[str, float]:
    """补齐三市场、clamp 到 [0,1]，返回 {market: ratio}。"""
    raw = raw or {}
    out: dict[str, float] = {}
    for m in ALL_MARKETS:
        try:
            v = float(raw.get(m, 0.0) or 0.0)
        except Exception:
            v = 0.0
        out[m] = min(1.0, max(0.0, v))
    return out


def market_allocations_or_default(account: Any) -> dict[str, float]:
    """账户未配置比例时回退默认配置，否则归一化已配置的比例。"""
    raw = getattr(account, "market_allocations", None) or {}
    if not raw:
        return dict(DEFAULT_ALLOCATIONS)
    return normalize_allocations(raw)


def allocations_from_excluded(excluded: list[str] | None) -> dict[str, float]:
    """迁移用：被排除市场比例置 0，其余市场按默认权重归一化到合计 1.0。"""
    excluded_set = {str(m).upper() for m in (excluded or [])}
    weights = {m: DEFAULT_ALLOCATIONS[m] for m in ALL_MARKETS if m not in excluded_set}
    total = sum(weights.values())
    if total <= 0:
        # 全部被排除：兜底投 A 股
        return {"CN": 1.0, "HK": 0.0, "US": 0.0}
    return {m: round(weights.get(m, 0.0) / total, 6) for m in ALL_MARKETS}


def compute_market_cash(
    initial_capital: float, ratio: float, realized_pnl: float, open_cost: float
) -> float:
    """某市场账面现金 = 总资金×比例 + 已实现盈亏 − 持仓含费投入。"""
    return initial_capital * ratio + realized_pnl - open_cost


def position_buy_cost(pos: PaperTradingPosition) -> float:
    meta = pos.research_meta or {}
    return float(meta["buy_outlay"]) if "buy_outlay" in meta else -COST_MODEL.fill("buy", pos.entry_price, pos.quantity).cash_delta


def position_cost_model(pos):
    meta = pos.research_meta or {}
    return QlibCostModel(meta["config"]) if meta.get("config") else COST_MODEL


def market_realized_open(db: Session, market: str) -> tuple[float, float]:
    """返回 (该市场已实现盈亏合计, 该市场未平仓持仓成本合计)。"""
    realized = (
        db.query(func.coalesce(func.sum(PaperTradingTrade.pnl), 0.0))
        .filter(PaperTradingTrade.stock_market == market)
        .scalar()
    ) or 0.0
    positions = (
        db.query(PaperTradingPosition)
        .filter(
            PaperTradingPosition.status == "open",
            PaperTradingPosition.stock_market == market,
        )
        .all()
    )
    open_cost = sum(position_buy_cost(p) for p in positions)
    return float(realized), float(open_cost)


def market_available_cash(
    db: Session, account: PaperTradingAccount, market: str, alloc: dict | None = None
) -> float:
    """某市场当前可用现金（用于建仓门槛与展示）。"""
    alloc = alloc or market_allocations_or_default(account)
    ratio = alloc.get(market, 0.0)
    realized, open_cost = market_realized_open(db, market)
    cash = compute_market_cash(account.initial_capital, ratio, realized, open_cost)
    now = _utc_now()
    return min(buying_power(db, cash, market, now=now),
               buying_power(db, account.current_capital, market, now=now, all_markets=True))


def account_buying_power(db: Session, account: PaperTradingAccount) -> float:
    """Aggregate allocated limits, capped by the shared cash balance."""
    return min(buying_power(db, account.current_capital, "CN", now=_utc_now(), all_markets=True),
               sum(market_available_cash(db, account, m) for m in ALL_MARKETS))


def _serialize_position(pos: PaperTradingPosition) -> dict:
    """将 ORM Position 提取为 plain dict，避免 detached 问题。"""
    return {
        "id": pos.id,
        "stock_symbol": pos.stock_symbol,
        "stock_market": pos.stock_market,
        "stock_name": pos.stock_name or "",
        "quantity": pos.quantity,
        "entry_price": pos.entry_price,
        "stop_loss": pos.stop_loss,
        "target_price": pos.target_price,
        "current_price": pos.current_price,
        "unrealized_pnl": pos.unrealized_pnl,
        "status": pos.status,
        "strategy_code": pos.strategy_code or "",
        "research": getattr(pos, "research_meta", None) or {},
    }


def _serialize_trade(trade: PaperTradingTrade) -> dict:
    """将 ORM Trade 提取为 plain dict。"""
    return {
        "id": trade.id,
        "stock_symbol": trade.stock_symbol,
        "stock_market": trade.stock_market,
        "stock_name": trade.stock_name or "",
        "quantity": trade.quantity,
        "entry_price": trade.entry_price,
        "exit_price": trade.exit_price,
        "pnl": trade.pnl,
        "pnl_pct": trade.pnl_pct,
        "exit_reason": trade.exit_reason,
        "holding_days": trade.holding_days,
        "strategy_code": trade.strategy_code or "",
    }





class PaperTradingEngine:
    """模拟盘扫描引擎。"""

    def _get_or_create_account(self, db: Session) -> PaperTradingAccount:
        account = db.query(PaperTradingAccount).first()
        if not account:
            account = PaperTradingAccount(
                initial_capital=1000000.0,
                current_capital=1000000.0,
                peak_capital=1000000.0,
            )
            db.add(account)
            db.commit()
            db.refresh(account)
        return account

    def _fetch_quotes_map(self, symbols_markets: list[tuple[str, str]]) -> dict[tuple[str, str], dict]:
        """批量获取报价，返回 {(market, symbol): quote_dict}

        通过 QuoteOrchestrator 调度,支持多 provider 主备故障转移。
        """
        grouped: dict[MarketCode, list[str]] = {}
        for symbol, market in symbols_markets:
            mc = _to_market(market)
            grouped.setdefault(mc, []).append(symbol)

        out: dict[tuple[str, str], dict] = {}
        for market, symbols in grouped.items():
            if not symbols:
                continue
            rows = md_quote_rows(symbols, market.value)
            by_symbol = {str(r.get("symbol")): r for r in rows}
            for sym in symbols:
                q = by_symbol.get(sym)
                if q:
                    out[(market.value, sym)] = q
        return out

    def _rebalance_qlib(self, db, account):
        """Execute one explicitly approved frozen decision at the next open.

        No legacy holding is automatically managed. Opening-window fills are a
        virtual daily-bar convention, not actual broker executions.
        """
        import hashlib
        from src.platform.persistence.qlib_models import QlibAudit, QlibControl, QlibExecution, QlibRecord
        from src.modules.qlib_research.policy import dropout_plan, lot_quantity, paper_security_supported
        from src.modules.qlib_research.paper_plans import marker
        stamp = _utc_now().astimezone(ZoneInfo("Asia/Shanghai"))
        if not _is_trading_time("CN") or not (stamp.hour == 9 and 30 <= stamp.minute <= 35):
            return 0, [], []
        state = db.get(QlibControl, 1)
        if not state or not state.champion_id:
            return 0, [], []
        decision = db.query(QlibRecord).filter_by(kind="decision").order_by(QlibRecord.created_at.desc(), QlibRecord.id.desc()).first()
        if not decision or decision.payload["model_id"] != state.champion_id:
            return 0, [], []
        payload = decision.payload
        if payload["execution_date"] != stamp.date().isoformat():
            return 0, [], []
        marker_id = hashlib.sha256(("executed:" + decision.id).encode()).hexdigest()[:32]
        if db.get(QlibRecord, marker_id) or db.get(QlibRecord, marker("cancelled", decision.id)):
            return 0, [], []
        selection = db.get(QlibRecord, payload["run_id"])
        if not selection:
            return 0, [], []
        positions = db.query(PaperTradingPosition).filter_by(status="open", stock_market="CN").all()
        managed = {p.research_meta["instrument"]: p for p in positions if (p.research_meta or {}).get("instrument")}
        legacy = {p.stock_symbol for p in positions if not (p.research_meta or {}).get("instrument")}
        predictions = selection.payload["predictions"]
        frozen = payload.get("confirmation_kind") == "explicit_frozen_orders_v1"
        frozen_orders = {o["symbol"]: o for o in payload.get("orders", [])}
        symbols = set(frozen_orders) if frozen else {row["symbol"] for row in predictions} | set(managed)
        by_symbol = {row["symbol"]: row for row in predictions}
        supported = {s for s in symbols if paper_security_supported(by_symbol.get(s, {"symbol": s}))}
        quotes = self._fetch_quotes_map([(symbol[2:], "CN") for symbol in sorted(supported)])
        eligible, reasons, prices = set(), {}, {}
        for symbol in symbols:
            quote = quotes.get(("CN", symbol[2:]))
            price = _safe_float(quote.get("open_price")) if quote else None
            previous = _safe_float(quote.get("prev_close")) if quote else None
            volume = _safe_float(quote.get("volume")) if quote else None
            order = frozen_orders.get(symbol)
            if symbol not in supported:
                reasons[symbol] = "qlib_security_execution_not_supported"
            elif frozen and ((order["side"] == "buy" and symbol in managed) or
                             (order["side"] == "sell" and (symbol not in managed or managed[symbol].id != order["position_id"] or managed[symbol].quantity != order["estimated_quantity"]))):
                reasons[symbol] = "qlib_plan_holdings_changed"
            elif symbol[2:] in legacy:
                reasons[symbol] = "legacy_position_not_managed"
            elif not quote or not quote.get("quote_date") or not quote_date_is_current("CN", quote):
                reasons[symbol] = "quote_stale_or_missing"
            elif not price or price <= 0 or not previous or previous <= 0 or not volume or volume <= 0:
                reasons[symbol] = "opening_quote_unavailable"
            elif abs(price / previous - 1) >= payload["config"]["limit_threshold"]:
                reasons[symbol] = "opening_price_limit"
            elif symbol in managed and sell_block_reason(managed[symbol]):
                reasons[symbol] = "paper_trading_t1_locked"
            else:
                eligible.add(symbol)
                prices[symbol] = price
        if frozen:
            sell = [s for s, o in frozen_orders.items() if o["side"] == "sell" and s in eligible]
            buy = [s for s, o in frozen_orders.items() if o["side"] == "buy" and s in eligible]
        else:
            sell, buy = dropout_plan(predictions, managed, eligible, payload["config"]["topk"])
        exits, entries = [], []
        costs = QlibCostModel(payload["config"])
        def event(symbol, side, status, quantity=0, price=None, reason="", position=None):
            intent = hashlib.sha256(f"{decision.id}:{symbol}:{side}".encode()).hexdigest()[:32]
            db.add(QlibExecution(id=intent, decision_id=decision.id, run_id=payload["run_id"],
                model_id=payload["model_id"], symbol=symbol, side=side, status=status,
                quantity=quantity, price=price, reason=reason, position_id=position))
        for symbol, reason in reasons.items():
            event(symbol, frozen_orders[symbol]["side"] if frozen else "filter", "rejected", reason=reason)
        for symbol in sell:
            position = managed[symbol]
            trade = self._close_position(db, account, position, prices[symbol], "qlib_dropout")
            trade.meta = {**(trade.meta or {}), "exit_decision_id": decision.id}
            event(symbol, "sell", "filled", position.quantity, prices[symbol], position=position.id)
            exits.append((position, trade))
        available = market_available_cash(db, account, "CN")
        budget = (min(available * 0.9, payload["cash_budget"]) / len(buy) if frozen and buy
                  else available * 0.9 / len(buy) if buy else 0)
        for symbol in buy:
            price = prices[symbol]
            symbol_budget = min(budget, frozen_orders[symbol]["cash_limit"]) if frozen else budget
            quantity = lot_quantity(symbol_budget, price, min(available, symbol_budget) if frozen else available, costs)
            if quantity <= 0:
                event(symbol, "buy", "rejected", reason="insufficient_cash_or_lot")
                continue
            outlay = -costs.fill("buy", price, quantity).cash_delta
            position = PaperTradingPosition(stock_symbol=symbol[2:], stock_market="CN",
                stock_name=frozen_orders[symbol]["name"] if frozen else symbol, quantity=quantity, entry_price=price, current_price=price,
                highest_price=price, status="open", strategy_code="qlib", opened_at=_utc_now(),
                settlement_date=settlement_date("CN", _utc_now()),
                research_meta={"instrument": symbol, "run_id": payload["run_id"], "model_id": payload["model_id"],
                    "decision_id": decision.id, "policy": payload["policy"], "config": payload["config"],
                    "buy_outlay": outlay, "execution_kind": "virtual_next_open", "research_mode": payload["research_mode"]})
            db.add(position)
            db.flush()
            account.current_capital -= outlay
            consume_cn_proceeds(db, outlay, now=_utc_now())
            available -= outlay
            event(symbol, "buy", "filled", quantity, price, position=position.id)
            entries.append((position, None))
        db.add(QlibRecord(id=marker_id, kind="execution_batch", parent_id=decision.id, name=payload["execution_date"],
            payload={"decision_id": decision.id, "opened": len(entries), "closed": len(exits),
                     "assumptions": "virtual opening price, aggregate Qlib fees, no intraday matching or liquidity-impact model"}))
        db.add(QlibAudit(action="paper_execution", actor="paper_scheduler", target_id=decision.id,
            detail={"opened": len(entries), "closed": len(exits)}))
        return len(entries), entries, exits

    def _close_position(
        self,
        db: Session,
        account: PaperTradingAccount,
        pos: PaperTradingPosition,
        exit_price: float,
        exit_reason: str,
    ) -> PaperTradingTrade:
        """平仓单个持仓，返回交易记录。"""
        now = _utc_now()
        block = sell_block_reason(pos, now=now)
        if block:
            raise ValueError(block)
        # 含交易成本的净盈亏:卖出净回收 − 建仓含费投入(与建仓口径一致,资金守恒)
        buy_cost = position_buy_cost(pos)
        sell_fill = position_cost_model(pos).fill("sell", exit_price, pos.quantity)
        sell_proceeds = sell_fill.cash_delta
        pnl = round(sell_proceeds - buy_cost, 4)
        pnl_pct = (pnl / buy_cost * 100) if buy_cost > 0 else 0.0

        holding_days = 0
        if pos.opened_at:
            opened = pos.opened_at
            if opened.tzinfo is None:
                opened = opened.replace(tzinfo=timezone.utc)
            holding_days = max(0, (now - opened).days)

        trade = PaperTradingTrade(
            stock_symbol=pos.stock_symbol,
            stock_market=pos.stock_market,
            stock_name=pos.stock_name or "",
            quantity=pos.quantity,
            entry_price=pos.entry_price,
            exit_price=exit_price,
            pnl=pnl,
            pnl_pct=round(pnl_pct, 2),
            exit_reason=exit_reason,
            signal_run_id=pos.signal_run_id,
            signal_snapshot_date=pos.signal_snapshot_date or "",
            strategy_code=pos.strategy_code or "",
            holding_days=holding_days,
            opened_at=pos.opened_at,
            closed_at=now,
            meta={"research": pos.research_meta or {}},
        )
        db.add(trade)

        pos.status = "closed"
        pos.closed_at = now
        pos.current_price = exit_price
        pos.unrealized_pnl = pnl

        # 回收资金(卖出净回收,已扣卖出费)
        account.current_capital += sell_proceeds
        record_sale(db, trade, sell_proceeds)
        account.total_pnl += pnl
        account.total_trades += 1
        if pnl > 0:
            account.winning_trades += 1

        logger.info(
            "[模拟盘] 平仓: %s %s @ %.2f, 盈亏=%.2f (%.2f%%), 原因=%s",
            pos.stock_name or pos.stock_symbol,
            pos.stock_market,
            exit_price,
            pnl,
            pnl_pct,
            exit_reason,
        )
        return trade

    def _mark_positions(self, db):
        positions = db.query(PaperTradingPosition).filter_by(status="open").all()
        quotes = self._fetch_quotes_map([(p.stock_symbol, p.stock_market) for p in positions])
        for position in positions:
            if not _is_trading_time(position.stock_market):
                continue
            quote = quotes.get((position.stock_market, position.stock_symbol))
            price = _safe_float(quote.get("current_price")) if quote else None
            if not price or not quote_date_is_current(position.stock_market, quote):
                continue
            position.current_price = price
            position.unrealized_pnl = position_cost_model(position).fill("sell", price, position.quantity).cash_delta - position_buy_cost(position)

    def _update_account_metrics(self, db: Session, account: PaperTradingAccount) -> None:
        """更新账户峰值和最大回撤。"""
        # 计算包含浮动盈亏的总资产
        open_positions = (
            db.query(PaperTradingPosition)
            .filter(PaperTradingPosition.status == "open")
            .all()
        )
        unrealized_total = sum(p.unrealized_pnl or 0 for p in open_positions)
        total_equity = account.current_capital + sum(
            (p.current_price or p.entry_price) * p.quantity for p in open_positions
        )

        if total_equity > account.peak_capital:
            account.peak_capital = total_equity

        if account.peak_capital > 0:
            drawdown = (account.peak_capital - total_equity) / account.peak_capital * 100
            if drawdown > account.max_drawdown_pct:
                account.max_drawdown_pct = round(drawdown, 2)

    @_account_mutation
    def _scan_sync(self) -> dict:
        """同步扫描（在线程中执行）。"""
        db = SessionLocal()
        try:
            account = self._get_or_create_account(db)
            settled = settle_due(db, now=_utc_now())
            db.commit()
            if not account.enabled:
                return {"status": "disabled", "settled": settled}

            opened, entry_events, exit_events = self._rebalance_qlib(db, account)
            closed = len(exit_events)
            self._mark_positions(db)
            self._update_account_metrics(db, account)
            db.commit()

            # 在 db.close() 前将 ORM 对象序列化为 dict，避免 detached 问题
            serialized_entries = [
                {"pos_data": _serialize_position(pos), "sig_data": None}
                for pos, sig in entry_events
            ]
            serialized_exits = [
                {"pos_data": _serialize_position(pos), "trade_data": _serialize_trade(trade)}
                for pos, trade in exit_events
            ]

            return {
                "status": "ok",
                "opened": opened,
                "closed": closed,
                "settled": settled,
                "entry_events": serialized_entries,
                "exit_events": serialized_exits,
            }
        except Exception as e:
            logger.exception(f"[模拟盘] 扫描异常: {e}")
            return {"status": "error", "error": str(e)}
        finally:
            db.close()

    async def scan_once(self) -> dict:
        """异步扫描入口。"""
        result = await asyncio.to_thread(self._scan_sync)
        # 发送通知（异步，失败不影响交易）
        await self._send_notifications(result)
        return result

    @_account_mutation
    def close_position_manual(self, position_id: int) -> dict:
        """手动平仓。"""
        db = SessionLocal()
        try:
            account = self._get_or_create_account(db)
            pos = (
                db.query(PaperTradingPosition)
                .filter(
                    PaperTradingPosition.id == position_id,
                    PaperTradingPosition.status == "open",
                )
                .first()
            )
            if not pos:
                return {"ok": False, "error": "持仓不存在或已平仓"}

            block = sell_block_reason(pos)
            if block:
                return {"ok": False, "error_code": block, "error": SELL_BLOCK_MESSAGES[block]}

            if not _is_trading_time(pos.stock_market):
                return {"ok": False, "error": "该市场当前非交易时段，无法成交"}

            # 获取最新报价(走 flag 门控的 md_quote_rows,支持故障转移)
            mc = _to_market(pos.stock_market)
            rows = md_quote_rows([pos.stock_symbol], mc.value)

            exit_price = _safe_float(rows[0].get("current_price")) if rows else None
            if not exit_price or exit_price <= 0 or not quote_date_is_current(pos.stock_market, rows[0]):
                return {"ok": False, "error": "暂无有效报价，无法成交"}
            if not _is_trading_time(pos.stock_market):
                return {"ok": False, "error": "该市场当前非交易时段，无法成交"}

            trade = self._close_position(db, account, pos, exit_price, "manual")
            settle_due(db, now=_utc_now())
            self._update_account_metrics(db, account)
            db.commit()
            # 序列化后返回，避免 db.close() 后 ORM 对象 detached
            return {
                "ok": True,
                "pos_data": _serialize_position(pos),
                "trade_data": _serialize_trade(trade),
            }
        finally:
            db.close()

    async def close_position_manual_async(self, position_id: int) -> dict:
        """异步手动平仓，含通知。"""
        result = await asyncio.to_thread(self.close_position_manual, position_id)
        if result.get("ok"):
            try:
                from src.modules.paper_trading.paper_trading_notifier import notify_exit
                pos_data = result.pop("pos_data", None)
                trade_data = result.pop("trade_data", None)
                if pos_data and trade_data:
                    await notify_exit(pos_data, trade_data)
            except Exception:
                logger.exception("[模拟盘] 手动平仓通知失败")
        return result

    async def _send_notifications(self, result: dict) -> None:
        """从扫描结果中取出序列化事件，发送通知。"""
        try:
            from src.modules.paper_trading.paper_trading_notifier import notify_entry, notify_exit

            for evt in result.pop("entry_events", []):
                await notify_entry(evt["pos_data"], evt.get("sig_data"))
            for evt in result.pop("exit_events", []):
                await notify_exit(evt["pos_data"], evt["trade_data"])
        except Exception:
            logger.exception("[模拟盘] 通知发送失败")

    @_account_mutation
    def reset_account(self) -> dict:
        """重置模拟盘（清空所有数据）。"""
        db = SessionLocal()
        try:
            db.query(PaperTradingSettlement).delete()
            db.query(PaperTradingPosition).delete()
            db.query(PaperTradingTrade).delete()
            account = db.query(PaperTradingAccount).first()
            if account:
                account.current_capital = account.initial_capital
                account.total_pnl = 0.0
                account.total_trades = 0
                account.winning_trades = 0
                account.max_drawdown_pct = 0.0
                account.peak_capital = account.initial_capital
                account.enabled = True
            db.commit()
            return {"ok": True}
        finally:
            db.close()


ENGINE = PaperTradingEngine()
