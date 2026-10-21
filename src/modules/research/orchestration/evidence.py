"""Convert existing collection output into scoped, timestamped package inputs."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json

from pan_investment_research import Evidence, ResearchInput, ResearchSignal, Security
from .signals import inspect_changes, published, material

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
            snapshots[stock_key] = to_jsonable(symbol_context)
            news = (pack.news.items if pack.news else []) + (pack.events.items if pack.events else [])
            # Preserve useful legacy input semantics without importing its formatting prompt.
            analysis_context = {k: symbol_context.get(k) for k in ("relative_strength", "ta_verdict") if symbol_context.get(k)}
            analysis_context["trading_styles"] = sorted({p.trading_style for p in positions})
            analysis_context["volatility_rules"] = {k: v for k, v in rule_context.items() if k != "position_warnings"}
            enriched_events = {item.get("url"): item for item in (symbol_context.get("events") or []) if isinstance(item, dict) and item.get("url")}
            news = [{**item, **enriched_events.get(item.get("url"), {})} for item in news]
            news = [item for item in news if (t := published(item, observed, stock.market.value)) is None or t <= observed]
            if not getattr(context, "_research_fast", False) and any(material(item) for item in news):
                signals.append(ResearchSignal(stock_key=stock_key, domain="events", reasons=["material_captured_event"], observed_at=observed, material=True))
            domains = {
                "technical": {"quote": quote, "indicators": pack.technical, "missing": pack.missing, "analysis_context": analysis_context},
                "flow": {"capital_flow": pack.capital_flow, "indices": data.get("indices") or data.get("us_indices"),
                         "unsupported_or_missing": pack.capital_flow is None},
                "events": {"items": news, "historical_context": {
                    "daily": None if getattr(context, "_research_fast", False) else data.get("daily_analysis"), "premarket": None if getattr(context, "_research_fast", False) else data.get("premarket_analysis")}},
            }
            if agent.name == "intraday_monitor" and not getattr(context, "_research_fast", False) and agent.event_only and pack.quote and not getattr(context, "_research_shadow", False):
                from src.modules.strategy.intraday_event_gate import check_and_update
                gate = check_and_update(symbol=stock_key, change_pct=pack.quote.change_pct,
                    volume_ratio=(pack.technical or {}).get("volume_ratio"), kline_summary=pack.technical,
                    price_threshold=agent.price_alert_threshold, volume_threshold=agent.volume_alert_ratio)
                domains["technical"]["event_gate"] = {"reasons": gate.reasons, "should_analyze": gate.should_analyze}
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
    if not securities:
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
