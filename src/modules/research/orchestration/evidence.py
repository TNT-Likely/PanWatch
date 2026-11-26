"""Convert existing collection output into scoped, timestamped package inputs."""
from copy import deepcopy
from datetime import datetime, timezone
import asyncio
import hashlib
import json
import logging
import time

from pan_investment_research import Evidence, ResearchInput, ResearchSignal, Security
from .signals import inspect_changes, published, material
from .quotes import session_quote_validity
from . import repository
from src.platform.persistence.worker import run_db_operation

from src.modules.automation.scheduling_policy import scoped_context
from src.platform.marketdata.models import MarketCode
from src.platform.persistence.json_safe import to_jsonable

logger = logging.getLogger(__name__)


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
                    return scoped, data, None
        except Exception as exc:
            reason = "collection_timeout" if isinstance(exc, TimeoutError) else "collection_failed"
            logger.warning("Session collection incomplete: agent=%s market=%s targets=%s error=%s",
                           agent.name, stocks[0].market.value, len(stocks), type(exc).__name__)
            return scoped, captured, reason

    async def optional(operation, default):
        try:
            async with asyncio.timeout(max(0, min(3, deadline - time.monotonic()))):
                return await operation()
        except Exception as exc:
            logger.warning("Session enrichment unavailable: agent=%s error=%s", agent.name, type(exc).__name__)
            return default

    securities, evidence, snapshots, signals = [], [], {}, []
    context._research_gate_observations = {}
    market_snapshots = {}
    collected = await asyncio.gather(*(collect(stocks) for stocks in batches))
    for stocks, (scoped, data, collection_error) in zip(batches, collected, strict=True):
        if data.get("skip_reason"):
            continue
        observed = datetime.now(timezone.utc)
        packs = data.get("signal_packs") or {stocks[0].symbol: data.get("signal_pack")}
        for stock in stocks:
            pack = packs.get(stock.symbol)
            stock_key = f"{stock.market.value}:{stock.symbol}"
            if not pack:
                securities.append(Security(key=stock_key, name=stock.name))
                missing = {"unavailable": True}
                if collection_error:
                    missing["reason"] = collection_error
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
            news = [{**item, **enriched_events.get(item.get("url"), {})} for item in news]
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
        return None, snapshots
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
    from src.platform.scheduling.trading_calendar import exchange_session_boundary, local_day_bounds
    from .models import ResearchDelivery, ResearchRun

    market = key.split(":", 1)[0]
    cutoff = cutoff.astimezone(timezone.utc)
    close = exchange_session_boundary(market, cutoff)
    if close is None:
        return None
    day = close.date().isoformat()
    start, end = local_day_bounds(market, close)
    # Older portfolio histories used the application date, including mixed-market reports.
    app_zone = ZoneInfo(app_timezone)
    history_dates = {day, start.astimezone(app_zone).date().isoformat(), end.astimezone(app_zone).date().isoformat()}

    def read(db):
        # Another market's report can replace the portfolio history row for this date.
        # Committed research deliveries retain the original plan and its availability.
        proposal = ResearchDelivery.payload["raw_data"]["suggestions"][key]
        target_day = ResearchDelivery.payload["raw_data"]["market_dates"][market].as_string()
        as_of = func.julianday(ResearchRun.as_of)
        plan = db.scalar(select(proposal).join(ResearchRun, ResearchRun.id == ResearchDelivery.run_id).where(
            ResearchRun.agent_name == "premarket_outlook", ResearchDelivery.status == "committed",
            as_of <= func.julianday(cutoff), ResearchRun.finished_at <= cutoff,
            (target_day == day) | (target_day.is_(None) & (as_of >= func.julianday(start)) & (as_of < func.julianday(end))),
            proposal["action"].as_string().is_not(None)).order_by(ResearchRun.finished_at.desc(), ResearchRun.id.desc()).limit(1))
        if isinstance(plan, dict):
            return plan

        proposal = AnalysisHistory.raw_data["suggestions"][key]
        recorded_day = AnalysisHistory.raw_data["market_dates"][market].as_string()
        rows = db.execute(select(AnalysisHistory.analysis_date, AnalysisHistory.updated_at, recorded_day, proposal).where(
            AnalysisHistory.agent_name == "premarket_outlook", AnalysisHistory.created_at <= cutoff,
            AnalysisHistory.updated_at <= cutoff, proposal["action"].as_string().is_not(None),
            (recorded_day == day) | (recorded_day.is_(None) & AnalysisHistory.analysis_date.in_(history_dates)
                                    & (AnalysisHistory.updated_at >= start) & (AnalysisHistory.updated_at < end)),
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
