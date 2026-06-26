"""账户和持仓管理 API"""
import logging
import time
import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session
from pydantic import BaseModel, Field, field_validator

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

SUPPORTED_ACCOUNT_CURRENCIES = frozenset({"CNY", "HKD", "USD"})


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


def _normalize_currency(currency: str | None) -> str:
    cur = (currency or "CNY").upper()
    if cur not in SUPPORTED_ACCOUNT_CURRENCIES:
        raise HTTPException(400, "币种仅支持 CNY/HKD/USD")
    return cur


def _market_currency(market: str | None) -> str:
    return {"HK": "HKD", "US": "USD"}.get(market or "CN", "CNY")


def _currency_cny_rate(currency: str) -> float:
    currency = (currency or "CNY").upper()
    if currency == "HKD":
        return get_hkd_cny_rate()
    if currency == "USD":
        return get_usd_cny_rate()
    return 1.0


def _to_cny_amount(amount: float, currency: str) -> float:
    """把账户币种金额换算为人民币。"""
    return round(float(amount) * _currency_cny_rate(currency), 4)


def _convert_amount(amount: float, from_currency: str, to_currency: str) -> float:
    from_currency = (from_currency or "CNY").upper()
    to_currency = (to_currency or "CNY").upper()
    if from_currency == to_currency:
        return round(float(amount), 4)
    amount_cny = float(amount) * _currency_cny_rate(from_currency)
    to_rate = _currency_cny_rate(to_currency)
    return round(amount_cny / to_rate, 4) if to_rate else round(float(amount), 4)


def _market_amount_to_account_currency(
    amount: float,
    market: str | None,
    account_currency: str,
) -> float:
    """把股票成交金额(原币种)换算为账户币种。"""
    return _convert_amount(amount, _market_currency(market), account_currency)


def _resolve_trading_style(
    style: str | None,
    fallback: str | None = None,
) -> str:
    """归一化交易风格，未设置时默认短线。"""
    for candidate in (style, fallback, "short"):
        if candidate and candidate.strip() in ("short", "swing", "long"):
            return candidate.strip()
    return "short"


def adjust_account_stock_cash(
    account: Account,
    *,
    side: str,
    amount: float,
    market: str | None,
) -> None:
    """买入扣减 / 卖出增加股票现金（账户币种）。"""
    account_currency = str(getattr(account, "base_currency", "CNY") or "CNY")
    converted = round(
        _market_amount_to_account_currency(float(amount), market, account_currency),
        4,
    )
    current = float(account.available_funds or 0.0)
    if side == "buy":
        account.available_funds = round(current - converted, 4)
    elif side == "sell":
        account.available_funds = round(current + converted, 4)


def _normalize_other_fund_items(
    items: list | None,
    legacy_other_funds: float | None = None,
) -> list[dict]:
    """规范化其他资产分类列表。"""
    normalized: list[dict] = []
    for raw in items or []:
        if isinstance(raw, dict):
            label = str(raw.get("label") or "").strip()
            amount = float(raw.get("amount") or 0)
        elif hasattr(raw, "model_dump"):
            dumped = raw.model_dump()
            label = str(dumped.get("label") or "").strip()
            amount = float(dumped.get("amount") or 0)
        else:
            continue
        if not label:
            continue
        normalized.append({"label": label, "amount": round(amount, 4)})
    if not normalized and legacy_other_funds is not None and float(legacy_other_funds or 0) > 0:
        normalized = [{"label": "其他", "amount": round(float(legacy_other_funds), 4)}]
    return normalized


def _sum_other_fund_items(items: list | None) -> float:
    return round(sum(float(item.get("amount") or 0) for item in (items or [])), 4)


def _apply_account_other_funds(
    account: Account,
    items: list | None = None,
    legacy_other_funds: float | None = None,
) -> None:
    normalized = _normalize_other_fund_items(
        items if items is not None else getattr(account, "other_fund_items", None),
        legacy_other_funds,
    )
    account.other_fund_items = normalized
    account.other_funds = _sum_other_fund_items(normalized)


