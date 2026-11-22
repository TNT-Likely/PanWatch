"""Typed issuer-disclosure references from the configured announcement provider."""

import re
from datetime import date
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.modules.mobile.schemas import MobileResponse, WatchlistSymbol
from src.platform.marketdata.marketdata_client import get_market_data
from src.platform.persistence.database import get_db
from src.platform.persistence.models import Stock
from src.web.errors import api_error

DB = Annotated[Session, Depends(get_db)]
DisclosureType = Literal["earnings", "dividend", "suspension", "repurchase", "financing", "regulatory", "restructuring"]
_KINDS: dict[str, DisclosureType] = {
    "earnings": "earnings", "dividend": "dividend", "suspension": "suspension",
    "repurchase": "repurchase", "financing": "financing", "regulatory": "regulatory",
    "restructuring": "restructuring",
}
_ARTICLE = re.compile(r"AN\d{8,30}")
_STOCK = re.compile(r"\d{6}")


class Disclosure(BaseModel):
    id: str
    target: WatchlistSymbol
    kind: DisclosureType
    date: date
    source: Literal["eastmoney"] = "eastmoney"
    url: str


class Disclosures(BaseModel):
    state: Literal["ready", "upstream_unavailable", "market_unavailable"]
    items: list[Disclosure]


def create_disclosures_router(require_device) -> APIRouter:
    router = APIRouter(dependencies=[Depends(require_device)])

    @router.get("/facts/disclosures")
    def disclosures(
        db: DB, market: Literal["CN", "HK", "US"] = "CN",
        symbol: Annotated[str | None, Query(min_length=1, max_length=80)] = None,
        days: Literal["7", "30"] = "7",
    ) -> MobileResponse[Disclosures]:
        query = select(Stock).where(Stock.market == market).order_by(Stock.id)
        if symbol is not None:
            query = query.where(Stock.symbol == symbol)
        stocks = db.scalars(query).all()
        if symbol is not None and not stocks:
            raise api_error(404, "mobile_symbol_not_found", "此标的不可用。")
        if market != "CN":
            return MobileResponse(data=Disclosures(state="market_unavailable", items=[]))

        universe = {}
        for row in stocks:
            if _STOCK.fullmatch(row.symbol):
                universe.setdefault(row.symbol, WatchlistSymbol.model_validate(row, from_attributes=True))
        if not universe:
            return MobileResponse(data=Disclosures(state="ready", items=[]))

        events, success = get_market_data().events_with_status(list(universe), market=market, since_days=int(days))
        items = {}
        for item in events:
            kind = _KINDS.get(item.event_type)
            if not kind or item.source != "eastmoney" or not _ARTICLE.fullmatch(item.external_id):
                continue
            for code in set(item.symbols).intersection(universe):
                identity = f"{code}:{item.external_id}"
                href = f"https://data.eastmoney.com/notices/detail/{code}/{item.external_id}.html"
                if href != item.url:
                    continue
                items.setdefault(identity, Disclosure(
                    id=identity, target=universe[code], kind=kind, date=item.publish_time.date(), url=href,
                ))
        ordered = sorted(items.values(), key=lambda item: (item.date, item.id), reverse=True)
        return MobileResponse(data=Disclosures(
            state="ready" if success else "upstream_unavailable", items=ordered[:50],
        ))

    return router
