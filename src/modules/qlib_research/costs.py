"""The same explicit aggregate fee convention as Qlib's Exchange.

Research fees are configurable assumptions, not a promise of broker tariffs.
No implicit old-engine slippage, taxes or rank-based sizing is applied.
"""

import math
from src.platform.execution.cost_model import Fill


class QlibCostModel:
    def __init__(self, config):
        self.config = config

    def fill(self, side, price, quantity):
        if side not in ("buy", "sell") or not math.isfinite(price) or price <= 0 or quantity <= 0:
            raise ValueError("qlib_fill_invalid")
        gross = price * quantity
        cost = max(gross * self.config["open_cost" if side == "buy" else "close_cost"], self.config["min_cost"])
        return Fill(side, price, price, quantity, gross, cost, 0, 0, 0, cost, cost,
                    -(gross + cost) if side == "buy" else gross - cost)