def _account_other_funds_total(account: Account) -> float:
    items = getattr(account, "other_fund_items", None)
    if items:
        return _sum_other_fund_items(items)
    return float(account.other_funds or 0)


def _account_open_cost_cny(account: Account, db: Session) -> float:
    """汇总账户在持持仓成本（人民币）。"""
    rows = (
        db.query(Position, Stock)
        .join(Stock, Position.stock_id == Stock.id)
        .filter(Position.account_id == account.id)
        .all()
    )
    total = 0.0
    for pos, stock in rows:
        if (pos.status or "open") != "open":
            continue
        rate = _currency_cny_rate(_market_currency(stock.market))
        total += float(pos.cost_price or 0) * int(pos.quantity or 0) * rate
    return round(total, 4)


def _compute_initial_funds(
    available_funds: float,
    other_funds: float,
    cost_cny: float,
    account_currency: str,
) -> float:
    """初始资金 = 总资产 - 盈亏 = 股票现金 + 其他 + 持仓成本（账户币种）。"""
    initial_cny = (
        _to_cny_amount(available_funds, account_currency)
        + _to_cny_amount(other_funds, account_currency)
        + float(cost_cny or 0)
    )
    return round(_convert_amount(initial_cny, "CNY", account_currency), 2)


def _sync_account_initial_funds(account: Account, db: Session) -> None:
    currency = str(getattr(account, "base_currency", "CNY") or "CNY").upper()
    cost_cny = _account_open_cost_cny(account, db)
    account.initial_funds = _compute_initial_funds(
        float(account.available_funds or 0),
        _account_other_funds_total(account),
        cost_cny,
        currency,
    )


# ========== Pydantic Models ==========

class OtherFundItem(BaseModel):
    label: str
    amount: float = 0


class AccountCreate(BaseModel):
    name: str
    available_funds: float = 0
    other_funds: float = 0
    other_fund_items: list[OtherFundItem] | None = None
    initial_funds: float | None = None
    base_currency: str = "CNY"


class AccountUpdate(BaseModel):
    name: str | None = None
    available_funds: float | None = None
    other_funds: float | None = None
    other_fund_items: list[OtherFundItem] | None = None
    initial_funds: float | None = None
    base_currency: str | None = None
    enabled: bool | None = None


class AccountResponse(BaseModel):
    id: int
    name: str
    available_funds: float
    other_funds: float = 0
    other_fund_items: list[OtherFundItem] = []
    initial_funds: float = 0
    base_currency: str = "CNY"
    enabled: bool

    @field_validator("other_fund_items", mode="before")
    @classmethod
    def _coerce_other_fund_items(cls, value):
        if not value:
            return []
        items: list[OtherFundItem] = []
        for raw in value:
            if isinstance(raw, OtherFundItem):
                items.append(raw)
            elif isinstance(raw, dict):
                label = str(raw.get("label") or "").strip()
                if label:
                    items.append(OtherFundItem(label=label, amount=float(raw.get("amount") or 0)))
            elif hasattr(raw, "model_dump"):
                dumped = raw.model_dump()
                label = str(dumped.get("label") or "").strip()
                if label:
                    items.append(OtherFundItem(label=label, amount=float(dumped.get("amount") or 0)))
        return items

    class Config:
        from_attributes = True


def _serialize_account(account: Account) -> AccountResponse:
    return AccountResponse.model_validate(account)


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
    status: str | None = None
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


class PositionTradeUpdateRequest(BaseModel):
    price: float | None = Field(default=None, gt=0, description="成交单价")
    quantity: int | None = Field(default=None, gt=0, description="成交股数")
    note: str | None = None
    traded_at: datetime | None = None
    side: str | None = Field(default=None, description="buy | sell")

    @field_validator("side")
    @classmethod
    def validate_side(cls, v: str | None) -> str | None:
        if v is None:
            return None
        s = v.strip().lower()
        if s not in ("buy", "sell"):
            raise ValueError("方向只能是 buy 或 sell")
        return s


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


