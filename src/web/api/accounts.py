"""账户和持仓管理 API"""
import logging
import time
import httpx
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel

from src.web.database import get_db
from src.web.models import Account, Position, Stock
from src.collectors.akshare_collector import _tencent_symbol, _fetch_tencent_quotes
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


class PositionUpdate(BaseModel):
    cost_price: float | None = None
    quantity: int | None = None
    invested_amount: float | None = None
    trading_style: str | None = None


class PositionResponse(BaseModel):
    id: int
    account_id: int
    stock_id: int
    cost_price: float
    quantity: int
    invested_amount: float | None
    trading_style: str | None
    # 关联信息
    account_name: str | None = None
    stock_symbol: str | None = None
    stock_name: str | None = None

    class Config:
        from_attributes = True


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

    positions = query.all()
    result = []
    for pos in positions:
        result.append({
            "id": pos.id,
            "account_id": pos.account_id,
            "stock_id": pos.stock_id,
            "cost_price": pos.cost_price,
            "quantity": pos.quantity,
            "invested_amount": pos.invested_amount,
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

    position = Position(
        account_id=data.account_id,
        stock_id=data.stock_id,
        cost_price=data.cost_price,
        quantity=data.quantity,
        invested_amount=data.invested_amount,
        trading_style=data.trading_style,
    )
    db.add(position)
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
        "trading_style": position.trading_style,
        "account_name": account.name,
        "stock_symbol": stock.symbol,
        "stock_name": stock.name,
    }


@router.put("/positions/{position_id}", response_model=PositionResponse)
def update_position(position_id: int, data: PositionUpdate, db: Session = Depends(get_db)):
    """更新持仓"""
    position = db.query(Position).filter(Position.id == position_id).first()
    if not position:
        raise HTTPException(404, "持仓不存在")

    if data.cost_price is not None:
        position.cost_price = data.cost_price
    if data.quantity is not None:
        position.quantity = data.quantity
    if data.invested_amount is not None:
        position.invested_amount = data.invested_amount
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
        "trading_style": position.trading_style,
        "account_name": position.account.name,
        "stock_symbol": position.stock.symbol,
        "stock_name": position.stock.name,
    }


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


# ========== Portfolio Summary ==========

@router.get("/portfolio/summary")
def get_portfolio_summary(account_id: int | None = None, db: Session = Depends(get_db)):
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
    for acc in accounts:
        for pos in acc.positions:
            all_stock_ids.add(pos.stock_id)

    stocks = db.query(Stock).filter(Stock.id.in_(all_stock_ids)).all() if all_stock_ids else []
    stock_map = {s.id: s for s in stocks}

    # 获取实时行情
    quotes = _fetch_quotes_for_stocks(stocks)

    # 获取汇率
    hkd_rate = get_hkd_cny_rate()
    usd_rate = get_usd_cny_rate()

    # 计算各账户持仓
    account_summaries = []
    grand_total_market_value = 0
    grand_total_cost = 0
    grand_available_funds = 0

    for acc in accounts:
        positions_data = []
        acc_market_value = 0
        acc_cost = 0

        for pos in acc.positions:
            stock = stock_map.get(pos.stock_id)
            if not stock:
                continue

            quote = quotes.get(stock.symbol)
            current_price = quote["current_price"] if quote else None
            change_pct = quote["change_pct"] if quote else None

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

            if current_price is not None:
                market_value = current_price * pos.quantity  # 原币种市值
                market_value_cny = market_value * rate  # 人民币市值
                cost = pos.cost_price * pos.quantity
                cost_cny = cost * rate  # 假设成本价也是原币种
                pnl = market_value_cny - cost_cny
                pnl_pct = (pnl / cost_cny * 100) if cost_cny > 0 else 0

                acc_market_value += market_value_cny
                acc_cost += cost_cny

            positions_data.append({
                "id": pos.id,
                "stock_id": pos.stock_id,
                "symbol": stock.symbol,
                "name": stock.name,
                "market": stock.market,
                "cost_price": pos.cost_price,
                "quantity": pos.quantity,
                "invested_amount": pos.invested_amount,
                "trading_style": pos.trading_style,
                "current_price": current_price,
                "current_price_cny": round(current_price * rate, 2) if current_price else None,
                "change_pct": change_pct,
                "market_value": round(market_value, 2) if market_value else None,
                "market_value_cny": round(market_value_cny, 2) if market_value_cny else None,
                "pnl": round(pnl, 2) if pnl else None,
                "pnl_pct": round(pnl_pct, 2) if pnl_pct else None,
                "exchange_rate": rate if is_foreign else None,
            })

        acc_pnl = acc_market_value - acc_cost
        acc_pnl_pct = (acc_pnl / acc_cost * 100) if acc_cost > 0 else 0
        acc_total_assets = acc_market_value + acc.available_funds

        account_summaries.append({
            "id": acc.id,
            "name": acc.name,
            "available_funds": acc.available_funds,
            "total_market_value": round(acc_market_value, 2),
            "total_cost": round(acc_cost, 2),
            "total_pnl": round(acc_pnl, 2),
            "total_pnl_pct": round(acc_pnl_pct, 2),
            "total_assets": round(acc_total_assets, 2),
            "positions": positions_data,
        })

        grand_total_market_value += acc_market_value
        grand_total_cost += acc_cost
        grand_available_funds += acc.available_funds

    grand_pnl = grand_total_market_value - grand_total_cost
    grand_pnl_pct = (grand_pnl / grand_total_cost * 100) if grand_total_cost > 0 else 0
    grand_total_assets = grand_total_market_value + grand_available_funds

    # 构建 quotes 字典（用于前端股票列表显示）
    quotes_dict = {}
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
            "available_funds": round(grand_available_funds, 2),
            "total_assets": round(grand_total_assets, 2),
        },
        "exchange_rates": {
            "HKD_CNY": hkd_rate,
            "USD_CNY": usd_rate,
        },
        "quotes": quotes_dict,  # 新增：直接返回行情数据
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
