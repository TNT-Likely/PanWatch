"""账户和持仓管理 API"""
import logging
import time
import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field

from datetime import datetime, timedelta, timezone

from src.web.database import get_db
from src.web.models import Account, Position, PositionTrade, PriceAlertRule, Stock
from src.core.timezone import to_utc
from src.core.position_daily_pnl import TradeLot, compute_position_daily_pnl
from src.core.position_trades_context import (
    day_start_qty_from_today_trades,
    fetch_today_trades_by_position_ids,
)
from src.collectors.akshare_collector import _tencent_symbol, _fetch_tencent_quotes
from src.collectors.market_http import TTLCache
from src.models.market import MarketCode

logger = logging.getLogger(__name__)
router = APIRouter()

# 汇率缓存
_hkd_rate_cache: dict = {"rate": 0.92, "ts": 0}  # 港币默认汇率 0.92
_usd_rate_cache: dict = {"rate": 7.25, "ts": 0}  # 美元默认汇率 7.25
EXCHANGE_RATE_TTL = 3600  # 1 小时缓存


def get_hkd_cny_rate() -> float:
    """获取港币兑人民币汇率"""
    global _hkd_rate_cache

    # 检查缓存
    if time.time() - _hkd_rate_cache["ts"] < EXCHANGE_RATE_TTL:
        return _hkd_rate_cache["rate"]

    # 从新浪财经获取汇率
    try:
        resp = httpx.get(
            "https://hq.sinajs.cn/list=fx_shkdcny",
            timeout=5,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://finance.sina.com.cn/"
            }
        )
        # 格式: var hq_str_fx_shkdcny="时间,汇率,..."
        text = resp.text
        if "=" in text and "," in text:
            data = text.split('"')[1]
            parts = data.split(",")
            if len(parts) > 1:
                rate = float(parts[1])
                _hkd_rate_cache = {"rate": rate, "ts": time.time()}
                logger.info(f"更新港币汇率: {rate}")
                return rate
    except Exception as e:
        logger.warning(f"获取港币汇率失败，使用缓存: {e}")

    return _hkd_rate_cache["rate"]


def get_usd_cny_rate() -> float:
    """获取美元兑人民币汇率"""
    global _usd_rate_cache

    # 检查缓存
    if time.time() - _usd_rate_cache["ts"] < EXCHANGE_RATE_TTL:
        return _usd_rate_cache["rate"]

    # 从新浪财经获取汇率
    try:
        resp = httpx.get(
            "https://hq.sinajs.cn/list=fx_susdcny",
            timeout=5,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Referer": "https://finance.sina.com.cn/"
            }
        )
        # 格式: var hq_str_fx_susdcny="时间,汇率,..."
        text = resp.text
        if "=" in text and "," in text:
            data = text.split('"')[1]
            parts = data.split(",")
            if len(parts) > 1:
                rate = float(parts[1])
                _usd_rate_cache = {"rate": rate, "ts": time.time()}
                logger.info(f"更新美元汇率: {rate}")
                return rate
    except Exception as e:
        logger.warning(f"获取美元汇率失败，使用缓存: {e}")

    return _usd_rate_cache["rate"]


# ========== Pydantic Models ==========

class AccountCreate(BaseModel):
    name: str
    available_funds: float = 0


class AccountUpdate(BaseModel):
    name: str | None = None
    available_funds: float | None = None
    enabled: bool | None = None


class AccountResponse(BaseModel):
    id: int
    name: str
    available_funds: float
    enabled: bool

    class Config:
        from_attributes = True


class PositionCreate(BaseModel):
    account_id: int
    stock_id: int
    cost_price: float
    quantity: int
    invested_amount: float | None = None
    trading_style: str | None = None  # short: 短线, swing: 波段, long: 长线
    traded_at: datetime | None = Field(
        default=None,
        description="建仓成交时间；补录历史持仓时可填实际买入时间",
    )


class PositionUpdate(BaseModel):
    cost_price: float | None = None
    quantity: int | None = None
    invested_amount: float | None = None
    trading_style: str | None = None
    trade_price: float | None = Field(
        default=None,
        gt=0,
        description="手动调整股数时用于记录流水的成交价",
    )
    trade_note: str | None = None
    traded_at: datetime | None = Field(
        default=None,
        description="手动调整股数时用于记录流水的成交时间",
    )


class PositionResponse(BaseModel):
    id: int
    account_id: int
    stock_id: int
    cost_price: float
    quantity: int
    invested_amount: float | None
    sort_order: int
    trading_style: str | None
    # 关联信息
    account_name: str | None = None
    stock_symbol: str | None = None
    stock_name: str | None = None

    class Config:
        from_attributes = True


class PositionReorderItem(BaseModel):
    id: int
    sort_order: int


class PositionReorderRequest(BaseModel):
    items: list[PositionReorderItem]


class PositionAddRequest(BaseModel):
    price: float = Field(gt=0, description="买入单价")
    quantity: int = Field(gt=0, description="买入股数")
    note: str | None = None
    traded_at: datetime | None = None