def _replay_position_trades(trades: list[PositionTrade]) -> tuple[int, float]:
    """按时间顺序重放流水,重算每笔的前后持仓/成本快照,返回最终股数与成本。"""
    ordered = sorted(trades, key=lambda t: (t.traded_at or datetime.min, t.id))
    cur_qty = 0
    cur_cost = 0.0

    for idx, trade in enumerate(ordered):
        price = float(trade.price)
        qty = int(trade.quantity)
        side = trade.side
        trade.amount = round(price * qty, 4)

        if side == "buy":
            if idx == 0:
                trade.qty_before = None
                trade.cost_before = None
            elif cur_qty <= 0:
                trade.qty_before = 0
                trade.cost_before = None
            else:
                trade.qty_before = cur_qty
                trade.cost_before = cur_cost
            if cur_qty <= 0:
                new_qty, new_cost = qty, price
            else:
                new_qty, new_cost = _calc_weighted_cost(cur_qty, cur_cost, qty, price)
        elif side == "sell":
            if qty > cur_qty:
                raise ValueError(
                    f"交易记录({trade.id})卖出 {qty} 股超过当时持仓 {cur_qty} 股"
                )
            trade.qty_before = cur_qty
            trade.cost_before = cur_cost
            new_qty = cur_qty - qty
            new_cost = cur_cost
        else:
            raise ValueError(f"未知交易方向: {side}")

        trade.qty_after = new_qty
        trade.cost_after = new_cost
        cur_qty = new_qty
        cur_cost = new_cost

    return cur_qty, cur_cost


def _normalize_traded_at(dt: datetime | None) -> datetime:
    """统一成交时间为 UTC naive，供数据库存储。"""
    if dt is None:
        return datetime.now(timezone.utc).replace(tzinfo=None)
    return to_utc(dt).replace(tzinfo=None)


def _format_dt_naive(dt) -> str | None:
    """格式化 naive datetime 为 ISO 字符串(供响应序列化)。"""
    if not dt:
        return None
    if dt.tzinfo is not None:
        dt = dt.replace(tzinfo=None)
    return dt.isoformat(timespec="seconds")


def compute_realized_pnl(db: Session, position_id: int) -> float:
    """按交易流水汇总某持仓的累计实现盈亏:卖出净额 − 买入成本(原币种)。

    清仓时调用,锁定该持仓生命周期内的实现盈亏。
    """
    rows = (
        db.query(PositionTrade)
        .filter(PositionTrade.position_id == position_id)
        .all()
    )
    buy_cost = sum(float(t.amount) for t in rows if t.side == "buy")
    sell_proceeds = sum(float(t.amount) for t in rows if t.side == "sell")
    return round(sell_proceeds - buy_cost, 4)


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
    return [_serialize_account(account) for account in db.query(Account).order_by(Account.id).all()]


@router.get("/accounts/{account_id}", response_model=AccountResponse)
def get_account(account_id: int, db: Session = Depends(get_db)):
    """获取单个账户"""
    account = db.query(Account).filter(Account.id == account_id).first()
    if not account:
        raise HTTPException(404, "账户不存在")
    return _serialize_account(account)


