"""Qlib adapter for the paper allocator, not a second portfolio strategy.

Close-time ranks/quantities/caps are frozen before the next open. Qlib supplies
the executor, account, fee aggregation and reporting. Daily bars cannot certify
opening quote timeliness, liquidity or historical corporate-action correctness.
"""

import copy
import math
import re

if __package__:
    from .allocation import (
        TARGET_RATIO,
        allocate_targets,
        buy_quantity,
        target_membership,
    )
else:
    from allocation import (
        TARGET_RATIO,
        allocate_targets,
        buy_quantity,
        target_membership,
    )


def strategy_for(signal, config, panel):
    import pandas as pd
    from qlib.contrib.strategy.signal_strategy import BaseSignalStrategy
    from qlib.backtest.decision import Order, TradeDecisionWO

    bars = {(bar["date"], bar["symbol"]): bar for bar in panel["bars"]}
    meta = {m["symbol"]: m for m in panel["members"]}

    class MarketPoolStrategy(BaseSignalStrategy):
        def __init__(self):
            super().__init__(signal=signal, risk_degree=TARGET_RATIO)
            self.order_count = 0
            self.rejected_count = 0

        def generate_trade_decision(self, execute_result=None):
            step = self.trade_calendar.get_trade_step()
            start, end = self.trade_calendar.get_step_time(step)
            prior_start, prior_end = self.trade_calendar.get_step_time(step, shift=1)
            score = self.signal.get_signal(start_time=prior_start, end_time=prior_end)
            if isinstance(score, pd.DataFrame):
                score = score.iloc[:, 0]
            if score is None:
                return TradeDecisionWO([], self)
            today, prior = start.strftime("%Y-%m-%d"), prior_start.strftime("%Y-%m-%d")
            scores = sorted(
                (
                    (str(s), float(v))
                    for s, v in score.items()
                    if math.isfinite(float(v))
                ),
                key=lambda item: (-item[1], item[0]),
            )
            rows = [dict(symbol=s, rank=i + 1) for i, (s, _) in enumerate(scores)]
            held = self.trade_position.get_stock_list()
            candidates = set(held) | {r["symbol"] for r in rows}
            factors = {
                s: self.trade_exchange.get_factor(s, prior_start, prior_end)
                for s in held
            }
            if any(
                not factors[s]
                or not math.isfinite(factors[s])
                or (prior, s) not in bars
                for s in held
            ):
                self.rejected_count += 1
                return TradeDecisionWO([], self)
            holdings = {
                s: int(round(self.trade_position.get_stock_amount(s) * factors[s]))
                for s in held
            }
            prices = {
                s: bars[(prior, s)]["close"] for s in candidates if (prior, s) in bars
            }
            eligible = {
                s
                for s in candidates
                if re.fullmatch(r"(?:SH60|SZ00)\d{4}", s)
                and not meta.get(s, {}).get("special_treatment")
                and (prior, s) in bars
                and bars[(prior, s)]["volume"] > 0
                and (
                    s not in held
                    or self.trade_position.get_stock_count(s, bar="day") >= 1
                )
            }
            sells, buys = target_membership(rows, holdings, eligible, config["topk"])
            try:
                plan = allocate_targets(
                    self.trade_position.get_cash(),
                    holdings,
                    holdings,
                    prices,
                    sells,
                    buys,
                    eligible,
                    config,
                )
            except ValueError:
                self.rejected_count += 1
                return TradeDecisionWO([], self)
            temp = copy.deepcopy(self.trade_position)
            current_factors = {
                s: self.trade_exchange.get_factor(s, start, end) for s in candidates
            }
            opening = {
                s: b["open"]
                for s in candidates
                if (b := bars.get((today, s)))
                and b["open"] > 0
                and math.isfinite(b["open"])
            }
            orders = []
            for frozen in plan["orders"]:
                symbol, side = frozen["symbol"], frozen["side"]
                bar = bars.get((today, symbol))
                factor = current_factors.get(symbol)
                price = opening.get(symbol)
                # Never replace a missing raw open with that day's close.
                if (
                    not bar
                    or not factor
                    or not math.isfinite(factor)
                    or not price
                    or bar["volume"] <= 0
                    or abs(price / prices[symbol] - 1) >= config["limit_threshold"]
                ):
                    self.rejected_count += 1
                    continue
                quantity = frozen["estimated_quantity"]
                if side == "buy":
                    current = temp.get_stock_list()
                    if any(
                        s not in opening
                        or not current_factors.get(s)
                        or not math.isfinite(current_factors[s])
                        for s in current
                    ):
                        self.rejected_count += 1
                        continue
                    values = {
                        s: temp.get_stock_amount(s) * current_factors[s] * opening[s]
                        for s in current
                    }
                    equity = temp.get_cash() + sum(values.values())
                    budget = max(
                        0.0,
                        min(
                            temp.get_cash(),
                            frozen["cash_limit"],
                            equity * TARGET_RATIO - sum(values.values()),
                            frozen["target_weight"] * equity - values.get(symbol, 0),
                        ),
                    )
                    quantity = buy_quantity(budget, price, config, maximum=quantity)
                if quantity <= 0:
                    self.rejected_count += 1
                    continue
                order = Order(
                    stock_id=symbol,
                    amount=quantity / factor,
                    start_time=start,
                    end_time=end,
                    direction=Order.BUY if side == "buy" else Order.SELL,
                )
                if self.trade_exchange.check_order(order):
                    self.trade_exchange.deal_order(order, position=temp)
                    if order.deal_amount > 0:
                        orders.append(order)
                        continue
                self.rejected_count += 1
            self.order_count += len(orders)
            return TradeDecisionWO(orders, self)

    return MarketPoolStrategy()
