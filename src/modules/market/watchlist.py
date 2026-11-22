"""Atomic insertion shared by the Web and the paired mobile clients."""

from sqlalchemy import exists, func, insert, literal, select
from sqlalchemy.orm import Session

from src.platform.marketdata.instruments import infer_asset_type
from src.platform.persistence.models import Stock


def add_stock(db: Session, *, symbol: str, name: str, market: str, asset_type: str | None = None) -> Stock | None:
    exists_query = exists().where(Stock.market == market, Stock.symbol == symbol)
    next_order = select(func.coalesce(func.max(Stock.sort_order), 0) + 1).scalar_subquery()
    row = select(
        literal(symbol), literal(name), literal(market), literal(infer_asset_type(name, asset_type)), next_order,
    ).where(~exists_query)
    stock_id = db.scalar(insert(Stock).from_select(
        ["symbol", "name", "market", "asset_type", "sort_order"], row,
    ).returning(Stock.id))
    if stock_id is None:
        return None
    db.commit()
    return db.get(Stock, stock_id)