@router.post("/accounts", response_model=AccountResponse)
def create_account(data: AccountCreate, db: Session = Depends(get_db)):
    """创建账户"""
    currency = _normalize_currency(data.base_currency)
    other_items = _normalize_other_fund_items(
        [item.model_dump() for item in data.other_fund_items] if data.other_fund_items is not None else None,
        data.other_funds if data.other_fund_items is None else None,
    )
    other_total = _sum_other_fund_items(other_items)
    account = Account(
        name=data.name,
        available_funds=data.available_funds,
        other_funds=other_total,
        other_fund_items=other_items,
        initial_funds=0,
        base_currency=currency,
    )
    db.add(account)
    db.commit()
    db.refresh(account)
    _sync_account_initial_funds(account, db)
    db.commit()
    db.refresh(account)
    logger.info(f"创建账户: {account.name}")
    return _serialize_account(account)


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
    if data.other_fund_items is not None:
        _apply_account_other_funds(account, [item.model_dump() for item in data.other_fund_items])
    elif data.other_funds is not None:
        _apply_account_other_funds(account, legacy_other_funds=data.other_funds)
    if data.base_currency is not None:
        account.base_currency = _normalize_currency(data.base_currency)
    if data.enabled is not None:
        account.enabled = data.enabled

    _sync_account_initial_funds(account, db)
    db.commit()
    db.refresh(account)
    logger.info(f"更新账户: {account.name}")
    return _serialize_account(account)


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
    status: str | None = "open",
    db: Session = Depends(get_db)
):
    """获取持仓列表，可按账户或股票筛选。status 默认仅返回 open(持仓中)。"""
    query = db.query(Position)
    if account_id:
        query = query.filter(Position.account_id == account_id)
    if stock_id:
        query = query.filter(Position.stock_id == stock_id)
    if status and status != "all":
        query = query.filter(Position.status == status)

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
            "status": pos.status or "open",
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
    if existing and (existing.status or "open") == "open":
        raise HTTPException(400, f"账户 {account.name} 已有 {stock.name} 的持仓，请编辑现有持仓")

    qty = int(data.quantity)
    cost = float(data.cost_price)
    traded_at = _normalize_traded_at(data.traded_at)

    if existing:
        # 复活已清仓持仓:保留历史流水,以本次建仓重置为新成本/股数
        position = existing
        position.cost_price = cost
        position.quantity = qty
        position.invested_amount = round(cost * qty, 4)
        position.trading_style = _resolve_trading_style(data.trading_style, position.trading_style)
        position.status = "open"
        position.closed_at = None
        position.realized_pnl = 0.0
        trade = PositionTrade(
            position_id=position.id,
            side="buy",
            price=cost,
            quantity=qty,
            amount=round(cost * qty, 4),
            cost_before=None,
            qty_before=0,
            cost_after=cost,
            qty_after=qty,
            note="清仓后重新建仓",
            traded_at=traded_at,
        )
        db.add(trade)
        db.flush()
    else:
        max_order = db.query(func.max(Position.sort_order)).filter(
            Position.account_id == data.account_id
        ).scalar() or 0

        position = Position(
            account_id=data.account_id,
            stock_id=data.stock_id,
            cost_price=cost,
            quantity=qty,
            invested_amount=round(cost * qty, 4),
            sort_order=int(max_order) + 1,
            trading_style=_resolve_trading_style(data.trading_style),
            status="open",
        )
        db.add(position)
        db.flush()

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

    # 建仓扣减股票现金（账户币种）
    adjust_account_stock_cash(
        account, side="buy", amount=round(cost * qty, 4), market=stock.market
    )
    _sync_account_initial_funds(account, db)
    db.commit()
    db.refresh(account)

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
        "status": position.status or "open",
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
        account = position.account
        if account is not None:
            adjust_account_stock_cash(
                account,
                side=side,
                amount=round(trade_price * trade_qty, 4),
                market=position.stock.market if position.stock else None,
            )

    if data.cost_price is not None:
        position.cost_price = new_cost
    if data.quantity is not None:
        position.quantity = new_qty
    if data.quantity is not None or data.cost_price is not None:
        position.invested_amount = round(new_cost * new_qty, 4)
    if data.trading_style is not None:
        # 空字符串表示清空，设为 None
        position.trading_style = data.trading_style if data.trading_style else None

    # 手动调整到 0 股视为清仓(仅当产生了卖出流水时回款)
    closed_by_edit = False
    if data.quantity is not None and new_qty <= 0 and (position.status or "open") == "open":
        position.status = "closed"
        position.closed_at = _normalize_traded_at(None)
        position.realized_pnl = compute_realized_pnl(db, position.id)
        closed_by_edit = True

    account = position.account
    if account is not None:
        _sync_account_initial_funds(account, db)
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
        "status": position.status or "open",
        "closed": closed_by_edit,
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
    is_revive = (position.status or "open") == "closed"

    # 已清仓持仓重新加仓:以本次买入为新成本起点(qty_before 视为 0)
    base_qty = 0 if is_revive else qty_before
    new_qty, new_cost = _calc_weighted_cost(base_qty, cost_before, add_qty, add_price)

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
        note=(data.note or "").strip() or ("清仓后重新建仓" if is_revive else None),
        traded_at=traded_at,
    )
    db.add(trade)

    position.quantity = new_qty
    position.cost_price = new_cost
    if position.invested_amount is not None and not is_revive:
        position.invested_amount = round(float(position.invested_amount) + add_amount, 4)
    else:
        position.invested_amount = round(new_cost * new_qty, 4)

    # 买入扣减股票现金（账户币种）
    account = position.account
    if account is not None:
        adjust_account_stock_cash(
            account,
            side="buy",
            amount=add_amount,
            market=position.stock.market if position.stock else None,
        )

    if is_revive:
        position.status = "open"
        position.closed_at = None
        position.realized_pnl = 0.0

    if account is not None:
        _sync_account_initial_funds(account, db)
    db.commit()
    db.refresh(position)
    db.refresh(trade)
    if account is not None:
        db.refresh(account)

    logger.info(
        "加仓: %s - %s +%d@%s → 成本 %.4f, 股数 %d%s",
        account.name if account else "未知账户",
        position.stock.name if position.stock else position.stock_id,
        add_qty,
        add_price,
        new_cost,
        new_qty,
        "（已清仓后重新建仓）" if is_revive else "",
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
            "status": position.status or "open",
            "closed_at": _format_dt_naive(position.closed_at),
            "realized_pnl": float(position.realized_pnl or 0.0),
            "account_name": account.name if account else None,
            "stock_symbol": position.stock.symbol if position.stock else None,
            "stock_name": position.stock.name if position.stock else None,
        },
        "trade": _serialize_position_trade(trade),
        "available_funds": float(account.available_funds) if account else None,
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

    # 清仓判断:卖完后剩余 0 股即视为清仓
    is_closed = new_qty <= 0

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
        note=(data.note or "").strip() or ("清仓" if is_closed else None),
        traded_at=traded_at,
    )
    db.add(trade)

    position.quantity = new_qty
    position.invested_amount = round(cost_before * new_qty, 4)

    # 卖出回款计入股票现金（账户币种）
    account = position.account
    if account is not None:
        adjust_account_stock_cash(
            account,
            side="sell",
            amount=sell_amount,
            market=position.stock.market if position.stock else None,
        )

    if is_closed:
        # 清仓:标记 closed 并锁定累计实现盈亏(按交易流水汇总,原币种口径)
        position.status = "closed"
        position.closed_at = traded_at
        position.realized_pnl = compute_realized_pnl(db, position.id)

    if account is not None:
        _sync_account_initial_funds(account, db)
    db.commit()
    db.refresh(position)
    db.refresh(trade)
    if account is not None:
        db.refresh(account)

    logger.info(
        "减仓: %s - %s -%d@%s → 成本 %.4f, 股数 %d%s",
        account.name if account else "未知账户",
        position.stock.name if position.stock else position.stock_id,
        sell_qty,
        sell_price,
        cost_before,
        new_qty,
        "（已清仓）" if is_closed else "",
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
            "status": position.status or "open",
            "closed_at": _format_dt_naive(position.closed_at),
            "realized_pnl": float(position.realized_pnl or 0.0),
            "account_name": account.name if account else None,
            "stock_symbol": position.stock.symbol if position.stock else None,
            "stock_name": position.stock.name if position.stock else None,
        },
        "trade": _serialize_position_trade(trade),
        "available_funds": float(account.available_funds) if account else None,
        "closed": is_closed,
    }


