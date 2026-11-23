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
from src.platform.persistence.models import AgentContextRun, Stock
from src.web.errors import api_error

DB = Annotated[Session, Depends(get_db)]
DisclosureType = Literal["earnings", "dividend", "suspension", "repurchase", "financing", "regulatory", "restructuring"]
ArchiveAgent = Literal["premarket_outlook", "intraday_monitor", "daily_report"]
_ARCHIVE_AGENTS = ("premarket_outlook", "intraday_monitor", "daily_report")
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


class ArchivedDisclosure(Disclosure):
    archive_agents: list[ArchiveAgent]


class DisclosureArchive(BaseModel):
    state: Literal["ready"]
    items: list[ArchivedDisclosure]


def _verified_disclosure(
    target: WatchlistSymbol, source: object, article: object, kind: object, published: date, url: object,
) -> Disclosure | None:
    if source != "eastmoney" or not isinstance(article, str) or not _ARTICLE.fullmatch(article):
        return None
    category = _KINDS.get(kind) if isinstance(kind, str) else None
    href = f"https://data.eastmoney.com/notices/detail/{target.symbol}/{article}.html"
    if category is None or url != href:
        return None
    return Disclosure(id=f"{target.symbol}:{article}", target=target, kind=category, date=published, url=href)


def _latest_items(items: dict[str, Disclosure]) -> list[Disclosure]:
    return sorted(items.values(), key=lambda item: (item.date, item.id), reverse=True)[:50]


def _cn_universe(db: Session, symbol: str | None) -> dict[str, WatchlistSymbol]:
    query = select(Stock).where(Stock.market == "CN").order_by(Stock.id)
    if symbol is not None:
        query = query.where(Stock.symbol == symbol)
    universe = {}
    for row in db.scalars(query):
        if _STOCK.fullmatch(row.symbol):
            universe.setdefault(row.symbol, WatchlistSymbol.model_validate(row, from_attributes=True))
    return universe


def load_disclosures(db: Session, market: str, symbol: str | None, days: int) -> Disclosures:
    if market != "CN":
        return Disclosures(state="market_unavailable", items=[])

    universe = _cn_universe(db, symbol)
    if not universe:
        return Disclosures(state="ready", items=[])

    events, success = get_market_data().events_with_status(list(universe), market=market, since_days=days)
    items = {}
    for event in events:
        for code in set(event.symbols).intersection(universe):
            item = _verified_disclosure(universe[code], event.source, event.external_id,
                                        event.event_type, event.publish_time.date(), event.url)
            if item:
                items.setdefault(item.id, item)
    return Disclosures(state="ready" if success else "upstream_unavailable", items=_latest_items(items))


def create_disclosures_router(require_device) -> APIRouter:
    router = APIRouter(dependencies=[Depends(require_device)])

    @router.get("/facts/disclosures")
    def disclosures(
        db: DB, market: Literal["CN", "HK", "US"] = "CN",
        symbol: Annotated[str | None, Query(min_length=1, max_length=80)] = None,
        days: Literal["7", "30"] = "7",
    ) -> MobileResponse[Disclosures]:
        if symbol is not None and db.scalar(select(Stock.id).where(Stock.market == market, Stock.symbol == symbol).limit(1)) is None:
            raise api_error(404, "mobile_symbol_not_found", "此标的不可用。")
        return MobileResponse(data=load_disclosures(db, market, symbol, int(days)))

    @router.get("/facts/disclosures/archive")
    def archive(
        db: DB, on: date, agent: ArchiveAgent | None = None,
        market: Literal["CN", "HK", "US"] = "CN",
        symbol: Annotated[str | None, Query(min_length=1, max_length=80)] = None,
        include_agents: bool = False,
    ) -> MobileResponse[DisclosureArchive | Disclosures]:
        if market != "CN":
            return MobileResponse(data=Disclosures(state="market_unavailable", items=[]))
        universe = _cn_universe(db, symbol)
        if not universe:
            return MobileResponse(data=Disclosures(state="ready", items=[]))
        query = select(AgentContextRun.agent_name, AgentContextRun.context_payload).where(
            AgentContextRun.analysis_date == on.isoformat(),
            AgentContextRun.agent_name.in_((agent,) if agent else _ARCHIVE_AGENTS),
            AgentContextRun.stock_symbol == "*",
        ).order_by(AgentContextRun.id.desc())
        # A busy intraday workflow can otherwise hide another agent's earlier recording of the same article.
        runs = db.execute(query.execution_options(yield_per=30) if include_agents else query.limit(30))
        items: dict[str, Disclosure] = {}
        recorded_by: dict[str, set[ArchiveAgent]] = {}
        for agent_name, payload in runs:
            symbols = payload.get("symbols") if isinstance(payload, dict) else None
            if not isinstance(symbols, dict):
                continue
            for identity, context in symbols.items():
                market_code, _, code = identity.partition(":")
                target = universe.get(code) if market_code == "CN" else None
                if target is None:
                    continue
                events = context.get("events") if isinstance(context, dict) else None
                if not isinstance(events, list):
                    continue
                for event in events:
                    if not isinstance(event, dict) or not isinstance(event.get("time"), str):
                        continue
                    try:
                        published = date.fromisoformat(event["time"][:10])
                    except ValueError:
                        continue
                    if published > on:
                        continue
                    item = _verified_disclosure(target, event.get("source"), event.get("external_id"),
                                                event.get("event_type"), published, event.get("url"))
                    if item:
                        items.setdefault(item.id, item)
                        if include_agents:
                            recorded_by.setdefault(item.id, set()).add(agent_name)
        latest = _latest_items(items)
        if include_agents:
            archived = [
                ArchivedDisclosure(**item.model_dump(), archive_agents=sorted(recorded_by[item.id])) for item in latest
            ]
            return MobileResponse(data=DisclosureArchive(state="ready", items=archived))
        return MobileResponse(data=Disclosures(state="ready", items=latest))

    return router
