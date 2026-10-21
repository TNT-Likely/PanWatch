"""Intraday signal gate -> scoped experts -> one proposal -> mandatory rule checks."""
from langgraph.graph import END, START, StateGraph
from langgraph.types import Send

from .contracts import ResearchInput, ResearchSignal, RoleReport


def build_fast_graph(engine, state_schema, context_schema):
    graph = StateGraph(state_schema, context_schema=context_schema)

    async def gate(state, runtime):
        deps = runtime.context
        signals = [s for s in deps.prepared.signals if s.domain in deps.policy.specialists]
        if deps.policy.trigger_mode == "always":
            signals = [ResearchSignal(stock_key=s.key, domain=role, reasons=["explicit_manual_review"],
                observed_at=deps.prepared.as_of) for s in deps.prepared.securities for role in deps.policy.specialists]
        keys = {s.stock_key for s in signals}
        if not keys and any(s.price is None for s in deps.prepared.securities):
            raise ValueError("No usable quote or research signal; data is unavailable, not unchanged")
        en = deps.prepared.language == "en-US"
        summary = (f"New signals on {len(keys)} securities; run only matching specialists." if keys else "No material change; no model call or formal recommendation.") if en else (
            f"{len(keys)}只股票出现新变化，仅运行对应专家。" if keys else "未发现实质变化，未调用模型，也不生成占位操作建议。")
        result = engine._store(state, "gate", RoleReport(summary=summary), "", deps, kind="rule")
        await deps.emit("gate", "done" if keys else "skipped", securities=len(keys))
        prepared = deps.prepared.model_copy(update={
            "securities": [s for s in deps.prepared.securities if s.key in keys],
            "evidence": [e for e in deps.prepared.evidence if e.stock_key in keys | {"*"}],
            "signals": signals,
        })
        return {**result, "skipped": not keys, "prepared": prepared.model_dump(mode="json")}

    def dispatch(state):
        return {}

    def sends(state, runtime):
        prepared = ResearchInput.model_validate(state["prepared"])
        jobs = []
        size = runtime.context.policy.max_group_size
        for index in range(0, len(prepared.securities), size):
            group = prepared.securities[index:index + size]
            for role in runtime.context.policy.specialists:
                keys = {s.stock_key for s in prepared.signals if s.domain == role}
                stocks = [s for s in group if s.key in keys]
                if not stocks:
                    continue
                keys = {s.key for s in stocks}
                scoped = prepared.model_copy(update={"securities": stocks,
                    "evidence": [e for e in prepared.evidence if e.stock_key in keys | {"*"}],
                    "signals": [s for s in prepared.signals if s.stock_key in keys]})
                jobs.append(Send("expert", {"prepared": scoped.model_dump(mode="json"),
                    "group": str(index // size), "role": role, "epoch": 0, "messages": [], "rounds": 0}))
        return jobs

    async def expert(state, runtime):
        result = await engine.expert_graph.ainvoke(state, context=runtime.context)
        return {"artifacts": result["artifacts"]}

    async def strategy(state, runtime):
        deps = runtime.context
        prepared = ResearchInput.model_validate(state["prepared"])
        reports = {k: {field: value for field, value in a.items() if field != "evidence"}
                   for k, a in state["artifacts"].items() if a["role"] in deps.policy.specialists}
        from .engine import compact_input
        payload = {"input": compact_input(prepared), "reports": reports, "plan": []}
        report, model = await engine._report("strategy", payload, deps, prepared)
        deps.plan = report.recommendations
        return {**engine._store(state, "strategy", report, model, deps),
                "plan": [r.model_dump(mode="json") for r in report.recommendations]}

    async def rules(state, runtime):
        prepared = ResearchInput.model_validate(state["prepared"])
        result = engine.execution_rules(state, runtime.context, prepared)
        await runtime.context.emit("execution", "done", kind="rule")
        return result

    graph.add_node("gate", gate)
    graph.add_node("dispatch", dispatch)
    graph.add_node("expert", expert)
    graph.add_node("strategy", strategy)
    graph.add_node("execution", rules)
    graph.add_edge(START, "gate")
    graph.add_conditional_edges("gate", lambda s: END if s["skipped"] else "dispatch", [END, "dispatch"])
    graph.add_conditional_edges("dispatch", sends, ["expert"])
    graph.add_edge("expert", "strategy")
    graph.add_edge("strategy", "execution")
    graph.add_edge("execution", END)
    return graph.compile()
