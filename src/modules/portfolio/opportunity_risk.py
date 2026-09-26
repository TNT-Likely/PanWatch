"""只读展示层集中度调整；不修改持久化评分、信号动作或交易执行。"""
import logging
from threading import Lock
from time import monotonic

from src.platform.persistence.database import SessionLocal
from src.platform.persistence.models import Account, Position, Stock
from src.modules.portfolio.holdings import gather_holdings
from src.modules.portfolio.portfolio_diagnostics import diagnose_positions, MAX_MARKET_WEIGHT

logger = logging.getLogger(__name__)
_lock = Lock()
_cache = None
CACHE_TTL = 30.0


def current_concentration():
    """单槽缓存限制内存；持仓/成本/市场/启用状态变动即失效。"""
    global _cache
    with _lock, SessionLocal() as db:
        rows = db.query(Position.id, Position.quantity, Position.cost_price,
                        Stock.symbol, Stock.market).select_from(Position).join(Account, Account.id == Position.account_id).join(
            Stock, Stock.id == Position.stock_id).filter(Account.enabled.is_(True)).order_by(Position.id).all()
        key = (id(db.get_bind()), tuple(tuple(row) for row in rows))
        now = monotonic()
        if _cache and _cache[0] == key and now - _cache[1] < CACHE_TTL:
            return _cache[2]
        diag = diagnose_positions(gather_holdings(db))
        _cache = (key, monotonic(), diag)
        return diag


def apply_concentration(items: list[dict], diag: dict) -> list[dict]:
    """保留输入顺序及全部候选；分数表示展示排序参考，不是交易指令。"""
    total = float(diag.get('total_market_value') or 0)
    markets = diag.get('by_market') or {}
    result = []
    for item in items:
        row = dict(item)
        raw = float(row.get('raw_score', row.get('score')) or 0)
        weight = float(markets.get(row.get('stock_market')) or 0) / total if total > 0 else 0
        # 卖出/减仓信号不会扩大敞口，不应被当作建仓机会降权。
        flag = weight >= MAX_MARKET_WEIGHT and row.get('action') not in ('sell', 'reduce')
        row.update(raw_score=raw, concentration_flag=flag,
                   concentration_note=(f"该市场占已投资金额 {weight:.0%}，建仓会进一步推高集中度；展示分按原分七折" if flag else ''),
                   score=round(raw * 0.7, 2) if flag else raw)
        if 'rank_score' in row:
            rank = float(row.get('raw_rank_score', row['rank_score']) or 0)
            row.update(raw_rank_score=rank, rank_score=round(rank * 0.7, 2) if flag else rank)
        result.append(row)
    return result


def risk_adjusted_opportunities(items: list[dict]) -> list[dict]:
    if not items:
        return items
    try:
        return apply_concentration(items, current_concentration())
    except Exception:
        logger.exception('组合集中度暂不可用，保留原始候选分数')
        return [dict(row, concentration_note='集中度暂不可用，当前显示原始分数') for row in items]
