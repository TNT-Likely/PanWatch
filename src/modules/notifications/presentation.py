"""Recover stock identity for new and legacy single-stock Agent notices."""
import re

from src.platform.persistence.models import Stock


def agent_notification_params(db, runs):
    params = {}
    for run in runs:
        identity = {"agent_name": run.agent_name}
        # Batch traces have no symbol. Keep hyphens in symbols such as BRK-B.
        match = re.fullmatch(r"(?:ta|man-[a-z_]+|auto-[a-z_]+)-(.+)-\d{10,}", run.trace_id or "")
        if match:
            identity["stock_symbol"] = match[1]
        params[run.id] = identity
    symbols = {value["stock_symbol"] for value in params.values() if value.get("stock_symbol")}
    names = {}
    if symbols:
        for symbol, name in db.query(Stock.symbol, Stock.name).filter(Stock.symbol.in_(symbols)):
            names.setdefault(symbol, []).append(name)
    for identity in params.values():
        matches = names.get(identity.get("stock_symbol"), [])
        if len(matches) == 1 and matches[0]:
            identity["stock_name"] = matches[0]
    return params
