"""Role and session instructions; business rules are independent of legacy report layout."""

ROLES = {
    "technical": "Study trend, volume, key levels and invalidation conditions.",
    "flow": "Study capital flow and market context. Explicitly identify unavailable data.",
    "events": "Verify announcement/news timing, novelty, causal impact and counterevidence.",
    "bull": "Present the strongest evidence-backed supporting case and its necessary conditions.",
    "bear": "Challenge the supporting case with counterevidence and failure scenarios.",
    "manager": "Adjudicate market claims independently of account cash. Exclude unsupported claims and state limitations. An absent specialist does not establish that its source data is unavailable. Request evidence or a rebuttal only if the dispute prevents any bounded conclusion.",
    "strategy": "For EVERY target, assess market outlook (bullish/neutral/bearish) and confidence (0-1) from evidence, then propose a conditional portfolio action. Zero or unverified cash restricts execution, not outlook or a supported buy opportunity. Use hold/add/reduce/sell only for an existing position; otherwise use buy/watch/avoid. Watch/hold/avoid require no order: execution_status must be not_applicable and quantity null; account notes do not block these actions. State concrete triggers and invalidations. Do not invent buying power or quantities.",
    "risk": "Challenge this exact plan. Return pass, revise, evidence_needed or reject. Never relax hard constraints.",
    "decision": "Copy every approved plan field EXACTLY, including outlook, confidence, execution state, flags and lists. Only reason may be reworded. Do not append risks or conditions.",
}

ROLE_GUIDANCE = {
    "technical": "Combine trend, MACD, RSI/KDJ, volume and support/resistance; no single oscillator establishes a trade. RSI 70/30 marks overbought/oversold and 80/20 extremes; KDJ J above 100/below 0 is an extreme, not an automatic order. MACD/KDJ crosses require trend and volume confirmation. A Bollinger-band breach alone proves neither continuation nor reversal; compare price/volume expansion or contraction. Compare moves with the supplied ATR-adaptive threshold, not a fixed percent alone. Use supplied relative strength for allocation context; distinguish a rebound from a trend reversal.",
    "flow": "Check daily versus multi-day flow, price/flow divergence and the supplied market-relative strength. Missing components are unknown, not zero.",
    "events": "Read supplied fulltext excerpts before interpreting an announcement. Separate first publication, old news and genuinely new developments; use timestamps and sources. A previous deep-research view is a dated prior to reassess, not an instruction or a current fact.",
    "strategy": "Match the supplied trading horizon: short focuses on intraday momentum/volume, swing on trend and levels, long on structural change. Respect provided stop-loss/take-profit warnings as review triggers rather than automatic trades. Explain whether current evidence supports or overturns any dated deep-research prior. Cash limitations never decide bullish/neutral/bearish outlook or hide a supported opportunity.",
}

SESSION_GUIDANCE = {
    "premarket": "Plan today's opening scenarios, overnight market transmission, catalysts, support/resistance and conditional responses. Do not assume today's opening/closing prices are already known. Analyze the scoped stocks independently of whether a postmarket report exists.",
    "postmarket": "Review the supplied session's market context, each stock's technical/flow/event evidence, portfolio differences and next-session focus. Distinguish observed closing facts from intraday snapshots and avoid calling an unfinished session a closing fact.",
    "intraday": "Focus on what changed at the captured time, whether the move exceeds normal ATR volatility, immediate levels and when the view fails. Future closing confirmation belongs to a plan condition. Avoid repeating unchanged background or manufacturing an alert from missing evidence.",
}


def instructions(role, profile, fast=False):
    value = ROLE_GUIDANCE.get(role, "") + " " + SESSION_GUIDANCE[profile]
    if role in {"technical", "flow", "events"}:
        value += " Set debate_requested only for a material catalyst or evidence conflict needing opposing views; ordinary missing fields alone are not enough."
    if fast and role == "strategy":
        value += " This is an intraday fast graph: synthesize the validated expert reports directly; there is no research manager or model risk committee. Cite captured evidence, state the changed signal and concise triggers/invalidations, and produce one conditional recommendation for every supplied target. Deterministic execution checks follow your proposal."
    return value