@router.get("/portfolio/closed-positions")
def list_closed_positions(
    account_id: int | None = None,
    limit: int = 100,
    db: Session = Depends(get_db),
):
    """已清仓持仓列表(含历史成交明细),默认按清仓时间倒序。"""
    query = db.query(Position).filter(Position.status == "closed")
    acc_q = db.query(Account).filter(Account.enabled == True)  # noqa: E712
    if account_id:
        query = query.filter(Position.account_id == account_id)
    rows = (
        query.order_by(Position.closed_at.desc().nullslast(), Position.id.desc())
        .limit(max(1, min(int(limit), 500)))
        .all()
    )
    out: list[dict] = []
    for pos in rows:
        trades = (
            db.query(PositionTrade)
            .filter(PositionTrade.position_id == pos.id)
            .order_by(PositionTrade.traded_at.desc(), PositionTrade.id.desc())
            .limit(50)
            .all()
        )
        out.append({
            "id": pos.id,
            "account_id": pos.account_id,
            "stock_id": pos.stock_id,
            "stock_symbol": pos.stock.symbol if pos.stock else None,
            "stock_name": pos.stock.name if pos.stock else None,
            "market": pos.stock.market if pos.stock else None,
            "account_name": pos.account.name if pos.account else None,
            "cost_price": pos.cost_price,
            "quantity": pos.quantity,
            "invested_amount": pos.invested_amount,
            "realized_pnl": float(pos.realized_pnl or 0.0),
            "opened_at": _format_dt_naive(pos.created_at),
            "closed_at": _format_dt_naive(pos.closed_at),
            "trading_style": pos.trading_style,
            "trades": [_serialize_position_trade(t) for t in trades],
        })
    return out


