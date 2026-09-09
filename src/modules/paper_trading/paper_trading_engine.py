"""模拟盘引擎：自动按策略信号建仓/平仓，跟踪虚拟账户收益。"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from src.core.marketdata_client import md_quote_rows
from src.models.market import MarketCode, MARKETS
from src.web.database import SessionLocal
from src.web.models import (
    PaperTradingAccount,
    PaperTradingPosition,
    PaperTradingTrade,
    StrategySignalRun,
)
from src.core.backtest.cost_model import CostModel

logger = logging.getLogger(__name__)

# 模拟盘交易成本(A股口径,Phase 1)。与回测共用同一成本模型。
COST_MODEL = CostModel()

# 建仓股数下限(A股一手)
FIXED_QUANTITY = 100

# 移动止损:浮盈超过 MIN_PROFIT_FOR_TRAILING 后启用,从持仓最高价回撤超 TRAILING_STOP_PCT 即离场
MIN_PROFIT_FOR_TRAILING = 0.05
TRAILING_STOP_PCT = 0.10
# 时间止损:无 signal.holding_days 时的默认最大持有自然日
DEFAULT_TIME_STOP_DAYS = 20


def _position_weight(rank_score: float) -> float:
    """按信号强度分配单笔资金占该市场预算的比例(rank_score 越高投越多)。"""
    s = float(rank_score or 0.0)
    if s >= 85:
        return 0.25
    if s >= 75:
        return 0.18
    if s >= 65:
        return 0.12
    return 0.08


def _compute_quantity(
    *,
    rank_score: float,
    market_budget: float,
    price: float,
    available_cash: float,
    cost_model: CostModel,
    lot: int = FIXED_QUANTITY,
) -> int:
    """按信号强度 + 市场预算计算建仓股数(lot 整数倍),受可用现金(含买入费)约束。

    返回 0 表示连最小一手都买不起,应跳过。
    """
    if price <= 0:
        return 0
    target_cash = max(0.0, market_budget) * _position_weight(rank_score)
    qty = int((target_cash / price) // lot) * lot
    if qty < lot:
        qty = lot  # 至少一手
    while qty >= lot:
        outlay = -cost_model.fill("buy", price, qty).cash_delta
        if outlay <= available_cash:
            return qty
        qty -= lot
    return 0


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _to_market(market: str) -> MarketCode:
    try:
        return MarketCode(market)
    except Exception:
        return MarketCode.CN


def _is_trading_time(market: str) -> bool:
    mc = _to_market(market)
    market_def = MARKETS.get(mc)
    if not market_def:
        return False
    return market_def.is_trading_time()


def _safe_float(v: Any) -> float | None:
    try:
        if v is None:
            return None
        return float(v)
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
    """某市场可用现金 = 总资金×比例 + 该市场已实现盈亏 − 该市场持仓成本（纯函数，可单测）。"""
    return initial_capital * ratio + realized_pnl - open_cost


def market_realized_open(db: Session, market: str) -> tuple[float, float]:
    """返回 (该市场已实现盈亏合计, 该市场未平仓持仓成本合计)。"""
    realized = (
        db.query(func.coalesce(func.sum(PaperTradingTrade.pnl), 0.0))
        .filter(PaperTradingTrade.stock_market == market)
        .scalar()
    ) or 0.0
    open_cost = (
        db.query(
            func.coalesce(
                func.sum(PaperTradingPosition.entry_price * PaperTradingPosition.quantity),
                0.0,
            )
        )
        .filter(
            PaperTradingPosition.status == "open",
            PaperTradingPosition.stock_market == market,
        )
        .scalar()
    ) or 0.0
    return float(realized), float(open_cost)


def market_available_cash(
    db: Session, account: PaperTradingAccount, market: str, alloc: dict | None = None
) -> float:
    """某市场当前可用现金（用于建仓门槛与展示）。"""
    alloc = alloc or market_allocations_or_default(account)
    ratio = alloc.get(market, 0.0)
    realized, open_cost = market_realized_open(db, market)
    return compute_market_cash(account.initial_capital, ratio, realized, open_cost)


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


def _serialize_signal(sig: StrategySignalRun) -> dict:
    """将 ORM Signal 提取为 plain dict。"""
    return {
        "id": sig.id,
        "stock_symbol": sig.stock_symbol,
        "stock_market": sig.stock_market,
        "stock_name": sig.stock_name or "",
        "strategy_code": sig.strategy_code or "",
        "rank_score": sig.rank_score,
        "entry_low": sig.entry_low,
        "entry_high": sig.entry_high,
        "action": sig.action,
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

    def _check_entries(
        self, db: Session, account: PaperTradingAccount,
    ) -> tuple[int, set[tuple[str, str]], list[tuple[PaperTradingPosition, StrategySignalRun | None]]]:
        """检查可入场的策略信号，自动建仓。返回 (建仓数, 新建仓股票key集合, 建仓事件列表)。"""
        # 查询最新活跃买入信号
        query = (
            db.query(StrategySignalRun)
            .filter(
                StrategySignalRun.status == "active",
                StrategySignalRun.action.in_(["buy", "add"]),
                StrategySignalRun.entry_low.isnot(None),
                StrategySignalRun.entry_high.isnot(None),
            )
        )
        # 按投资比例排除不投入（比例为 0）的市场
        alloc = market_allocations_or_default(account)
        excluded = [m for m in ALL_MARKETS if alloc.get(m, 0.0) <= 0]
        if excluded:
            query = query.filter(StrategySignalRun.stock_market.notin_(excluded))
        signals = query.order_by(StrategySignalRun.rank_score.desc()).limit(50).all()
        entry_events: list[tuple[PaperTradingPosition, StrategySignalRun | None]] = []
        new_keys: set[tuple[str, str]] = set()
        if not signals:
            return 0, new_keys, entry_events

        # 已有 open position 的股票
        open_keys = set()
        open_positions = (
            db.query(PaperTradingPosition)
            .filter(PaperTradingPosition.status == "open")
            .all()
        )
        for p in open_positions:
            open_keys.add((p.stock_symbol, p.stock_market))

        # 收集需要报价的信号（去重：同股票只取 rank_score 最高的一条）
        candidates = []
        seen = set()
        for sig in signals:
            key = (sig.stock_symbol, sig.stock_market)
            if key in open_keys:
                continue
            if key in seen:
                continue
            seen.add(key)
            candidates.append(sig)

        if not candidates:
            return 0, new_keys, entry_events

        # 批量获取报价
        syms = [(s.stock_symbol, s.stock_market) for s in candidates]
        quotes = self._fetch_quotes_map(syms)

        # 预算各市场可用现金（建仓时按市场子池逐笔扣减）
        market_cash = {m: market_available_cash(db, account, m, alloc) for m in ALL_MARKETS}

        opened = 0
        for sig in candidates:
            key = (sig.stock_market, sig.stock_symbol)
            quote = quotes.get(key)
            if not quote:
                continue
            current_price = _safe_float(quote.get("current_price"))
            if current_price is None or current_price <= 0:
                continue

            # 用当前市价入场
            entry_price = current_price
            mkt = sig.stock_market
            if alloc.get(mkt, 0.0) <= 0:
                continue  # 该市场比例为 0，不投入
            avail = market_cash.get(mkt, 0.0)

            # 仓位管理:按信号强度分配该市场预算(替换原固定 100 股)
            market_budget = account.initial_capital * alloc.get(mkt, 0.0)
            quantity = _compute_quantity(
                rank_score=float(sig.rank_score or 0.0),
                market_budget=market_budget,
                price=entry_price,
                available_cash=avail,
                cost_model=COST_MODEL,
            )
            if quantity <= 0:
                continue  # 子池额度不足以买入最小一手

            # 含交易成本的实际买入流出
            buy_fill = COST_MODEL.fill("buy", entry_price, quantity)
            buy_outlay = -buy_fill.cash_delta

            # 基于入场价计算止损/止盈
            # 优先用信号的止损/止盈比例，否则用默认 -8%/+15%
            stop_loss = sig.stop_loss
            target_price = sig.target_price
            if stop_loss and sig.entry_low and sig.entry_low > 0:
                # 保留信号的止损比例，映射到实际入场价
                orig_mid = (sig.entry_low + (sig.entry_high or sig.entry_low)) / 2
                if orig_mid > 0:
                    stop_ratio = (stop_loss - orig_mid) / orig_mid
                    target_ratio = ((target_price - orig_mid) / orig_mid) if target_price else 0.15
                    stop_loss = round(entry_price * (1 + stop_ratio), 4)
                    target_price = round(entry_price * (1 + target_ratio), 4) if target_price else None
            # 兜底：止损不合理时用默认 -8%
            if not stop_loss or stop_loss <= 0 or stop_loss >= entry_price:
                stop_loss = round(entry_price * 0.92, 4)
            # 兜底：止盈不合理时用默认 +15%
            if not target_price or target_price <= 0 or target_price <= entry_price:
                target_price = round(entry_price * 1.15, 4)

            pos = PaperTradingPosition(
                stock_symbol=sig.stock_symbol,
                stock_market=sig.stock_market,
                stock_name=sig.stock_name or "",
                quantity=quantity,
                entry_price=entry_price,
                stop_loss=stop_loss,
                target_price=target_price,
                current_price=current_price,
                highest_price=entry_price,
                unrealized_pnl=0.0,
                status="open",
                signal_run_id=sig.id,
                signal_snapshot_date=sig.snapshot_date or "",
                signal_action=sig.action or "",
                strategy_code=sig.strategy_code or "",
            )
            db.add(pos)
            account.current_capital -= buy_outlay
            market_cash[mkt] = avail - buy_outlay
            open_keys.add((sig.stock_symbol, sig.stock_market))
            new_keys.add((sig.stock_symbol, sig.stock_market))
            entry_events.append((pos, sig))
            opened += 1
            logger.info(
                "[模拟盘] 建仓: %s %s @ %.2f x%d, 止损=%.2f, 止盈=%s, 买入费=%.2f, 策略=%s",
                sig.stock_name or sig.stock_symbol,
                sig.stock_market,
                entry_price,
                quantity,
                stop_loss or 0,
                target_price or "无",
                buy_fill.explicit_fees + buy_fill.slippage_cost,
                sig.strategy_code,
            )

        if opened > 0:
            db.commit()
        return opened, new_keys, entry_events

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
        # 含交易成本的净盈亏:卖出净回收 − 建仓含费投入(与建仓口径一致,资金守恒)
        buy_cost = -COST_MODEL.fill("buy", pos.entry_price, pos.quantity).cash_delta
        sell_fill = COST_MODEL.fill("sell", exit_price, pos.quantity)
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
        )
        db.add(trade)

        pos.status = "closed"
        pos.closed_at = now
        pos.current_price = exit_price
        pos.unrealized_pnl = pnl

        # 回收资金(卖出净回收,已扣卖出费)
        account.current_capital += sell_proceeds
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

    def _check_exits(
        self, db: Session, account: PaperTradingAccount, skip_keys: set[tuple[str, str]] | None = None,
    ) -> tuple[int, list[tuple[PaperTradingPosition, PaperTradingTrade]]]:
        """检查持仓止损/止盈/信号反转，自动平仓。skip_keys 中的股票跳过（本轮新建仓）。"""
        exit_events: list[tuple[PaperTradingPosition, PaperTradingTrade]] = []
        positions = (
            db.query(PaperTradingPosition)
            .filter(PaperTradingPosition.status == "open")
            .all()
        )
        if not positions:
            return 0, exit_events

        # 批量获取报价
        syms = [(p.stock_symbol, p.stock_market) for p in positions]
        quotes = self._fetch_quotes_map(syms)

        closed = 0
        for pos in positions:
            # 跳过本轮刚建仓的持仓
            if skip_keys and (pos.stock_symbol, pos.stock_market) in skip_keys:
                continue
            key = (pos.stock_market, pos.stock_symbol)
            quote = quotes.get(key)
            current_price = _safe_float(quote.get("current_price")) if quote else None

            if current_price is None or current_price <= 0:
                continue

            # 更新现价、净浮动盈亏(含若此刻平仓的双边成本)、持仓期最高价
            pos.current_price = current_price
            _buy_cost_u = -COST_MODEL.fill("buy", pos.entry_price, pos.quantity).cash_delta
            _sell_u = COST_MODEL.fill("sell", current_price, pos.quantity).cash_delta
            pos.unrealized_pnl = round(_sell_u - _buy_cost_u, 4)
            if pos.highest_price is None or current_price > pos.highest_price:
                pos.highest_price = current_price

            # 检查止损
            if pos.stop_loss and current_price <= pos.stop_loss:
                trade = self._close_position(db, account, pos, current_price, "stop_loss")
                exit_events.append((pos, trade))
                closed += 1
                continue

            # 检查止盈
            if pos.target_price and current_price >= pos.target_price:
                trade = self._close_position(db, account, pos, current_price, "target_price")
                exit_events.append((pos, trade))
                closed += 1
                continue

            # 移动止损:浮盈达标后,从持仓最高价回撤超阈值则离场
            if pos.highest_price and pos.entry_price > 0:
                profit_ratio = (pos.highest_price - pos.entry_price) / pos.entry_price
                if profit_ratio >= MIN_PROFIT_FOR_TRAILING:
                    trail_line = pos.highest_price * (1 - TRAILING_STOP_PCT)
                    if current_price <= trail_line:
                        trade = self._close_position(db, account, pos, current_price, "trailing_stop")
                        exit_events.append((pos, trade))
                        closed += 1
                        continue

            # 检查信号反转
            if pos.signal_run_id:
                # no_autoflush: 信号查询是只读的,不要把本轮累积的持仓现价更新提前 flush——
                # 否则扫描中途会反复抢 SQLite 写锁,与其它调度器并发写时触发 "database is locked"。
                # 所有写入统一在本方法末尾 db.commit() 时一次性落盘。
                with db.no_autoflush:
                    latest = (
                        db.query(StrategySignalRun)
                        .filter(
                            StrategySignalRun.stock_symbol == pos.stock_symbol,
                            StrategySignalRun.stock_market == pos.stock_market,
                            StrategySignalRun.status == "active",
                        )
                        .order_by(StrategySignalRun.created_at.desc())
                        .first()
                    )
                if latest and latest.action in ("sell", "reduce"):
                    trade = self._close_position(db, account, pos, current_price, "signal_reversal")
                    exit_events.append((pos, trade))
                    closed += 1
                    continue

            # 时间止损:持有超过最大自然日离场(优先用 signal 的 holding_days)
            max_days = DEFAULT_TIME_STOP_DAYS
            if pos.signal_run_id:
                sig_hold = (
                    db.query(StrategySignalRun.holding_days)
                    .filter(StrategySignalRun.id == pos.signal_run_id)
                    .scalar()
                )
                if sig_hold and int(sig_hold) > 0:
                    max_days = int(sig_hold)
            if pos.opened_at:
                opened_dt = pos.opened_at
                if opened_dt.tzinfo is None:
                    opened_dt = opened_dt.replace(tzinfo=timezone.utc)
                if (_utc_now() - opened_dt).days >= max_days:
                    trade = self._close_position(db, account, pos, current_price, "time_stop")
                    exit_events.append((pos, trade))
                    closed += 1
                    continue

        self._update_account_metrics(db, account)
        db.commit()
        return closed, exit_events

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

    def _scan_sync(self) -> dict:
        """同步扫描（在线程中执行）。"""
        db = SessionLocal()
        try:
            account = self._get_or_create_account(db)
            if not account.enabled:
                return {"status": "disabled"}

            opened, new_keys, entry_events = self._check_entries(db, account)
            closed, exit_events = self._check_exits(db, account, skip_keys=new_keys)

            # 在 db.close() 前将 ORM 对象序列化为 dict，避免 detached 问题
            serialized_entries = [
                {"pos_data": _serialize_position(pos), "sig_data": _serialize_signal(sig) if sig else None}
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

            # 获取最新报价(走 flag 门控的 md_quote_rows,支持故障转移)
            mc = _to_market(pos.stock_market)
            rows = md_quote_rows([pos.stock_symbol], mc.value)

            exit_price = pos.current_price or pos.entry_price
            if rows:
                p = _safe_float(rows[0].get("current_price"))
                if p and p > 0:
                    exit_price = p

            trade = self._close_position(db, account, pos, exit_price, "manual")
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
                from src.core.paper_trading_notifier import notify_exit
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
            from src.core.paper_trading_notifier import notify_entry, notify_exit

            for evt in result.pop("entry_events", []):
                await notify_entry(evt["pos_data"], evt.get("sig_data"))
            for evt in result.pop("exit_events", []):
                await notify_exit(evt["pos_data"], evt["trade_data"])
        except Exception:
            logger.exception("[模拟盘] 通知发送失败")

    def reset_account(self) -> dict:
        """重置模拟盘（清空所有数据）。"""
        db = SessionLocal()
        try:
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