class PositionReduceRequest(BaseModel):
    price: float = Field(gt=0, description="卖出单价")
    quantity: int = Field(gt=0, description="卖出股数")
    note: str | None = None
    traded_at: datetime | None = None


class PositionTradeResponse(BaseModel):
    id: int
    position_id: int
    side: str
    price: float
    quantity: int
    amount: float
    cost_before: float | None
    qty_before: int | None
    cost_after: float | None
    qty_after: int | None
    note: str | None
    traded_at: datetime | None
    created_at: datetime | None

    class Config:
        from_attributes = True


def _calc_weighted_cost(
    cur_qty: int, cur_cost: float, add_qty: int, add_price: float
) -> tuple[int, float]:
    """加权平均成本: (原持仓成本 + 新买入成本) / 总股数"""
    new_qty = int(cur_qty) + int(add_qty)
    if new_qty <= 0:
        raise ValueError("加仓后股数必须大于 0")
    if cur_qty <= 0 or cur_cost <= 0:
        return new_qty, float(add_price)
    total_cost = cur_qty * cur_cost + add_qty * add_price
    return new_qty, round(total_cost / new_qty, 6)


def _normalize_traded_at(dt: datetime | None) -> datetime:
    """统一成交时间为 UTC naive，供数据库存储。"""
    if dt is None:
        return datetime.now(timezone.utc).replace(tzinfo=None)
    return to_utc(dt).replace(tzinfo=None)


def _serialize_position_trade(trade: PositionTrade) -> dict:
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
        "traded_at": trade.traded_at,
        "created_at": trade.created_at,
    }


# ========== Account Endpoints ==========

@router.get("/accounts", response_model=list[AccountResponse])
def list_accounts(db: Session = Depends(get_db)):
    """获取所有账户"""
    return db.query(Account).order_by(Account.id).all()


@router.get("/accounts/{account_id}", response_model=AccountResponse)
def get_account(account_id: int, db: Session = Depends(get_db)):
    """获取单个账户"""
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise HTTPException(404, "账户不存在")
    return account


@router.post("/accounts", response_model=AccountResponse)
def create_account(data: AccountCreate, db: Session = Depends(get_db)):
    """创建账户"""
    account = Account(name=data.name, available_funds=data.available_funds)
    db.add(account)
    db.commit()
    db.refresh(account)
    logger.info(f"创建账户: {account.name}")
    return account


@router.put("/accounts/{account_id}", response_model=AccountResponse)
def update_account(account_id: int, data: AccountUpdate, db: Session = Depends(get_db)):
    """更新账户"""
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise HTTPException(404, "账户不存在")

    if data.name is not None:
        account.name = data.name
    if data.available_funds is not None:
        account.available_funds = data.available_funds
    if data.enabled is not None:
        account.enabled = data.enabled

    db.commit()
    db.refresh(account)
    logger.info(f"更新账户: {account.name}")
    return account


@router.delete("/accounts/{account_id}")
def delete_account(account_id: int, db: Session = Depends(get_db)):
    """删除账户（会同时删除该账户的所有持仓）"""
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise HTTPException(404, "账户不存在")

    db.delete(account)
    db.commit()
    logger.info(f"删除账户: {account.name}")
    return {"success": True}


# ========== Position Endpoints ==========

@router.get("/positions", response_model=list[PositionResponse])
def list_positions(
    account_id: int | None = None,
    stock_id: int | None = None,
    db: Session = Depends(get_db)
):
    """获取持仓列表，可按账户或股票筛选"""
    query = db.query(Position)
    if account_id:
        query = query.filter(Position.account_id == account_id)
    if stock_id:
        query = query.filter(Position.stock_id == stock_id)

    positions = query.order_by(Position.account_id.asc(), Position.sort_order.asc(), Position.id.asc()).all()
    result = []
    for pos in positions:
        result.append({
            "id": pos.id,
            "account_id": pos.account_id,
            "stock_id": pos.stock_id,
            "cost_price": pos.cost_price,
            "quantity": pos.quantity,
            "invested_amount": pos.invested_amount,
            "sort_order": pos.sort_order or 0,
            "trading_style": pos.trading_style,
            "account_name": pos.account.name if pos.account else None,
            "stock_symbol": pos.stock.symbol if pos.stock else None,
            "stock_name": pos.stock.name if pos.stock else None,
        })
    return result


