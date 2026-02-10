import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from pydantic import BaseModel

from src.web.database import get_db
from src.web.models import Stock, StockAgent, AgentConfig
from src.web.stock_list import search_stocks, refresh_stock_list
from src.collectors.akshare_collector import _tencent_symbol, _fetch_tencent_quotes
from src.models.market import MarketCode, MARKETS

logger = logging.getLogger(__name__)
router = APIRouter()


class StockCreate(BaseModel):
    symbol: str
    name: str
    market: str = "CN"


class StockUpdate(BaseModel):
    name: str | None = None
    enabled: bool | None = None


class StockAgentInfo(BaseModel):
    agent_name: str
    schedule: str = ""
    ai_model_id: int | None = None
    notify_channel_ids: list[int] = []


class StockResponse(BaseModel):
    id: int
    symbol: str
    name: str
    market: str
    enabled: bool
    agents: list[StockAgentInfo] = []

    class Config:
        from_attributes = True


class StockAgentItem(BaseModel):
    agent_name: str
    schedule: str = ""
    ai_model_id: int | None = None
    notify_channel_ids: list[int] = []


class StockAgentUpdate(BaseModel):
    agents: list[StockAgentItem]


def _stock_to_response(stock: Stock) -> dict:
    return {
        "id": stock.id,
        "symbol": stock.symbol,
        "name": stock.name,
        "market": stock.market,
        "enabled": stock.enabled,
        "agents": [
            {
                "agent_name": sa.agent_name,
                "schedule": sa.schedule or "",
                "ai_model_id": sa.ai_model_id,
                "notify_channel_ids": sa.notify_channel_ids or [],
            }
            for sa in stock.agents
        ],
    }


@router.get("/markets/status")
def get_market_status():
    """获取各市场的交易状态"""
    from datetime import datetime

    result = []
    for market_code, market_def in MARKETS.items():
        try:
            now = datetime.now(market_def.get_tz())
            is_trading = market_def.is_trading_time()

            # 获取交易时段描述
            sessions_desc = []
            for session in market_def.sessions:
                sessions_desc.append(f"{session.start.strftime('%H:%M')}-{session.end.strftime('%H:%M')}")

            # 判断状态
            weekday = now.weekday()
            current_time = now.time()

            if weekday >= 5:
                status = "closed"
                status_text = "休市（周末）"
            elif is_trading:
                status = "trading"
                status_text = "交易中"
            else:
                # 判断是盘前还是盘后
                first_session = market_def.sessions[0]
                last_session = market_def.sessions[-1]
                if current_time < first_session.start:
                    status = "pre_market"
                    status_text = "盘前"
                elif current_time > last_session.end:
                    status = "after_hours"
                    status_text = "已收盘"
                else:
                    status = "break"
                    status_text = "午间休市"

            result.append({
                "code": market_code.value,
                "name": market_def.name,
                "status": status,
                "status_text": status_text,
                "is_trading": is_trading,
                "sessions": sessions_desc,
                "local_time": now.strftime("%H:%M"),
                "timezone": market_def.timezone,
            })
        except Exception as e:
            # 单个市场获取失败不影响其他市场
            logger.error(f"获取 {market_code.value} 市场状态失败: {e}")
            result.append({
                "code": market_code.value,
                "name": market_def.name,
                "status": "unknown",
                "status_text": "未知",
                "is_trading": False,
                "sessions": [],
                "local_time": "--:--",
                "timezone": market_def.timezone,
                "error": str(e),
            })

    return result


@router.get("/search")
def search(q: str = Query("", min_length=1), market: str = Query("")):
    """模糊搜索股票(代码/名称)"""
    return search_stocks(q, market)


@router.post("/refresh-list")
def refresh_list():
    """刷新股票列表缓存"""
    stocks = refresh_stock_list()
    return {"count": len(stocks)}


@router.get("", response_model=list[StockResponse])
def list_stocks(db: Session = Depends(get_db)):
    stocks = db.query(Stock).all()
    return [_stock_to_response(s) for s in stocks]


