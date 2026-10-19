# Pan Investment Research

[简体中文](README.zh-CN.md)

A standalone Python package for bounded, multi-agent investment research using
LangGraph. Inspired by TradingAgents, it combines three specialist analysts,
supporting/opposing researchers, a research manager, a strategist, a risk
challenger and a final decision role. Roles can share one configured model.

## Package boundary

The package depends on Pydantic and LangGraph. It does not import PanWatch, open
a database, select credentials, send notifications or place orders. The host
supplies a model port, optional read-only tools and an event sink. It receives
typed recommendations and immutable, versioned research artifacts.

Three compiled graphs separate investment decisions, group research and expert
tool loops. Specialists run concurrently; each worker returns only after its
child graph finishes. A material dispute can request targeted evidence or a
rebuttal. Risk can return a plan for revision. All feedback shares one quota.

## Install and use

```sh
python -m pip install ./packages/pan-investment-research
python -m pytest packages/pan-investment-research/tests -q
```

```python
from pan_investment_research import InvestmentResearch, ResearchInput, ResearchPolicy

result = await InvestmentResearch().run(
    ResearchInput.model_validate(prepared_input),
    model=my_model_adapter,
    tools=my_read_only_tools,
    events=my_event_sink,
    policy=ResearchPolicy(),
)
```

See `ResearchModel`, `ResearchTools` and `ResearchEvents` in `ports.py`.
**Every provider attempt, including fallbacks and retries, must await the
supplied `before_attempt` callback immediately before sending.** Model SDK
automatic retries must be disabled. The adapter returns usage from that exact
response, not shared mutable counters. Missing or uncertain usage stays unknown.

Inputs use `market:symbol` keys and timezone-aware timestamps. Evidence cannot
cross the research scope or arrive after the declared analysis cutoff. Model
claims must cite visible evidence. Tools are allowlisted per specialist, may
only return scoped evidence, and cannot change an existing evidence identity.
Documents are treated as data, never as agent instructions.

## Limits and results

One complete research group needs nine logical calls before tools, repair and
feedback. Multiple groups need separate budgets; strategy/risk/decision run once
for the combined portfolio. Request attempts, estimated token reservations,
tool calls, feedback, concurrency, output size and time are bounded. Estimated
tokens are conservative preflight accounting, not measured billing.

`ready` means the produced plan passed the package's implemented checks. It is
not a promise of profitability, complete exchange-rule validation or executable
buying power. Missing settlement/currency facts cannot authorize quantities.
The final decision cannot change the risk-approved plan. An unresolved objection
or exhausted budget returns `review` with explicit, non-actionable watch results.
Infrastructure exceptions propagate to the host; cancellation is not swallowed.

The host owns fresh account/market constraints, persistence, idempotent delivery,
notification policy and durable recovery. Checkpoint recovery is not enabled by
default. Package tests use scripted models and replay evidence; they do not
demonstrate live model quality or trading returns.
