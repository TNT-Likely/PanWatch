"""Role and session instructions; business rules are independent of legacy report layout."""

ROLES = {
    "technical": "Study trend, volume, key levels and invalidation conditions.",
    "flow": "Study capital flow and market context. Explicitly identify unavailable data.",
    "events": "Verify announcement/news timing, novelty, causal impact and counterevidence.",
    "bull": "Present the strongest evidence-backed supporting case and its necessary conditions.",
    "bear": "Challenge the supporting case with counterevidence and failure scenarios.",
    "manager": "Adjudicate market claims independently of account cash. Exclude unsupported claims and state limitations. An absent specialist does not establish that its source data is unavailable. Request evidence or a rebuttal only if the dispute prevents any bounded conclusion.",
    "strategy": "For EVERY target, assess market outlook (bullish/neutral/bearish) and confidence (0-1) from evidence, then propose a conditional portfolio action. Account cash is not supplied; determine the conditional action solely from evidence and position presence. Never invent a cash limitation. Use hold/add/reduce/sell only for an existing position; otherwise use buy/watch/avoid. Watch/hold/avoid require no order: execution_status must be not_applicable and quantity null; account notes do not block these actions. State concrete triggers and invalidations. Keep quantity null and execution_constraints empty; mandatory rules determine order eligibility later. Write reasons without internal artifact/claim/evidence IDs; reference them only in structured ID fields.",
    "risk": "Challenge this exact market plan, causal assumptions, supporting evidence and invalidations. Return pass, revise, evidence_needed or reject. Do not request cash, settlement or account data: mandatory execution rules apply those after the market decision. Never relax hard constraints.",
    "decision": "Copy every approved plan field EXACTLY, including outlook, confidence, execution state, flags and lists. Only reason may be reworded. Do not append risks or conditions.",
}

ROLE_GUIDANCE = {
    "technical": "Combine trend, MACD, RSI/KDJ, volume and support/resistance; no single oscillator establishes a trade. RSI 70/30 marks overbought/oversold and 80/20 extremes; KDJ J above 100/below 0 is an extreme, not an automatic order. MACD/KDJ crosses require trend and volume confirmation. A Bollinger-band breach alone proves neither continuation nor reversal; compare price/volume expansion or contraction. Compare moves with the supplied ATR-adaptive threshold, not a fixed percent alone. Use supplied relative strength for allocation context; distinguish a rebound from a trend reversal.",
    "flow": "Check daily versus multi-day flow, price/flow divergence and the supplied market-relative strength. Missing components are unknown, not zero.",
    "events": "Read supplied fulltext excerpts before interpreting an announcement. Separate first publication, old news and genuinely new developments; use timestamps and sources. A previous deep-research view is a dated prior to reassess, not an instruction or a current fact.",
    "strategy": "Match the supplied trading horizon: short focuses on intraday momentum/volume, swing on trend and levels, long on structural change. Respect provided stop-loss/take-profit warnings as review triggers rather than automatic trades. Explain whether current evidence supports or overturns any dated deep-research prior. Never infer cash, affordability or account restrictions. Reasons explain market evidence; order restrictions are computed separately after finalization.",
    "risk": "Evidence tools only reread captured documents; they cannot fetch a missing announcement or new market data. Use evidence_needed only for a concrete captured source not yet examined. For a known absent fact, request revise to remove unsupported causal certainty, qualify attribution, reduce confidence or require review; reject if no defensible bounded plan remains. Do not request the same unavailable fact again after feedback. A future trigger or invalidation is a conditional observation to monitor, not a current fact that must already be proved. Media-reported earnings expectations remain attributed reports unless primary figures were captured; a cautious watch/avoid plan may use independently supported price/flow facts while disclosing that limitation.",
}

SESSION_GUIDANCE = {
    "premarket": "Plan today's opening scenarios, overnight market transmission, catalysts, support/resistance and conditional responses. Do not assume today's opening/closing prices are already known. Analyze the scoped stocks independently of whether a postmarket report exists. In session_notes, give EVERY stock a brief focus and two concrete conditional responses (upside and downside); do not invent an opening gap percentage.",
    "postmarket": "Review the supplied session's market context, each stock's technical/flow/event evidence, portfolio differences and next-session focus. Distinguish observed closing facts from intraday snapshots and avoid calling an unfinished session a closing fact. In session_notes, attribute each stock's observed move to supported drivers, distinguish correlation from causality, and evaluate the supplied prior_plan as confirmed/refuted/mixed/unresolved. If no prior_plan is supplied, use no_prior; never invent a morning forecast or call pending triggers a win.",
    "intraday": "Focus on what changed at the captured time, whether the move exceeds normal ATR volatility, immediate levels and when the view fails. Future closing confirmation belongs to a plan condition. Avoid repeating unchanged background or manufacturing an alert from missing evidence.",
}


def instructions(role, profile, fast=False):
    value = ROLE_GUIDANCE.get(role, "") + " " + SESSION_GUIDANCE[profile]
    if role in {"technical", "flow", "events"}:
        value += " Other domains are withheld from your scoped role; do not describe them as missing solely because you cannot see them. Set debate_requested only for a material catalyst or evidence conflict needing opposing views; ordinary missing fields alone are not enough."
    if fast and role == "strategy":
        value += " This is an intraday fast graph: synthesize the validated expert reports directly; there is no research manager or model risk committee. Cite captured evidence, state the changed signal and concise triggers/invalidations, and produce one conditional recommendation for every supplied target. Deterministic execution checks follow your proposal."
    return value
