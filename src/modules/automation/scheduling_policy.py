"""Effective stock schedules and market eligibility shared by execution/preview."""
from __future__ import annotations

from copy import copy
from dataclasses import dataclass, replace
import logging
import sys

from src.modules.automation.base import PortfolioInfo
from src.platform.marketdata.models import MARKETS
from src.platform.persistence.models import StockAgent
from src.platform.scheduling.trading_calendar import is_trading_day

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SchedulePlan:
    schedule: str
    stock_keys: tuple[tuple[str, str], ...]
    stock_agent_id: int | None = None

    @property
    def markets(self) -> list[str]:
        return sorted({market for market, _symbol in self.stock_keys})


def schedule_plans(db, agent, *, global_schedule: str | None = None) -> list[SchedulePlan]:
    inherited = []
    plans = []
    bindings = db.query(StockAgent).filter(StockAgent.agent_name == agent.name).all()
    for binding in bindings:
        stock = binding.stock
        if stock is None:
            continue
        key = (stock.market, stock.symbol)
        # Batch reports have a shared schedule; per-stock overrides apply to single mode.
        if agent.execution_mode == "single" and (binding.schedule or "").strip():
            plans.append(SchedulePlan(binding.schedule.strip(), (key,), binding.id))
        else:
            inherited.append(key)
    schedule = agent.schedule if global_schedule is None else global_schedule
    if schedule and inherited:
        plans.insert(0, SchedulePlan(schedule, tuple(inherited)))
    return plans


def market_allowed(agent_name: str, market, dt=None) -> bool:
    if agent_name == "intraday_monitor":
        definition = MARKETS.get(market)
        return definition is not None and definition.is_trading_time(dt)
    return is_trading_day(market, dt)


def scoped_context(context, watchlist):
    """Filter both report inputs and positions without mutating the caller's context."""
    scoped = copy(context)
    if hasattr(context, "config"):
        scoped.config = copy(context.config)
        scoped.config.watchlist = watchlist
    else:
        scoped.watchlist = watchlist
    if isinstance(getattr(context, "portfolio", None), PortfolioInfo):
        keys = {(stock.market, stock.symbol) for stock in watchlist}
        scoped.portfolio = PortfolioInfo(accounts=[replace(account, positions=[
            p for p in account.positions if (p.market, p.symbol) in keys
        ]) for account in context.portfolio.accounts])
    return scoped


def request_scheduler_reload() -> None:
    """Reload on the owning event loop, including updates from FastAPI worker threads."""
    runtime = sys.modules.get("server")
    scheduler = getattr(runtime, "scheduler", None)
    loop = getattr(getattr(scheduler, "scheduler", None), "_eventloop", None)
    if runtime is not None and loop is not None and loop.is_running():
        loop.call_soon_threadsafe(runtime.reload_scheduler)
