"""Currency-safe research inputs for portfolios containing Canadian listings.

Amounts stay in their listing currency until a dated, bounded reference FX rate
is available. Legacy synthetic USD/HKD defaults are never used in this path.
"""
from __future__ import annotations

import math

from src.platform.marketdata.instrument_identity import instrument_get, market_value
from src.platform.marketdata import reference_fx

CANADIAN_MARKETS = {"TSX", "TSXV"}


def has_canadian_positions(portfolio) -> bool:
    return any(market_value(p.market) in CANADIAN_MARKETS
               for account in getattr(portfolio, "accounts", []) for p in account.positions)


def quote_currency(market, quote=None):
    code = market_value(market)
    if code not in CANADIAN_MARKETS:
        return {"CN": "CNY", "HK": "HKD", "US": "USD"}.get(code)
    currency = quote.get("currency") if isinstance(quote, dict) else getattr(quote, "currency", None)
    return currency if currency in {"CAD", "USD"} else None


def _reference_fx(currency, *, refresh):
    # Share the verified expiry/source-date contract with portfolio valuation;
    # never use the legacy USD/HKD synthetic fallback caches.
    cache = {"CAD": reference_fx.CAD_RATE_CACHE, "USD": reference_fx.USD_RATE_CACHE,
             "HKD": reference_fx.HKD_RATE_CACHE}[currency]
    rate = reference_fx.reference_cny_rate(currency, cache, refresh=refresh)
    if rate is None or not math.isfinite(rate) or rate <= 0:
        return None, None
    return rate, cache.get("source_date")


def build_portfolio_valuation(portfolio, packs=None, *, refresh=False):
    """One snapshot per context build; at most one bounded lookup per currency."""
    packs = packs or {}
    fx = {"CNY": {"rate": 1.0, "source": "identity", "source_date": None}}
    accounts, positions = [], {}
    for account in getattr(portfolio, "accounts", []):
        account_cost = 0.0
        complete = True
        for pos in account.positions:
            code = market_value(pos.market)
            pack = instrument_get(packs, pos.symbol, pos.market)
            quote = getattr(pack, "quote", None)
            currency = quote_currency(code, quote)
            if currency and currency not in fx:
                rate, source_date = _reference_fx(currency, refresh=refresh)
                fx[currency] = {"rate": rate, "source": "Frankfurter/ECB", "source_date": source_date}
            rate = fx.get(currency, {}).get("rate")
            native_cost = pos.cost_price * pos.quantity
            converted = native_cost * rate if rate is not None else None
            complete &= converted is not None
            if converted is not None:
                account_cost += converted
            key = f"{code}:{pos.symbol}"
            row = positions.setdefault(key, {"currency": currency, "native_cost": 0.0,
                "cost_cny": 0.0 if converted is not None else None, "exchange_rate": rate})
            row["native_cost"] += native_cost
            if row["cost_cny"] is not None and converted is not None:
                row["cost_cny"] += converted
            else:
                row["cost_cny"] = None
        accounts.append({"id": account.id, "name": account.name,
            "available_funds": account.available_funds, "cash_currency": "CNY",
            "cost_cny": account_cost if complete else None, "valuation_incomplete": not complete})
    complete = all(not account["valuation_incomplete"] for account in accounts)
    return {"accounts": accounts, "positions": positions, "fx": fx,
        "total_cost_cny": sum(account["cost_cny"] for account in accounts) if complete else None,
        "valuation_incomplete": not complete, "cash_currency": "CNY"}


def amount_text(amount, currency):
    if amount is None:
        return "不可用"
    return f"{amount:.2f} {currency or '币种未知（不可换算）'}"


def constraints_text(constraints):
    ratio = constraints.get("single_position_ratio")
    ratio_text = "不可用（币种或已验证汇率缺失）" if ratio is None else f"{ratio * 100:.1f}%"
    return (f"- 资金约束：总可用 {constraints.get('total_available_funds', 0):.0f} CNY，"
            f"单票仓位占比 {ratio_text}（{constraints.get('risk_budget_hint', 'unavailable')}）")


def canadian_quote_note(market, symbol, quote=None):
    if market_value(market) not in CANADIAN_MARKETS:
        return ""
    def get(key):
        return quote.get(key) if isinstance(quote, dict) else getattr(quote, key, None)
    stamp = get("source_timestamp")
    stamp = stamp.isoformat() if hasattr(stamp, "isoformat") else stamp
    delay = {True: "是", False: "否"}.get(get("delayed"), "未知")
    return (f"- 标的：{market_value(market)}:{symbol}；报价币种 {quote_currency(market, quote) or '未知'}；"
            f"来源 {get('source') or '不可用'}；数据时点 {stamp or '不可用'}；供应商延迟 {delay}。"
            "以下价格与成本为原币种，不能视为当前实时行情；账户现金为 CNY，缺少已验证汇率时不可比较或估算购买力。")


def portfolio_overview_lines(valuation):
    lines = []
    for account in valuation["accounts"]:
        lines.append(f"- {account['name']}: 持仓成本 {amount_text(account['cost_cny'], 'CNY')} "
                     f"可用资金 {amount_text(account['available_funds'], 'CNY')}")
    lines.append(f"- 合计: 总持仓成本 {amount_text(valuation['total_cost_cny'], 'CNY')}")
    if valuation["valuation_incomplete"]:
        lines.append("- 组合估值不完整：币种或已验证汇率缺失，不推断完整仓位占比、风险预算或购买力。")
    for currency, item in valuation["fx"].items():
        if currency != "CNY" and item["rate"] is not None:
            lines.append(f"- 参考汇率：{currency}/CNY {item['rate']}；来源 {item['source']}；日期 {item['source_date'] or '不可用'}")
    return lines
