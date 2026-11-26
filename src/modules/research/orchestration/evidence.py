"""Convert existing collection output into scoped, timestamped package inputs."""
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
import asyncio
import hashlib
import json
import logging
import math
import time

from pan_investment_research import Evidence, ResearchInput, ResearchSignal, Security
from .signals import inspect_changes, published, material
from . import repository
from src.platform.persistence.worker import run_db_operation

from src.modules.automation.scheduling_policy import scoped_context
from src.platform.marketdata.models import MARKETS, IndexData, MarketCode, StockData
from src.platform.scheduling.trading_calendar import calendar_known, exchange_session_boundary, market_local_date, trading_sessions
from src.platform.persistence.json_safe import to_jsonable

logger = logging.getLogger(__name__)
INTRADAY_MAX_AGE = timedelta(minutes=10)


async def prepare(agent, context, run_id, *, deadline, group_size, concurrency):
    # Partition before legacy symbol-keyed builders, retaining batched quotes per market.
    groups = {}
    for stock in context.watchlist:
        key = (stock.market, stock.symbol if agent.name == "intraday_monitor" else "*")
        groups.setdefault(key, []).append(stock)
    batches = [stocks[i:i + group_size] for stocks in groups.values()
               for i in range(0, len(stocks), group_size)]
    semaphore = asyncio.Semaphore(concurrency)

    async def collect(stocks):
        scoped = scoped_context(context, stocks)
        scoped.config, scoped.portfolio = deepcopy(scoped.config), deepcopy(scoped.portfolio)
        scoped._research_collect = True
        captured = {}
        scoped._research_capture = captured.update
        try:
            async with asyncio.timeout(max(0, deadline - time.monotonic())):
                async with semaphore:
                    data = await agent.collect(scoped)
                    if not isinstance(data, dict):
                        raise TypeError("Session collector must return a mapping")
                    return scoped, data, None, datetime.now(timezone.utc)
        except Exception as exc:
            reason = "collection_timeout" if isinstance(exc, TimeoutError) else "collection_failed"
            logger.warning("Session collection incomplete: agent=%s market=%s targets=%s error=%s",
                           agent.name, stocks[0].market.value, len(stocks), type(exc).__name__)
            return scoped, captured, reason, datetime.now(timezone.utc)

    async def optional(operation, default):
        try:
            async with asyncio.timeout(max(0, min(3, deadline - time.monotonic()))):
                return await operation()
        except Exception as exc:
            logger.warning("Session enrichment unavailable: agent=%s error=%s", agent.name, type(exc).__name__)
            return default

    securities, evidence, snapshots, signals = [], [], {}, []
    context._research_gate_observations = {}
    openings = getattr(context, "_scheduled_premarket_openings", {})
    missed = {}
    context._missed_premarket = missed
    market_snapshots = {}
    collected = await asyncio.gather(*(collect(stocks) for stocks in batches))
    for stocks, (scoped, data, collection_error, collected_at) in zip(batches, collected, strict=True):
        if data.get("skip_reason"):
            continue
        opening = openings.get(stocks[0].market)
        if opening is not None and collected_at >= opening:
            for stock in stocks:
                missed[f"{stock.market.value}:{stock.symbol}"] = opening.date().isoformat()
            continue
        observed = collected_at if opening is not None else datetime.now(timezone.utc)
        packs = data.get("signal_packs") or {stocks[0].symbol: data.get("signal_pack")}
        for stock in stocks:
            pack = packs.get(stock.symbol) if isinstance(packs, dict) else None
            stock_key = f"{stock.market.value}:{stock.symbol}"
            if not hasattr(pack, "quote"):
                securities.append(Security(key=stock_key, name=stock.name))
                missing = {"unavailable": True}
                if collection_error:
                    missing["reason"] = collection_error
                elif pack is not None or not isinstance(packs, dict):
                    missing["reason"] = "invalid_signal_pack"
                evidence.append(Evidence(id=f"{stock_key}/missing", stock_key=stock_key, domain="market",
                    source="collection result", observed_at=observed, available_at=observed, content=missing))
                continue
            positions = [p for p in scoped.portfolio.all_positions if p.symbol == stock.symbol and p.market == stock.market]
            lifetime, quote_issue = session_quote_validity(agent.name, stock.market, pack.quote, observed)
            quote = to_jsonable(pack.quote) if lifetime is not None else None
            securities.append(Security(key=stock_key, name=stock.name,
                price=pack.quote.current_price if quote else None,
                quote_expires_at=observed + lifetime if lifetime is not None else None,
                held_quantity=sum(p.quantity for p in positions)))
            symbol_context = (data.get("symbol_contexts") or {}).get(stock.symbol) or data.get("symbol_context") or {}
            from src.modules.strategy.intraday_event_gate import adaptive_price_threshold
            rule_context = {"atr_pct": (pack.technical or {}).get("atr_pct"),
                "adaptive_price_threshold": adaptive_price_threshold((pack.technical or {}).get("atr_pct"), getattr(agent, "price_alert_threshold", 3)),
                "stop_loss_warning": getattr(agent, "stop_loss_warning", -5), "take_profit_warning": getattr(agent, "take_profit_warning", 10)}
            found = []
            if getattr(context, "_research_fast", False) and quote:
                found, observation, rule_context = inspect_changes(stock_key, pack, positions, observed, agent,
                    event_only=not getattr(context, "_research_force", False))
                found = [s for s in found if s.domain in getattr(context, "_research_specialists", ["technical", "events"])]
                signals.extend(found)
                context._research_gate_observations[stock_key] = observation
                if found and not symbol_context:
                    from src.modules.research.context_builder import ContextBuilder
                    enriched = await optional(lambda: ContextBuilder().build_symbol_contexts(
                        agent_name=agent.name, context=scoped, packs={stock.symbol: pack}, realtime_hours=6,
                        extended_hours=24, history_days=7, kline_days=60, persist_snapshot=False), {})
                    symbol_context = enriched.get("symbols", {}).get(stock.symbol) or {}
            if getattr(context, "_research_fast", False) and found and stock.market.value == "CN":
                if stock.market not in market_snapshots:
                    from src.modules.automation.daily_report import DailyReportAgent
                    market_snapshots[stock.market] = await optional(
                        lambda: DailyReportAgent()._fetch_index_for_market(stock.market), [])
                data["indices"] = market_snapshots[stock.market]
            index_market = MarketCode.US if agent.name == "premarket_outlook" else stock.market
            index_session = "daily_report" if agent.name == "premarket_outlook" else agent.name
            indices = to_jsonable([row for row in (data.get("indices") or data.get("us_indices") or [])
                                   if session_quote_validity(index_session, index_market, row, observed)[0] is not None])
            if indices:
                captured = datetime.now(timezone.utc)
                evidence.append(Evidence(id=f"{stock_key}/market-snapshot", stock_key=stock_key, domain="market",
                    source="PanWatch captured indices", observed_at=captured, available_at=captured, content={"indices": indices}))
            snapshots[stock_key] = to_jsonable(symbol_context)
            if agent.name == "daily_report":
                prior = await optional(lambda: prior_plan(stock_key, observed, context.config.settings.app_timezone), None)
                if prior:
                    evidence.append(Evidence(id=f"{stock_key}/prior-plan", stock_key=stock_key, domain="market",
                        source="saved premarket plan", observed_at=observed, available_at=observed, content={"prior_plan": prior}))
            news = (pack.news.items if pack.news else []) + (pack.events.items if pack.events else [])
            # Preserve useful legacy input semantics without importing its formatting prompt.
            analysis_context = {k: symbol_context.get(k) for k in ("relative_strength", "ta_verdict") if symbol_context.get(k)}
            if "ta_verdict" in analysis_context:
                prior = analysis_context["ta_verdict"]
                # Retain a dated market rating without recycling old account-limited prose.
                analysis_context["ta_verdict"] = {k: v for k, v in prior.items() if k in {"rating", "action_label", "date", "age_days", "days_ago"}}
            analysis_context["trading_styles"] = sorted({p.trading_style for p in positions})
            analysis_context["volatility_rules"] = {k: v for k, v in rule_context.items() if k != "position_warnings"}
            analysis_context["position_alerts"] = sorted({str(value).split(":")[-1] for value in rule_context.get("position_warnings", [])})
            enriched_events = {item.get("url"): item for item in (symbol_context.get("events") or []) if isinstance(item, dict) and item.get("url")}
            news = [{**item, **enriched_events.get(item.get("url"), {})} for item in news if isinstance(item, dict)]
            news = [item for item in news if (t := published(item, observed, stock.market.value)) is None or t <= observed]
            if not getattr(context, "_research_fast", False) and any(material(item) for item in news):
                signals.append(ResearchSignal(stock_key=stock_key, domain="events", reasons=["material_captured_event"], observed_at=observed, material=True))
            domains = {
                "technical": {"quote": quote, "indicators": pack.technical, "missing": pack.missing, "analysis_context": analysis_context},
                "flow": {"capital_flow": pack.capital_flow, "indices": indices,
                         "unsupported_or_missing": pack.capital_flow is None},
                "events": {"items": sorted(news, key=lambda item: not material(item))[:10], "captured_count": len(news)},
            }
            if quote_issue:
                domains["technical"]["quote_unavailable"] = {
                    "reason": quote_issue, "source": getattr(pack.quote, "source", None),
                    "quote_date": getattr(pack.quote, "quote_date", None),
                    "source_timestamp": getattr(pack.quote, "source_timestamp", None),
                }
            for domain, payload in domains.items():
                payload["sources"] = pack.sources
                full = to_jsonable(payload)
                token = hashlib.sha256(json.dumps(full, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]
                common = dict(stock_key=stock_key, domain=domain, source="PanWatch captured sources",
                              observed_at=observed, available_at=observed)
                # Fixed evidence is supplied up front, with bounded document excerpts.
                preview = _preview(full)
                evidence.append(Evidence(id=f"{stock_key}/{domain}/{token}/summary", content=preview, **common))
    if not securities:
        if not missed:
            return None, snapshots
        # Keep an all-late poll auditable as skipped for its original session.
        securities = [Security(key=key) for key in missed]
    snapshot = to_jsonable(context.portfolio)
    portfolio = {"accounts": [{"available_funds": account.get("available_funds")} for account in snapshot.get("accounts", [])],
                 "buying_power_verified": snapshot.get("buying_power_verified")}
    portfolio["research_rules"] = {"stop_loss_warning": getattr(agent, "stop_loss_warning", -5), "take_profit_warning": getattr(agent, "take_profit_warning", 10)}
    portfolio["validation_scope"] = "Provided account snapshot only; currency and settlement buying power unverified"
    prepared = ResearchInput(run_id=run_id, as_of=datetime.now(timezone.utc), securities=securities,
                             evidence=evidence, portfolio=portfolio, signals=signals, language=context.report_language)
    return prepared, snapshots


def _preview(value):
    if isinstance(value, str):
        return value if len(value) <= 1200 else value[:1200] + " [excerpt]"
    if isinstance(value, dict):
        return {k: _preview(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_preview(v) for v in value]
    return value


async def prior_plan(key, cutoff, app_timezone):
    """Read a plan available by the cutoff on the security's own exchange date."""
    from sqlalchemy import func, select
    from zoneinfo import ZoneInfo
    from src.platform.persistence.models import AnalysisHistory
    from src.platform.scheduling.trading_calendar import premarket_plan_window
    from .models import ResearchDelivery, ResearchRun

    market = key.split(":", 1)[0]
    cutoff = cutoff.astimezone(timezone.utc)
    close = exchange_session_boundary(market, cutoff)
    if close is None:
        return None
    day = close.date().isoformat()
    start, opening = premarket_plan_window(market, close)
    # Older portfolio histories used the application date of the last update.
    app_zone = ZoneInfo(app_timezone)

    def read(db):
        # Another market's report can replace the portfolio history row for this date.
        # Committed research deliveries retain the original plan and its availability.
        proposal = ResearchDelivery.payload["raw_data"]["suggestions"][key]
        target_day = ResearchDelivery.payload["raw_data"]["market_dates"][market].as_string()
        as_of = func.julianday(ResearchRun.as_of)
        rows = db.execute(select(ResearchRun.as_of, target_day, proposal).select_from(ResearchRun)
            .join(ResearchDelivery, ResearchRun.id == ResearchDelivery.run_id).where(
            ResearchRun.agent_name == "premarket_outlook", ResearchDelivery.status == "committed",
            as_of <= func.julianday(cutoff), ResearchRun.finished_at <= cutoff,
            (target_day == day) | (target_day.is_(None) & as_of.between(func.julianday(start), func.julianday(opening))),
            proposal["action"].as_string().is_not(None)).order_by(ResearchRun.finished_at.desc(), ResearchRun.id.desc()))
        for timestamp, exchange_date, plan in rows:
            # SQLite julianday rounds to milliseconds. Preserve the final pre-open microseconds.
            captured = datetime.fromisoformat(timestamp)
            if captured.tzinfo is None:
                captured = captured.replace(tzinfo=timezone.utc)
            if captured <= cutoff and (exchange_date == day or start <= captured < opening):
                return plan

        proposal = AnalysisHistory.raw_data["suggestions"][key]
        recorded_day = AnalysisHistory.raw_data["market_dates"][market].as_string()
        rows = db.execute(select(AnalysisHistory.analysis_date, AnalysisHistory.updated_at, recorded_day, proposal).where(
            AnalysisHistory.agent_name == "premarket_outlook", AnalysisHistory.created_at <= cutoff,
            AnalysisHistory.updated_at <= cutoff, proposal["action"].as_string().is_not(None),
            (recorded_day == day) | (recorded_day.is_(None)
                                    & (AnalysisHistory.updated_at >= start) & (AnalysisHistory.updated_at < opening)),
        ).order_by(AnalysisHistory.updated_at.desc(), AnalysisHistory.id.desc()))
        for stored_date, updated_at, exchange_date, suggestion in rows:
            legacy_date = updated_at.replace(tzinfo=timezone.utc).astimezone(app_zone).date().isoformat()
            if exchange_date == day or stored_date in {day, legacy_date}:
                return suggestion
        return None

    plan = await run_db_operation(repository.engine, read)
    if isinstance(plan, dict):
        return {"analysis_date": day, **{name: plan[name] for name in ("outlook", "action", "triggers", "invalidations") if name in plan}}
    return None


def research_date(agent_name, market, instant):
    if agent_name == "intraday_monitor":
        return market_local_date(market, instant)
    boundary = exchange_session_boundary(market, instant, next_open=agent_name == "premarket_outlook")
    return boundary.date() if boundary else None


def session_quote_validity(agent_name: str, market: MarketCode, quote: StockData | IndexData | dict | None,
                           observed: datetime) -> tuple[timedelta | None, str | None]:
    """Return the usable research lifetime or the reason the quote was rejected."""
    if quote is None:
        return None, "quote_missing"
    if isinstance(quote, dict):
        price = quote.get("current_price", quote.get("current"))
        source_time, source_day = quote.get("source_timestamp"), quote.get("quote_date")
        try:
            if isinstance(source_time, str):
                source_time = datetime.fromisoformat(source_time.replace("Z", "+00:00"))
            if isinstance(source_day, str):
                source_day = date.fromisoformat(source_day)
        except ValueError:
            return None, "quote_invalid_time"
    else:
        price = getattr(quote, "current_price", None)
        source_time = getattr(quote, "source_timestamp", None)
        source_day = getattr(quote, "quote_date", None)
    if isinstance(price, bool) or not isinstance(price, (int, float)) or not math.isfinite(price) or price <= 0:
        return None, "quote_invalid_price"
    zone = MARKETS[market].get_tz()
    local = observed.astimezone(zone)
    if source_time is not None:
        if not isinstance(source_time, datetime) or source_time.tzinfo is None or source_time > observed:
            return None, "quote_invalid_time"
        source_time = source_time.astimezone(zone)
        if source_day is not None and source_time.date() != source_day:
            return None, "quote_invalid_time"
        source_day = source_time.date()
    if not isinstance(source_day, date):
        return None, "quote_time_unknown"
    if not calendar_known(market, local):
        return None, "quote_calendar_unknown"
    sessions = trading_sessions(market, local)
    if agent_name == "intraday_monitor":
        active = next((s for s in sessions if s.start <= local.time() <= s.end), None)
        if active is None:
            return None, "quote_market_closed"
        if source_day != local.date():
            return None, "quote_wrong_session"
        if source_time is None:
            return None, "quote_time_unknown"
        age = observed - source_time
        if source_time.time() < active.start or age >= INTRADAY_MAX_AGE:
            return None, "quote_delayed"
        return min(timedelta(minutes=1), INTRADAY_MAX_AGE - age), None

    # A timestamped same-day pre-open price is an indicative quote. A date
    # alone could still refer to the previous close before the market opens.
    if (agent_name == "premarket_outlook" and sessions and local.time() < sessions[0].start
            and source_time is not None and source_day == local.date()):
        remaining = timedelta(minutes=15) - (observed - source_time)
        if remaining <= timedelta(0):
            return None, "quote_delayed"
        opening = datetime.combine(local.date(), sessions[0].start, zone)
        return min(remaining, opening - local), None
    if agent_name == "premarket_outlook" and research_date(agent_name, market, observed) is None:
        return None, "quote_calendar_unknown"
    close = exchange_session_boundary(market, observed)
    if close is None:
        return None, "quote_calendar_unknown"
    if source_day != close.date():
        return None, "quote_wrong_session"
    if source_time is not None and source_time < close:
        return None, "quote_before_close"
    return timedelta(minutes=15), None