@router.post("/positions", response_model=PositionResponse)
def create_position(data: PositionCreate, db: Session = Depends(get_db)):
    """创建持仓"""
    # 检查账户和股票是否存在
    account = db.query(Account).filter(Account.id == data.account_id).first()
    if not account:
        raise HTTPException(400, "账户不存在")

    stock = db.query(Stock).filter(Stock.id == data.stock_id).first()
    if not stock:
        raise HTTPException(400, "股票不存在")

    # 检查是否已存在该账户的该股票持仓
    existing = db.query(Position).filter(
        Position.account_id == data.account_id,
        Position.stock_id == data.stock_id,
    ).first()
    if existing:
        raise HTTPException(400, f"账户 {account.name} 已有 {stock.name} 的持仓，请编辑现有持仓")

    max_order = db.query(func.max(Position.sort_order)).filter(
        Position.account_id == data.account_id
    ).scalar() or 0

    position = Position(
        account_id=data.account_id,
        stock_id=data.stock_id,
        cost_price=data.cost_price,
        quantity=data.quantity,
        invested_amount=data.invested_amount,
        sort_order=int(max_order) + 1,
        trading_style=data.trading_style,
    )
    db.add(position)
    db.flush()

    qty = int(data.quantity)
    cost = float(data.cost_price)
    traded_at = _normalize_traded_at(data.traded_at)
    trade = PositionTrade(
        position_id=position.id,
        side="buy",
        price=cost,
        quantity=qty,
        amount=round(cost * qty, 4),
        cost_before=None,
        qty_before=None,
        cost_after=cost,
        qty_after=qty,
        note="建仓",
        traded_at=traded_at,
    )
    db.add(trade)
    db.commit()
    db.refresh(position)

    logger.info(f"创建持仓: {account.name} - {stock.name}")
    return {
        "id": position.id,
        "account_id": position.account_id,
        "stock_id": position.stock_id,
        "cost_price": position.cost_price,
        "quantity": position.quantity,
        "invested_amount": position.invested_amount,
        "sort_order": position.sort_order or 0,
        "trading_style": position.trading_style,
        "account_name": account.name,
        "stock_symbol": stock.symbol,
        "stock_name": stock.name,
    }


@router.put("/positions/{position_id}", response_model=PositionResponse)
def update_position(position_id: int, data: PositionUpdate, db: Session = Depends(get_db)):
    """更新持仓；若股数变化且提供 trade_price，则自动写入买卖流水"""
    position = db.query(Position).filter(Position.id == position_id).first()
    if not position:
        raise HTTPException(404, "持仓不存在")

    cost_before = float(position.cost_price)
    qty_before = int(position.quantity)
    new_qty = int(data.quantity) if data.quantity is not None else qty_before
    new_cost = float(data.cost_price) if data.cost_price is not None else cost_before

    if data.quantity is not None and new_qty != qty_before and data.trade_price:
        qty_diff = new_qty - qty_before
        side = "buy" if qty_diff > 0 else "sell"
        trade_qty = abs(qty_diff)
        trade_price = float(data.trade_price)
        traded_at = _normalize_traded_at(data.traded_at)
        if side == "sell":
            if trade_qty > qty_before:
                raise HTTPException(400, "卖出股数超过持仓")
            after_qty = qty_before - trade_qty
            after_cost = cost_before
        else:
            after_qty, after_cost = _calc_weighted_cost(
                qty_before, cost_before, trade_qty, trade_price
            )
        trade = PositionTrade(
            position_id=position.id,
            side=side,
            price=trade_price,
            quantity=trade_qty,
            amount=round(trade_price * trade_qty, 4),
            cost_before=cost_before,
            qty_before=qty_before,
            cost_after=after_cost,
            qty_after=after_qty,
            note=(data.trade_note or "").strip() or "手动调整持仓",
            traded_at=traded_at,
        )
        db.add(trade)
        new_qty = after_qty
        new_cost = after_cost

    if data.cost_price is not None:
        position.cost_price = new_cost
    if data.quantity is not None:
        position.quantity = new_qty
    if data.invested_amount is not None:
        position.invested_amount = data.invested_amount
    elif data.quantity is not None or data.cost_price is not None:
        position.invested_amount = round(new_cost * new_qty, 4)
    if data.trading_style is not None:
        # 空字符串表示清空，设为 None
        position.trading_style = data.trading_style if data.trading_style else None

    db.commit()
    db.refresh(position)

    logger.info(f"更新持仓: {position.account.name} - {position.stock.name}")
    return {
        "id": position.id,
        "account_id": position.account_id,
        "stock_id": position.stock_id,
        "cost_price": position.cost_price,
        "quantity": position.quantity,
        "invested_amount": position.invested_amount,
        "sort_order": position.sort_order or 0,
        "trading_style": position.trading_style,
        "account_name": position.account.name,
        "stock_symbol": position.stock.symbol,
        "stock_name": position.stock.name,
    }


