"""Convert existing collection output into scoped, timestamped package inputs."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json

from pan_investment_research import Evidence, ResearchInput, Security

from src.modules.automation.scheduling_policy import scoped_context
from src.platform.persistence.json_safe import to_jsonable


async def prepare(agent, context, run_id):
    # Partition before legacy symbol-keyed builders, retaining batched quotes per market.
    groups = {}
    for stock in context.watchlist:
        key = (stock.market, stock.symbol if agent.name == "intraday_monitor" else "*")
        groups.setdefault(key, []).append(stock)
    securities, evidence, details, snapshots = [], [], [], {}
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
            snapshots[stock_key] = to_jsonable(symbol_context)
            news = (pack.news.items if pack.news else []) + (pack.events.items if pack.events else [])
            domains = {
                "technical": {"quote": quote, "indicators": pack.technical, "missing": pack.missing},
                "flow": {"capital_flow": pack.capital_flow, "indices": data.get("indices") or data.get("us_indices"),
                         "unsupported_or_missing": pack.capital_flow is None},
                "events": {"items": news, "historical_context": {
                    "daily": data.get("daily_analysis"), "premarket": data.get("premarket_analysis")}},
            }
            if agent.name == "intraday_monitor" and agent.event_only and pack.quote and not getattr(context, "_research_shadow", False):
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
    portfolio["validation_scope"] = "Provided account snapshot only; currency and settlement buying power unverified"
    prepared = ResearchInput(run_id=run_id, as_of=datetime.now(timezone.utc), securities=securities,
                             evidence=evidence, portfolio=portfolio, language=context.report_language)
    return prepared, details, snapshots


def _preview(value):
    if isinstance(value, str):
        return value if len(value) <= 1200 else value[:1200] + " [details available via read-only tool]"
    if isinstance(value, dict):
        return {k: _preview(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_preview(v) for v in value]
    return value
