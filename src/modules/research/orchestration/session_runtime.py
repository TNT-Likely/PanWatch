"""Run bounded groups independently and retain captured facts when research cannot finish."""
import asyncio
from dataclasses import dataclass

from openai import APIError
from pan_investment_research import InvestmentResearch, ResearchResult
from pan_investment_research.engine import constrain_execution

from .gateway import ResearchProviderError


def provider_failure(exc):
    cause = exc.__cause__ or exc
    status = getattr(cause, "status_code", None)
    if status in {401, 403}:
        return "provider_auth"
    if status == 429:
        return "provider_rate_limit"
    if status == 400:
        return "provider_request"
    return "model_unavailable"


@dataclass
class Events:
    group: str
    publish_event: object

    async def publish(self, event):
        await self.publish_event({**event, "group": self.group})


async def run_groups(prepared, policy, model, budget, *, publish_event, completed, save_group):
    engine = InvestmentResearch()
    size = policy.max_group_size
    batches = [prepared.securities[i:i + size] for i in range(0, len(prepared.securities), size)]

    async def run_batch(index, stocks):
        usable = [stock for stock in stocks if stock.price is not None]
        missing = {stock.key: "data_unavailable" for stock in stocks if stock.price is None}
        if not usable:
            return None, missing
        group = str(index) if len(batches) > 1 else "portfolio"
        if group in completed:
            saved = ResearchResult.model_validate(completed[group])
            saved.recommendations = constrain_execution(prepared, saved.recommendations)
            for artifact in saved.artifacts:
                if artifact.role == "execution" and saved.recommendations:
                    artifact.report.summary += (
                        " Order restrictions were rechecked on recovery." if prepared.language == "en-US"
                        else "恢复运行时已重新核对执行条件。")
            return saved, missing
        try:
            output = await engine.run(
                prepared.for_stocks(usable), policy=policy, model=model, budget=budget,
                group=str(index) if len(batches) > 1 else None,
                events=Events(group, publish_event), finalize_plan=True,
            )
        except (APIError, ResearchProviderError) as exc:
            code = provider_failure(exc)
            await publish_event({"role": "run", "group": group, "status": "failed", "reason": code})
            return None, {**missing, **{stock.key: code for stock in usable}}
        if output.completion in {"complete", "skipped"}:
            await save_group(group, output, budget.snapshot())
        else:
            missing.update({stock.key: output.completion for stock in usable})
        return output, missing

    tasks = [asyncio.create_task(run_batch(index, stocks)) for index, stocks in enumerate(batches)]
    try:
        groups = await asyncio.gather(*tasks)
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    results = [result for result, _ in groups if result is not None]
    failures = {key: error for _, missing in groups for key, error in missing.items()}
    recommendations = [item for result in results for item in result.recommendations]
    if not any(stock.price is not None for stock in prepared.securities):
        completion, status = "data_unavailable", "review"
    elif failures:
        completion, status = "degraded", "review"
        recommendations = [item.model_copy(update={
            "review_required": True, "quantity": None, "execution_status": "blocked",
            "execution_constraints": list(dict.fromkeys([*item.execution_constraints, "research_incomplete"])),
        }) for item in recommendations]
    elif all(result.completion == "skipped" for result in results):
        completion, status = "skipped", "skipped"
    else:
        completion = "complete"
        status = "review" if any(result.status == "review" for result in results) else "ready"
    return ResearchResult(
        run_id=prepared.run_id, graph=policy.graph, status=status, completion=completion,
        recommendations=recommendations, artifacts=[a for result in results for a in result.artifacts],
        reason=next((result.reason for result in results if result.completion not in {"complete", "skipped"}), ""),
        group_failures=failures, usage=budget.snapshot(),
    )
