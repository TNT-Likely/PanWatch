"""Convert existing collection output into scoped, timestamped package inputs."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json

from pan_investment_research import Evidence, ResearchInput, ResearchSignal, Security
from .signals import inspect_changes, published, material
from . import repository
from src.platform.persistence.worker import run_db_operation

from src.modules.automation.scheduling_policy import scoped_context
from src.platform.persistence.json_safe import to_jsonable


async def prepare(agent, context, run_id):
    # Partition before legacy symbol-keyed builders, retaining batched quotes per market.
    groups = {}
    for stock in context.watchlist:
        key = (stock.market, stock.symbol if agent.name == "intraday_monitor" else "*")
        groups.setdefault(key, []).append(stock)
    securities, evidence, details, snapshots, signals = [], [], [], {}, []
    context._research_gate_observations = {}
    market_snapshots = {}
    for stocks in groups.values():
        scoped = scoped_context(context, stocks)
        scoped.config = deepcopy(scoped.config)
        scoped.portfolio = deepcopy(scoped.portfolio)
        scoped._research_collect = True
        data = await agent.collect(scoped)
        if data.get("skip_reason"):
            continue
        observed = datetime.now(timezone.utc)
        packs = data.get("signal_packs") or {stocks[0].symbol: data.get("signal_pack")}
        for stock in stocks:
            pack = packs.get(stock.symbol)
            stock_key = f"{stock.market.value}:{stock.symbol}"
            if not pack:
                securities.append(Security(key=stock_key, name=stock.name))
                evidence.append(Evidence(id=f"{stock_key}/missing", stock_key=stock_key, domain="market",
                    source="collection result", observed_at=observed, available_at=observed, content={"unavailable": True}))
                continue
            positions = [p for p in scoped.portfolio.all_positions if p.symbol == stock.symbol and p.market == stock.market]
            quote = to_jsonable(pack.quote) if pack.quote else None
            expiry = observed + timedelta(seconds=60 if agent.name == "intraday_monitor" else 900)
            securities.append(Security(key=stock_key, name=stock.name,
                price=getattr(pack.quote, "current_price", None) or None,
                quote_expires_at=expiry if quote else None,
                held_quantity=sum(p.quantity for p in positions)))
            symbol_context = (data.get("symbol_contexts") or {}).get(stock.symbol) or data.get("symbol_context") or {}
            from src.modules.strategy.intraday_event_gate import adaptive_price_threshold
            rule_context = {"atr_pct": (pack.technical or {}).get("atr_pct"),
                "adaptive_price_threshold": adaptive_price_threshold((pack.technical or {}).get("atr_pct"), getattr(agent, "price_alert_threshold", 3)),
                "stop_loss_warning": getattr(agent, "stop_loss_warning", -5), "take_profit_warning": getattr(agent, "take_profit_warning", 10)}
            if getattr(context, "_research_fast", False):
                found, observation, rule_context = inspect_changes(stock_key, pack, positions, observed, agent,
                    event_only=not getattr(context, "_research_force", False))
                found = [s for s in found if s.domain in getattr(context, "_research_specialists", ["technical", "events"])]
                signals.extend(found)
                context._research_gate_observations[stock_key] = observation
                if found and not symbol_context:
                    from src.modules.research.context_builder import ContextBuilder
                    enriched = await ContextBuilder().build_symbol_contexts(agent_name=agent.name, context=scoped,
                        packs={stock.symbol: pack}, realtime_hours=6, extended_hours=24, history_days=7,
                        kline_days=60, persist_snapshot=False)
                    symbol_context = enriched.get("symbols", {}).get(stock.symbol) or {}
            if getattr(context, "_research_fast", False) and found and stock.market.value == "CN":
                if stock.market not in market_snapshots:
                    from src.modules.automation.daily_report import DailyReportAgent
                    try:
                        market_snapshots[stock.market] = await DailyReportAgent()._fetch_index_for_market(stock.market)
                    except Exception:
                        market_snapshots[stock.market] = []
                data["indices"] = market_snapshots[stock.market]
            indices = to_jsonable(data.get("indices") or data.get("us_indices") or [])
            if indices:
                captured = datetime.now(timezone.utc)
                evidence.append(Evidence(id=f"{stock_key}/market-snapshot", stock_key=stock_key, domain="market",
                    source="PanWatch captured indices", observed_at=captured, available_at=captured, content={"indices": indices}))
            snapshots[stock_key] = to_jsonable(symbol_context)
            if agent.name == "daily_report":
                prior = await prior_plan(stock_key, observed, context.config.settings.app_timezone)
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
                "flow": {"capital_flow": pack.capital_flow, "indices": data.get("indices") or data.get("us_indices"),
                         "unsupported_or_missing": pack.capital_flow is None},
                "events": {"items": news},
            }
            for domain, payload in domains.items():
                payload["sources"] = pack.sources
                full = to_jsonable(payload)
                token = hashlib.sha256(json.dumps(full, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]
                common = dict(stock_key=stock_key, domain=domain, source="PanWatch captured sources",
                              observed_at=observed, available_at=observed)
                details.append(Evidence(id=f"{stock_key}/{domain}/{token}/detail", content=full, **common))
                # Preserve all source identities; limit long document bodies, not target coverage.
                preview = _preview(full)
                evidence.append(Evidence(id=f"{stock_key}/{domain}/{token}/summary", content=preview, **common))
    if not securities or not any(e.domain in {"technical", "flow", "events"} for e in evidence):
        return None, details, snapshots
    portfolio = to_jsonable(context.portfolio)
    portfolio["research_rules"] = {"stop_loss_warning": getattr(agent, "stop_loss_warning", -5), "take_profit_warning": getattr(agent, "take_profit_warning", 10)}
    portfolio["validation_scope"] = "Provided account snapshot only; currency and settlement buying power unverified"
    prepared = ResearchInput(run_id=run_id, as_of=datetime.now(timezone.utc), securities=securities,
                             evidence=evidence, portfolio=portfolio, signals=signals, language=context.report_language)
    return prepared, details, snapshots


def _preview(value):
    if isinstance(value, str):
        return value if len(value) <= 1200 else value[:1200] + " [details available via read-only tool]"
    if isinstance(value, dict):
        return {k: _preview(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_preview(v) for v in value]
    return value


async def prior_plan(key, cutoff, app_timezone):
    """Only same-market, same-day market fields; no prior account narrative reaches models."""
    from sqlalchemy import select
    from zoneinfo import ZoneInfo
    from src.platform.persistence.models import AnalysisHistory
    day = cutoff.astimezone(ZoneInfo(app_timezone)).date().isoformat()
    def read(db):
        rows = db.scalars(select(AnalysisHistory).where(
            AnalysisHistory.agent_name == "premarket_outlook", AnalysisHistory.analysis_date == day,
            AnalysisHistory.created_at <= cutoff.replace(tzinfo=None),
            AnalysisHistory.updated_at <= cutoff.replace(tzinfo=None)).order_by(AnalysisHistory.updated_at.desc(), AnalysisHistory.created_at.desc())).all()
        for row in rows:
            raw = row.raw_data if isinstance(row.raw_data, dict) else {}
            rec = (raw.get("suggestions") or {}).get(key)
            if not isinstance(rec, dict):
                continue
            return {"analysis_date": day, **{name: rec[name] for name in ("outlook", "action", "triggers", "invalidations") if name in rec}}
        return None
    return await run_db_operation(repository.engine, read)