@router.get("/quotes")
def get_quotes(db: Session = Depends(get_db)):
    """获取所有自选股的实时行情"""
    stocks = db.query(Stock).filter(Stock.enabled == True).all()
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
                quotes[item["symbol"]] = {
                    "current_price": item["current_price"],
                    "change_pct": item["change_pct"],
                    "change_amount": item["change_amount"],
                    "prev_close": item["prev_close"],
                }
        except Exception as e:
            logger.error(f"获取 {market} 行情失败: {e}")

    return quotes


@router.post("", response_model=StockResponse)
def create_stock(stock: StockCreate, db: Session = Depends(get_db)):
    existing = db.query(Stock).filter(
        Stock.symbol == stock.symbol, Stock.market == stock.market
    ).first()
    if existing:
        raise HTTPException(400, f"股票 {stock.symbol} 已存在")

    db_stock = Stock(**stock.model_dump())
    db.add(db_stock)
    db.commit()
    db.refresh(db_stock)
    return _stock_to_response(db_stock)


@router.put("/{stock_id}", response_model=StockResponse)
def update_stock(stock_id: int, stock: StockUpdate, db: Session = Depends(get_db)):
    db_stock = db.query(Stock).filter(Stock.id == stock_id).first()
    if not db_stock:
        raise HTTPException(404, "股票不存在")

    for key, value in stock.model_dump(exclude_unset=True).items():
        setattr(db_stock, key, value)

    db.commit()
    db.refresh(db_stock)
    return _stock_to_response(db_stock)


@router.delete("/{stock_id}")
def delete_stock(stock_id: int, db: Session = Depends(get_db)):
    db_stock = db.query(Stock).filter(Stock.id == stock_id).first()
    if not db_stock:
        raise HTTPException(404, "股票不存在")
    db.delete(db_stock)
    db.commit()
    return {"ok": True}


@router.put("/{stock_id}/agents", response_model=StockResponse)
def update_stock_agents(stock_id: int, body: StockAgentUpdate, db: Session = Depends(get_db)):
    """更新股票关联的 Agent 列表（含调度配置和 AI/通知覆盖）"""
    db_stock = db.query(Stock).filter(Stock.id == stock_id).first()
    if not db_stock:
        raise HTTPException(404, "股票不存在")

    for item in body.agents:
        agent = db.query(AgentConfig).filter(AgentConfig.name == item.agent_name).first()
        if not agent:
            raise HTTPException(400, f"Agent {item.agent_name} 不存在")

    # 清除旧关联，重建
    db.query(StockAgent).filter(StockAgent.stock_id == stock_id).delete()
    for item in body.agents:
        db.add(StockAgent(
            stock_id=stock_id,
            agent_name=item.agent_name,
            schedule=item.schedule,
            ai_model_id=item.ai_model_id,
            notify_channel_ids=item.notify_channel_ids,
        ))

    db.commit()
    db.refresh(db_stock)
    return _stock_to_response(db_stock)


@router.post("/{stock_id}/agents/{agent_name}/trigger")
async def trigger_stock_agent(
    stock_id: int,
    agent_name: str,
    bypass_throttle: bool = False,
    db: Session = Depends(get_db),
):
    """手动触发某只股票的指定 Agent"""
    db_stock = db.query(Stock).filter(Stock.id == stock_id).first()
    if not db_stock:
        raise HTTPException(404, "股票不存在")

    sa = db.query(StockAgent).filter(
        StockAgent.stock_id == stock_id, StockAgent.agent_name == agent_name
    ).first()
    if not sa:
        raise HTTPException(400, f"股票未关联 Agent {agent_name}")

    logger.info(f"手动触发 Agent {agent_name} - {db_stock.name}({db_stock.symbol})")

    from server import trigger_agent_for_stock
    try:
        result = await trigger_agent_for_stock(
            agent_name, db_stock, stock_agent_id=sa.id, bypass_throttle=bypass_throttle
        )
        logger.info(f"Agent {agent_name} 执行完成 - {db_stock.symbol}")
        return {
            "result": result,
            "code": int(result.get("code", 0)),
            "success": bool(result.get("success", True)),
            "message": result.get("message", "ok"),
        }
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.error(f"Agent {agent_name} 执行失败 - {db_stock.symbol}: {e}")
        raise HTTPException(500, f"Agent 执行失败: {e}")