@router.get("/positions/{position_id}/trades")
def list_position_trades(
    position_id: int,
    limit: int = 20,
    db: Session = Depends(get_db),
):
    """获取持仓变动流水(最近 N 条,含已清仓持仓的历史明细)"""
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


@router.put("/positions/trades/{trade_id}")
def update_position_trade(
    trade_id: int,
    data: PositionTradeUpdateRequest,
    db: Session = Depends(get_db),
):
    """修改历史交易明细,并重放流水以同步持仓与账户现金。"""
    trade = db.query(PositionTrade).filter(PositionTrade.id == trade_id).first()
    if not trade:
        raise HTTPException(404, "交易记录不存在")

    position = trade.position
    if not position:
        raise HTTPException(404, "持仓不存在")

    if (
        data.price is None
        and data.quantity is None
        and data.note is None
        and data.traded_at is None
        and data.side is None
    ):
        raise HTTPException(400, "请至少提供一个要修改的字段")

    account = position.account
    market = position.stock.market if position.stock else None
    old_side = trade.side
    old_amount = float(trade.amount)

    if data.price is not None:
        trade.price = float(data.price)
    if data.quantity is not None:
        trade.quantity = int(data.quantity)
    if data.traded_at is not None:
        trade.traded_at = _normalize_traded_at(data.traded_at)
    if data.note is not None:
        trade.note = (data.note or "").strip() or None
    if data.side is not None:
        trade.side = data.side

    trade.amount = round(float(trade.price) * int(trade.quantity), 4)
    new_side = trade.side
    new_amount = float(trade.amount)

    all_trades = (
        db.query(PositionTrade)
        .filter(PositionTrade.position_id == position.id)
        .all()
    )
    try:
        final_qty, final_cost = _replay_position_trades(all_trades)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    if account is not None and (old_amount != new_amount or old_side != new_side):
        reverse_side = "sell" if old_side == "buy" else "buy"
        adjust_account_stock_cash(
            account, side=reverse_side, amount=old_amount, market=market
        )
        adjust_account_stock_cash(
            account, side=new_side, amount=new_amount, market=market
        )

    position.quantity = final_qty
    position.cost_price = final_cost
    if final_qty > 0:
        position.invested_amount = round(final_cost * final_qty, 4)
        position.status = "open"
        position.closed_at = None
        position.realized_pnl = 0.0
    else:
        position.invested_amount = 0.0
        position.status = "closed"
        last_trade = max(
            all_trades,
            key=lambda t: (t.traded_at or datetime.min, t.id),
        )
        position.closed_at = last_trade.traded_at
        position.realized_pnl = compute_realized_pnl(db, position.id)

    if account is not None:
        _sync_account_initial_funds(account, db)

    db.commit()
    db.refresh(trade)
    db.refresh(position)

    ordered_trades = sorted(
        all_trades,
        key=lambda t: (t.traded_at or datetime.min, t.id),
        reverse=True,
    )
    logger.info(
        "修改交易流水 #%d: %s - %s",
        trade.id,
        account.name if account else "未知账户",
        position.stock.name if position.stock else position.stock_id,
    )
    return {
        "trade": _serialize_position_trade(trade),
        "trades": [_serialize_position_trade(t) for t in ordered_trades],
        "position": {
            "id": position.id,
            "cost_price": position.cost_price,
            "quantity": position.quantity,
            "invested_amount": position.invested_amount,
            "status": position.status or "open",
            "closed_at": _format_dt_naive(position.closed_at),
            "realized_pnl": float(position.realized_pnl or 0.0),
        },
    }


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

    account = position.account
    db.delete(position)
    if account is not None:
        _sync_account_initial_funds(account, db)
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
                "other_funds": 0,
                "initial_funds": 0,
                "total_assets": 0,
            }
        }

    # 获取所有相关股票(仅持仓中,排除已清仓)
    all_stock_ids = set()
    all_position_ids: list[int] = []
    for acc in accounts:
        for pos in acc.positions:
            if (pos.status or "open") != "open":
                continue
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
    grand_other_funds = 0
    grand_initial_funds = 0
    grand_daily_pnl = 0

    for acc in accounts:
        positions_data = []
        acc_market_value = 0
        acc_cost = 0
        acc_daily_pnl = 0

        positions_sorted = sorted(
            [p for p in (acc.positions or []) if (p.status or "open") == "open"],
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

        acc_other_funds = _account_other_funds_total(acc)
        acc_other_items = getattr(acc, "other_fund_items", None) or []
        acc_currency = str(getattr(acc, "base_currency", "CNY") or "CNY").upper()
        acc_available_cny = _to_cny_amount(acc.available_funds, acc_currency)
        acc_other_cny = _to_cny_amount(acc_other_funds, acc_currency)
        if include_quotes:
            acc_pnl = acc_market_value - acc_cost
            acc_total_assets = acc_market_value + acc_available_cny + acc_other_cny
            acc_initial_cny = acc_total_assets - acc_pnl
            acc_pnl_pct = (acc_pnl / acc_initial_cny * 100) if acc_initial_cny > 0 else 0
        else:
            acc_pnl = 0
            acc_total_assets = acc_available_cny + acc_other_cny
            acc_initial_cny = acc_available_cny + acc_other_cny + acc_cost
            acc_pnl_pct = 0

        account_summaries.append({
            "id": acc.id,
            "name": acc.name,
            "base_currency": acc_currency,
            "available_funds": acc.available_funds,
            "other_funds": round(acc_other_funds, 2),
            "other_fund_items": acc_other_items,
            "initial_funds": _compute_initial_funds(
                float(acc.available_funds or 0),
                acc_other_funds,
                acc_cost,
                acc_currency,
            ),
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
        grand_available_funds += acc_available_cny
        grand_other_funds += acc_other_cny
        grand_initial_funds += acc_initial_cny
        grand_daily_pnl += acc_daily_pnl

    if include_quotes:
        grand_pnl = grand_total_market_value - grand_total_cost
        grand_total_assets = grand_total_market_value + grand_available_funds + grand_other_funds
        grand_initial_funds = grand_total_assets - grand_pnl
        grand_pnl_pct = (grand_pnl / grand_initial_funds * 100) if grand_initial_funds > 0 else 0
    else:
        grand_pnl = 0
        grand_pnl_pct = 0
        grand_total_assets = grand_available_funds + grand_other_funds
        grand_initial_funds = grand_available_funds + grand_other_funds + grand_total_cost

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
            "other_funds": round(grand_other_funds, 2),
            "initial_funds": round(grand_initial_funds, 2),
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
    open_positions = [p for acc in accounts for p in (acc.positions or []) if (p.status or "open") == "open"]
    stock_ids = {p.stock_id for p in open_positions}
    stocks = db.query(Stock).filter(Stock.id.in_(stock_ids)).all() if stock_ids else []
    stock_map = {s.id: s for s in stocks}
    quotes = _fetch_quotes_for_stocks(stocks) if stocks else {}
    hkd, usd = get_hkd_cny_rate(), get_usd_cny_rate()

    out: list[dict] = []
    seen: dict[tuple[str, str], dict] = {}
    for acc in accounts:
        for pos in (acc.positions or []):
            if (pos.status or "open") != "open":
                continue
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
    held_ids = {p.stock_id for acc in accounts for p in (acc.positions or []) if (p.status or "open") == "open"}
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