@router.post("/positions/{position_id}/add")
def add_to_position(
    position_id: int, data: PositionAddRequest, db: Session = Depends(get_db)
):
    """加仓:记录买入流水并按加权平均更新持仓成本与股数"""
    position = db.query(Position).filter(Position.id == position_id).first()
    if not position:
        raise HTTPException(404, "持仓不存在")

    add_qty = int(data.quantity)
    add_price = float(data.price)
    cost_before = float(position.cost_price)
    qty_before = int(position.quantity)

    try:
        new_qty, new_cost = _calc_weighted_cost(
            qty_before, cost_before, add_qty, add_price
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e

    add_amount = round(add_price * add_qty, 4)
    traded_at = _normalize_traded_at(data.traded_at)

    trade = PositionTrade(
        position_id=position.id,
        side="buy",
        price=add_price,
        quantity=add_qty,
        amount=add_amount,
        cost_before=cost_before,
        qty_before=qty_before,
        cost_after=new_cost,
        qty_after=new_qty,
        note=(data.note or "").strip() or None,
        traded_at=traded_at,
    )
    db.add(trade)

    position.quantity = new_qty
    position.cost_price = new_cost
    if position.invested_amount is not None:
        position.invested_amount = round(float(position.invested_amount) + add_amount, 4)
    else:
        position.invested_amount = round(new_cost * new_qty, 4)

    db.commit()
    db.refresh(position)
    db.refresh(trade)

    logger.info(
        "加仓: %s - %s +%d@%s → 成本 %.4f, 股数 %d",
        position.account.name,
        position.stock.name,
        add_qty,
        add_price,
        new_cost,
        new_qty,
    )
    return {
        "position": {
            "id": position.id,
            "account_id": position.account_id,
            "stock_id": position.stock_id,
            "cost_price": position.cost_price,
            "quantity": position.quantity,
            "invested_amount": position.invested_amount,
            "sort_order": position.sort_order or 0,
            "trading_style": position.trading_style,
            "account_name": position.account.name,
            "stock_symbol": position.stock.symbol,
            "stock_name": position.stock.name,
        },
        "trade": _serialize_position_trade(trade),
    }


@router.post("/positions/{position_id}/reduce")
def reduce_from_position(
    position_id: int, data: PositionReduceRequest, db: Session = Depends(get_db)
):
    """减仓/卖出:记录流水并更新持仓股数(成本单价不变)"""
    position = db.query(Position).filter(Position.id == position_id).first()
    if not position:
        raise HTTPException(404, "持仓不存在")

    sell_qty = int(data.quantity)
    sell_price = float(data.price)
    cost_before = float(position.cost_price)
    qty_before = int(position.quantity)

    if sell_qty > qty_before:
        raise HTTPException(400, "卖出股数超过持仓")

    new_qty = qty_before - sell_qty
    sell_amount = round(sell_price * sell_qty, 4)
    traded_at = _normalize_traded_at(data.traded_at)

    trade = PositionTrade(
        position_id=position.id,
        side="sell",
        price=sell_price,
        quantity=sell_qty,
        amount=sell_amount,
        cost_before=cost_before,
        qty_before=qty_before,
        cost_after=cost_before,
        qty_after=new_qty,
        note=(data.note or "").strip() or None,
        traded_at=traded_at,
    )
    db.add(trade)

    position.quantity = new_qty
    if position.invested_amount is not None:
        position.invested_amount = round(cost_before * new_qty, 4)
    else:
        position.invested_amount = round(cost_before * new_qty, 4)

    db.commit()
    db.refresh(position)
    db.refresh(trade)

    logger.info(
        "减仓: %s - %s -%d@%s → 成本 %.4f, 股数 %d",
        position.account.name,
        position.stock.name,
        sell_qty,
        sell_price,
        cost_before,
        new_qty,
    )
    return {
        "position": {
            "id": position.id,
            "account_id": position.account_id,
            "stock_id": position.stock_id,
            "cost_price": position.cost_price,
            "quantity": position.quantity,
            "invested_amount": position.invested_amount,
            "sort_order": position.sort_order or 0,
            "trading_style": position.trading_style,
            "account_name": position.account.name,
            "stock_symbol": position.stock.symbol,
            "stock_name": position.stock.name,
        },
        "trade": _serialize_position_trade(trade),
    }


@router.get("/positions/{position_id}/trades")
def list_position_trades(
    position_id: int,
    limit: int = 20,
    db: Session = Depends(get_db),
):
    """获取持仓变动流水(最近 N 条)"""
    position = db.query(Position).filter(Position.id == position_id).first()
    if not position:
        raise HTTPException(404, "持仓不存在")

    lim = max(1, min(int(limit), 100))
    trades = (
        db.query(PositionTrade)
        .filter(PositionTrade.position_id == position_id)
        .order_by(PositionTrade.traded_at.desc(), PositionTrade.id.desc())
        .limit(lim)
        .all()
    )
    return [_serialize_position_trade(t) for t in trades]


@router.get("/portfolio/recent-trades")
def recent_portfolio_trades(limit: int = 50, db: Session = Depends(get_db)):
    """全账户最近持仓变动流水(加仓等)"""
    lim = max(1, min(int(limit), 200))
    rows = (
        db.query(PositionTrade, Position, Stock, Account)
        .join(Position, PositionTrade.position_id == Position.id)
        .join(Stock, Position.stock_id == Stock.id)
        .join(Account, Position.account_id == Account.id)
        .filter(Account.enabled == True)  # noqa: E712
        .order_by(PositionTrade.traded_at.desc(), PositionTrade.id.desc())
        .limit(lim)
        .all()
    )
    out: list[dict] = []
    for trade, _pos, stock, acc in rows:
        item = _serialize_position_trade(trade)
        item["account_name"] = acc.name
        item["symbol"] = stock.symbol
        item["market"] = stock.market
        item["stock_name"] = stock.name
        out.append(item)
    return out


@router.delete("/positions/{position_id}")
def delete_position(position_id: int, db: Session = Depends(get_db)):
    """删除持仓"""
    position = db.query(Position).filter(Position.id == position_id).first()
    if not position:
        raise HTTPException(404, "持仓不存在")

    db.delete(position)
    db.commit()
    logger.info(f"删除持仓: {position.account.name} - {position.stock.name}")
    return {"success": True}


@router.put("/positions/reorder/batch")
def reorder_positions(data: PositionReorderRequest, db: Session = Depends(get_db)):
    """批量更新持仓排序"""
    if not data.items:
        return {"updated": 0}
    ids = [int(x.id) for x in data.items]
    rows = db.query(Position).filter(Position.id.in_(ids)).all()
    row_map = {r.id: r for r in rows}
    updated = 0
    for item in data.items:
        row = row_map.get(int(item.id))
        if not row:
            continue
        row.sort_order = int(item.sort_order)
        updated += 1
    db.commit()
    return {"updated": updated}


# ========== Portfolio Summary ==========

@router.get("/portfolio/summary")
def get_portfolio_summary(
    account_id: int | None = None,
    include_quotes: bool = True,
    db: Session = Depends(get_db),
):
    """
    获取持仓汇总信息

    Args:
        account_id: 可选，指定账户ID。不指定则汇总所有账户

    Returns:
        accounts: 账户列表及各账户持仓明细
        total: 所有账户汇总
    """
    # 获取账户
    if account_id:
        accounts = db.query(Account).filter(Account.id == account_id, Account.enabled == True).all()
    else:
        accounts = db.query(Account).filter(Account.enabled == True).all()

    if not accounts:
        return {
            "accounts": [],
            "total": {
                "total_market_value": 0,
                "total_cost": 0,
                "total_pnl": 0,
                "total_pnl_pct": 0,
                "available_funds": 0,
                "total_assets": 0,
            }
        }

    # 获取所有相关股票
    all_stock_ids = set()
    all_position_ids: list[int] = []
    for acc in accounts:
        for pos in acc.positions:
            all_stock_ids.add(pos.stock_id)
            all_position_ids.append(pos.id)

    stocks = db.query(Stock).filter(Stock.id.in_(all_stock_ids)).all() if all_stock_ids else []
    stock_map = {s.id: s for s in stocks}
    today_trades_by_position = fetch_today_trades_by_position_ids(db, all_position_ids)

    # 获取实时行情（可选）
    quotes = _fetch_quotes_for_stocks(stocks) if include_quotes else {}

    # 获取汇率
    hkd_rate = get_hkd_cny_rate()
    usd_rate = get_usd_cny_rate()

    # 计算各账户持仓
    account_summaries = []
    grand_total_market_value = 0
    grand_total_cost = 0
    grand_available_funds = 0
    grand_daily_pnl = 0

    for acc in accounts:
        positions_data = []
        acc_market_value = 0
        acc_cost = 0
        acc_daily_pnl = 0

        positions_sorted = sorted(
            list(acc.positions or []),
            key=lambda p: (int(getattr(p, "sort_order", 0) or 0), int(p.id)),
        )
        for pos in positions_sorted:
            stock = stock_map.get(pos.stock_id)
            if not stock:
                continue

            quote = quotes.get(stock.symbol)
            current_price = quote["current_price"] if quote else None
            change_pct = quote["change_pct"] if quote else None
            prev_close = quote.get("prev_close") if quote else None

            # 根据市场确定汇率
            is_foreign = stock.market in ("HK", "US")
            if stock.market == "HK":
                rate = hkd_rate
            elif stock.market == "US":
                rate = usd_rate
            else:
                rate = 1.0

            market_value = None
            market_value_cny = None
            pnl = None
            pnl_pct = None
            daily_pnl = None
            daily_pnl_pct = None

            today_rows = today_trades_by_position.get(pos.id, [])
            day_start_qty = day_start_qty_from_today_trades(today_rows, int(pos.quantity or 0))
            today_trade_lots = [
                TradeLot(side=str(t.side), quantity=int(t.quantity), price=float(t.price))
                for t in today_rows
            ]
            today_trades_payload = [
                {"side": t.side, "quantity": int(t.quantity), "price": float(t.price)}
                for t in today_rows
            ]

            if current_price is not None:
                daily_pnl, daily_pnl_pct = compute_position_daily_pnl(
                    current_price=float(current_price),
                    quantity=int(pos.quantity or 0),
                    prev_close=float(prev_close) if prev_close else None,
                    today_trades=today_trade_lots,
                    day_start_qty=day_start_qty,
                )
                if daily_pnl is not None:
                    acc_daily_pnl += daily_pnl * rate

            cost = pos.cost_price * pos.quantity
            cost_cny = cost * rate  # 假设成本价也是原币种
            acc_cost += cost_cny

            if current_price is not None:
                market_value = current_price * pos.quantity  # 原币种市值
                market_value_cny = market_value * rate  # 人民币市值
                pnl = market_value_cny - cost_cny
                pnl_pct = (pnl / cost_cny * 100) if cost_cny > 0 else 0

                acc_market_value += market_value_cny

            positions_data.append({
                "id": pos.id,
                "stock_id": pos.stock_id,
                "symbol": stock.symbol,
                "name": stock.name,
                "market": stock.market,
                "account_name": acc.name,
                "cost_price": pos.cost_price,
                "quantity": pos.quantity,
                "invested_amount": pos.invested_amount,
                "sort_order": pos.sort_order or 0,
                "trading_style": pos.trading_style,
                "current_price": current_price,
                "current_price_cny": round(current_price * rate, 2) if current_price else None,
                "change_pct": change_pct,
                "market_value": round(market_value, 2) if market_value else None,
                "market_value_cny": round(market_value_cny, 2) if market_value_cny else None,
                "pnl": round(pnl, 2) if pnl else None,
                "pnl_pct": round(pnl_pct, 2) if pnl_pct else None,
                "daily_pnl": round(daily_pnl * rate, 2) if daily_pnl is not None else None,
                "daily_pnl_pct": daily_pnl_pct,
                "day_start_qty": day_start_qty,
                "today_trades": today_trades_payload,
                "exchange_rate": rate if is_foreign else None,
            })

        if include_quotes:
            acc_pnl = acc_market_value - acc_cost
            acc_pnl_pct = (acc_pnl / acc_cost * 100) if acc_cost > 0 else 0
            acc_total_assets = acc_market_value + acc.available_funds
        else:
            acc_pnl = 0
            acc_pnl_pct = 0
            acc_total_assets = acc.available_funds

        account_summaries.append({
            "id": acc.id,
            "name": acc.name,
            "available_funds": acc.available_funds,
            "total_market_value": round(acc_market_value, 2),
            "total_cost": round(acc_cost, 2),
            "total_pnl": round(acc_pnl, 2),
            "total_pnl_pct": round(acc_pnl_pct, 2),
            "total_daily_pnl": round(acc_daily_pnl, 2),
            "total_assets": round(acc_total_assets, 2),
            "positions": positions_data,
        })

        grand_total_market_value += acc_market_value
        grand_total_cost += acc_cost
        grand_available_funds += acc.available_funds
        grand_daily_pnl += acc_daily_pnl

    if include_quotes:
        grand_pnl = grand_total_market_value - grand_total_cost
        grand_pnl_pct = (grand_pnl / grand_total_cost * 100) if grand_total_cost > 0 else 0
        grand_total_assets = grand_total_market_value + grand_available_funds
    else:
        grand_pnl = 0
        grand_pnl_pct = 0
        grand_total_assets = grand_available_funds

    # 构建 quotes 字典（用于前端股票列表显示）
    quotes_dict = {}
    if include_quotes:
        for symbol, quote in quotes.items():
            quotes_dict[symbol] = {
                "current_price": quote.get("current_price"),
                "change_pct": quote.get("change_pct"),
            }

    return {
        "accounts": account_summaries,
        "total": {
            "total_market_value": round(grand_total_market_value, 2),
            "total_cost": round(grand_total_cost, 2),
            "total_pnl": round(grand_pnl, 2),
            "total_pnl_pct": round(grand_pnl_pct, 2),
            "total_daily_pnl": round(grand_daily_pnl, 2),
            "available_funds": round(grand_available_funds, 2),
            "total_assets": round(grand_total_assets, 2),
        },
        "exchange_rates": {
            "HKD_CNY": hkd_rate,
            "USD_CNY": usd_rate,
        },
        "quotes": quotes_dict,  # 可选：返回行情数据
    }


def _fetch_quotes_for_stocks(stocks: list[Stock]) -> dict:
    """获取股票列表的实时行情"""
    if not stocks:
        return {}

    # 按市场分组
    market_stocks: dict[str, list[Stock]] = {}
    for s in stocks:
        market_stocks.setdefault(s.market, []).append(s)

    quotes = {}
    for market, stock_list in market_stocks.items():
        try:
            market_code = MarketCode(market)
        except ValueError:
            continue

        symbols = [_tencent_symbol(s.symbol, market_code) for s in stock_list]
        try:
            items = _fetch_tencent_quotes(symbols)
            for item in items:
                quotes[item["symbol"]] = item
        except Exception as e:
            logger.error(f"获取 {market} 行情失败: {e}")

    return quotes


# 组合基准/归因结果缓存:重建全持仓 NAV 很贵(逐只拉 K 线),按持仓指纹缓存结果。
# 持仓变动即失效(指纹变);失败/空结果不缓存,避免把瞬时故障冻住 10 分钟。
_PORTFOLIO_RESULT_CACHE = TTLCache(default_ttl_sec=600.0)


def _holdings_signature(db: Session) -> str:
    """启用账户持仓的稳定指纹(stock_id + 合并后数量);仅查 DB,不拉行情/K 线。"""
    rows = (
        db.query(Position.stock_id, Position.quantity)
        .join(Account, Account.id == Position.account_id)
        .filter(Account.enabled == True)  # noqa: E712
        .all()
    )
    agg: dict[int, float] = {}
    for sid, qty in rows:
        agg[sid] = agg.get(sid, 0.0) + (qty or 0)
    return ";".join(f"{sid}:{agg[sid]:g}" for sid in sorted(agg))


def _gather_holdings(db: Session) -> list[dict]:
    """汇总所有启用账户的真实持仓为统一列表(CNY 市值/浮盈 + fx),多账户同股合并。"""
    accounts = db.query(Account).filter(Account.enabled == True).all()  # noqa: E712
    stock_ids = {p.stock_id for acc in accounts for p in acc.positions}
    stocks = db.query(Stock).filter(Stock.id.in_(stock_ids)).all() if stock_ids else []
    stock_map = {s.id: s for s in stocks}
    quotes = _fetch_quotes_for_stocks(stocks) if stocks else {}
    hkd, usd = get_hkd_cny_rate(), get_usd_cny_rate()

    out: list[dict] = []
    seen: dict[tuple[str, str], dict] = {}
    for acc in accounts:
        for pos in acc.positions:
            stock = stock_map.get(pos.stock_id)
            if not stock:
                continue
            rate = hkd if stock.market == "HK" else usd if stock.market == "US" else 1.0
            quote = quotes.get(stock.symbol)
            price = quote.get("current_price") if quote else None
            cost_cny = pos.cost_price * pos.quantity * rate
            mv_cny = (price * pos.quantity * rate) if price else cost_cny
            pnl_cny = (mv_cny - cost_cny) if price else 0.0
            key = (stock.market, stock.symbol)
            if key in seen:  # 多账户同一标的合并
                h = seen[key]
                h["quantity"] += pos.quantity
                h["market_value"] += mv_cny
                h["unrealized_pnl"] += pnl_cny
            else:
                h = {
                    "symbol": stock.symbol,
                    "market": stock.market,
                    "name": stock.name,
                    "quantity": pos.quantity,
                    "fx": rate,
                    "market_value": mv_cny,
                    "unrealized_pnl": pnl_cny,
                    "strategy_code": pos.trading_style or "",
                }
                seen[key] = h
                out.append(h)
    return out


@router.get("/portfolio/diagnostics")
def portfolio_diagnostics(db: Session = Depends(get_db)):
    """真实持仓组合诊断:集中度(HHI)/最大单仓/市场分布/风险提示(只读)。"""
    from src.core.portfolio_diagnostics import diagnose_positions

    return diagnose_positions(_gather_holdings(db))


@router.get("/portfolio/benchmark")
def portfolio_benchmark(
    days: int = 60, benchmark: str = "000300", db: Session = Depends(get_db)
):
    """真实持仓组合 vs 基准:超额收益/信息比率/相对回撤 + 归一化净值曲线。"""
    from src.core.portfolio_benchmark import (
        DEFAULT_BENCHMARK,
        build_portfolio_benchmark,
    )

    days = max(20, min(int(days), 250))
    bcode = benchmark or DEFAULT_BENCHMARK
    sig = _holdings_signature(db)
    if not sig:
        return {"empty": True, "reason": "no_holdings"}
    ckey = f"bench:{days}:{bcode}:{sig}"
    cached = _PORTFOLIO_RESULT_CACHE.get(ckey)
    if cached is not None:
        return cached

    holdings = _gather_holdings(db)
    if not holdings:
        return {"empty": True, "reason": "no_holdings"}
    res = build_portfolio_benchmark(holdings, days=days, benchmark_code=bcode)
    if not res:
        # 失败/数据不足不缓存,下轮可重试(由 K 线负缓存兜住打爆)
        return {"empty": True, "reason": "insufficient_data"}
    _PORTFOLIO_RESULT_CACHE.set(ckey, res)
    return res


@router.get("/portfolio/todos")
def portfolio_todos(db: Session = Depends(get_db)):
    """首页空态待办:持仓但未设提醒 / 提醒即将到期(可行动,盘后也不空)。"""
    todos: list[dict] = []
    accounts = db.query(Account).filter(Account.enabled == True).all()  # noqa: E712
    held_ids = {p.stock_id for acc in accounts for p in acc.positions}
    if held_ids:
        ruled = {
            r.stock_id
            for r in db.query(PriceAlertRule)
            .filter(PriceAlertRule.enabled == True, PriceAlertRule.stock_id.in_(held_ids))  # noqa: E712
            .all()
        }
        for sid in held_ids - ruled:
            stock = db.query(Stock).filter(Stock.id == sid).first()
            if stock:
                todos.append(
                    {
                        "type": "no_alert",
                        "symbol": stock.symbol,
                        "market": stock.market,
                        "message": f"{stock.name} 持仓中,未设价格提醒",
                    }
                )

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    soon = now + timedelta(days=3)
    expiring = (
        db.query(PriceAlertRule)
        .filter(
            PriceAlertRule.enabled == True,  # noqa: E712
            PriceAlertRule.expire_at.isnot(None),
            PriceAlertRule.expire_at >= now,
            PriceAlertRule.expire_at <= soon,
        )
        .all()
    )
    for r in expiring:
        stock = db.query(Stock).filter(Stock.id == r.stock_id).first()
        todos.append(
            {
                "type": "alert_expiring",
                "symbol": stock.symbol if stock else "",
                "market": stock.market if stock else "CN",
                "message": f"{(r.name or '提醒')} 即将到期",
            }
        )

    return {"todos": todos[:10], "count": len(todos)}


@router.get("/portfolio/attribution")
def portfolio_attribution(days: int = 60, benchmark: str = "000300", db: Session = Depends(get_db)):
    """近 days 日各持仓对组合收益的贡献(谁拖累/贡献),降序。"""
    from src.core.portfolio_benchmark import DEFAULT_BENCHMARK, build_attribution

    days = max(20, min(int(days), 250))
    bcode = benchmark or DEFAULT_BENCHMARK
    sig = _holdings_signature(db)
    if not sig:
        return {"items": []}
    ckey = f"attr:{days}:{bcode}:{sig}"
    cached = _PORTFOLIO_RESULT_CACHE.get(ckey)
    if cached is not None:
        return cached

    holdings = _gather_holdings(db)
    if not holdings:
        return {"items": []}
    items = build_attribution(holdings, days=days, benchmark_code=bcode)
    result = {"items": items}
    if items:  # 空结果不缓存,下轮可重试
        _PORTFOLIO_RESULT_CACHE.set(ckey, result)
    return result


@router.post("/portfolio/ai-review")
async def portfolio_ai_review(model_id: int | None = None, db: Session = Depends(get_db)):
    """组合 AI 体检:诊断+基准+归因 → 叙述结论 + 调仓建议(只读,不下单)。"""
    from src.core.portfolio_benchmark import build_attribution, build_portfolio_benchmark
    from src.core.portfolio_diagnostics import diagnose_positions
    from src.web.api.chat import _get_ai_client

    holdings = _gather_holdings(db)
    if not holdings:
        return {"empty": True, "reason": "no_holdings"}

    diag = diagnose_positions(holdings)
    bench = build_portfolio_benchmark(holdings, days=60) or {}
    attr = build_attribution(holdings, days=60)
    top = attr[:3]
    worst = list(reversed(attr[-3:])) if len(attr) > 3 else []

    lines = [
        f"持仓 {diag['position_count']} 只,总市值 {diag['total_market_value']:.0f},浮盈 {diag['total_unrealized_pnl']:.0f}",
        f"集中度 HHI {diag['hhi']},最大单仓 {diag['max_weight'] * 100:.0f}%",
    ]
    if bench.get("excess_return") is not None:
        lines.append(
            f"近60日 vs {bench.get('benchmark_label', '基准')}:超额 {bench['excess_return']}%"
            f"(组合 {bench.get('portfolio_return')}% / 基准 {bench.get('benchmark_return')}%),"
            f"相对回撤 {bench.get('relative_drawdown')}%"
        )
    if diag.get("by_market"):
        lines.append("市场分布:" + ", ".join(f"{k} {v:.0f}" for k, v in diag["by_market"].items()))
    if diag.get("alerts"):
        lines.append("风险提示:" + "; ".join(diag["alerts"]))
    if top:
        lines.append("贡献最大:" + ", ".join(f"{r['name']}({r['contribution_pct']:+.2f}%)" for r in top))
    if worst:
        lines.append("拖累最大:" + ", ".join(f"{r['name']}({r['contribution_pct']:+.2f}%)" for r in worst))

    system_prompt = (
        "你是稳健的组合顾问。基于给定的组合诊断/基准对比/个股归因,给一段简短体检 + 可执行调仓建议,"
        "只读分析、不下单、不承诺收益。严格格式:\n体检: 一句话总评\n建议:\n- (2~3 条具体可执行)\n风险: 一句话最大风险"
    )
    user_content = "组合概况:\n" + "\n".join(lines)
    try:
        content = await _get_ai_client(db, model_id).chat(system_prompt, user_content, temperature=0.3)
    except Exception as e:
        raise HTTPException(502, f"AI 体检失败: {e}")

    return {"content": content, "top": top, "worst": worst, "diagnostics": diag, "benchmark": bench}
