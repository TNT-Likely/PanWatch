"""Scope-isolated graph execution and single-owner result delivery for all three sessions."""
from datetime import datetime, timedelta, timezone
from copy import copy, deepcopy
import asyncio
import hashlib
import json
import uuid
import time
import logging
from zoneinfo import ZoneInfo

from pan_investment_research import ResearchInput, ResearchPolicy
from pan_investment_research.budget import Budget

from src.modules.automation.base import AnalysisResult
from src.modules.research.signals.actions import normalize_suggestion
from src.platform.observability.log_context import get_log_context
from src.platform.persistence.json_safe import to_jsonable
from . import repository
from .evidence import prepare
from .reporting import build_report
from .gateway import ResearchModelGateway
from .policies import SESSION_TIMEOUTS, validate_config
from .session_runtime import run_groups

logger = logging.getLogger(__name__)


def scope_policy(policy, config, targets):
    """Size default capacities for target coverage, retaining explicit global caps."""
    groups = (targets + policy.max_group_size - 1) // policy.max_group_size
    updates = {}
    if "max_attempts" not in config:
        updates["max_attempts"] = min(100, policy.max_attempts + 6 * (groups - 1))
    defaults = {
        "max_tools": min(30, policy.max_tools * groups),
        "max_total_tokens": min(1000000, policy.max_total_tokens * targets),
        "max_input_chars": min(250000, policy.max_input_chars + 12000 * (targets - 1)),
        "max_output_tokens": policy.max_output_tokens,
    }
    updates.update({key: value for key, value in defaults.items() if key not in config})
    return ResearchPolicy.model_validate({**policy.model_dump(), **updates})


async def deliver(run_id, result, targets):
    result.raw_data.setdefault("notified", False)
    for key, notifier in targets:
        if await repository.claim(run_id, key):
            try:
                outcome = await asyncio.wait_for(notifier.notify_with_result(result.title, result.content), 10)
            except Exception:
                outcome = {"success": False, "error": "Notification acknowledgement unknown"}
            await repository.sent(run_id, key, outcome)
            result.raw_data["notified"] |= bool(outcome.get("success"))


async def execute(agent, context, config):
    started = time.monotonic()
    mode, policy = validate_config(config, agent.name)
    keys = sorted(f"{s.market.value}:{s.symbol}" for s in context.watchlist)
    scope = hashlib.sha256(json.dumps(keys).encode()).hexdigest()
    trace = getattr(context, "_trace_id", None) or get_log_context().get("trace_id") or str(uuid.uuid4())
    run_id = hashlib.sha256(f"{trace}/{agent.name}/{scope}/{mode}".encode()).hexdigest()
    policy = scope_policy(policy, config, max(1, len(keys)))
    duration = SESSION_TIMEOUTS[agent.name]
    if policy.budget_enabled:
        duration = min(duration, policy.deadline_seconds)
    deadline = started + duration
    owner = uuid.uuid4().hex
    signature = hashlib.sha256(json.dumps({
        "version": 1, "policy": policy.model_dump(), "language": context.report_language,
        "timezone": context.config.settings.app_timezone, "portfolio": to_jsonable(context.portfolio),
        "model": getattr(context.ai_client, "model", None), "endpoint": getattr(context.ai_client, "base_url", None),
        "models": [label for _, label in getattr(context.ai_client, "candidates", [])],
    }, sort_keys=True).encode()).hexdigest()
    prior = await repository.start(run_id, trace, agent.name, scope, mode, owner=owner,
                                   signature=signature, lease_seconds=duration + 30) or {}
    run_id = prior.get("run_id", run_id)
    targets = context.notifier.delivery_targets() if hasattr(context.notifier, "delivery_targets") else [("configured", context.notifier)]
    can_notify = not getattr(context, "suppress_notify", False) and not (
        context.notify_policy and context.notify_policy.is_quiet_now())
    if "delivery" in prior:
        result = AnalysisResult(agent.name, **prior["delivery"])
        if can_notify:
            await deliver(run_id, result, targets)
        return result
    if prior.get("busy") or prior.get("ended"):
        return AnalysisResult(agent.name, agent.display_name, "Research run already claimed", raw_data={"skipped": True})
    try:
        checkpoint = prior.get("checkpoint", {})
        context = copy(context)
        context.config, context.portfolio = deepcopy(context.config), deepcopy(context.portfolio)
        context._research_shadow = False
        context._research_fast = policy.graph == "intraday_fast"
        context._research_specialists = policy.specialists
        context._research_force = policy.trigger_mode == "always"
        prepared = ResearchInput.model_validate(checkpoint["prepared"]) if checkpoint.get("prepared") else None
        zone = ZoneInfo(context.config.settings.app_timezone)
        now = datetime.now(timezone.utc)
        if (prepared is None or now - prepared.as_of > timedelta(seconds=SESSION_TIMEOUTS[agent.name] * 3)
                or now.astimezone(zone).date() != prepared.as_of.astimezone(zone).date()):
            checkpoint = {}
            prepared, snapshots = await prepare(agent, context, run_id, deadline=started + duration * .65,
                group_size=policy.max_group_size, concurrency=policy.concurrency)
            if prepared is not None:
                await repository.checkpoint(run_id, owner, {
                    "$.prepared": prepared.model_dump(mode="json"), "$.snapshots": snapshots, "$.groups": {},
                    "$.usage": {}, "$.observations": context._research_gate_observations,
                })
        else:
            snapshots = checkpoint.get("snapshots", {})
            context._research_gate_observations = checkpoint.get("observations", {})
        if prepared is None:
            await repository.fail(run_id, "No eligible targets", owner=owner)
            return AnalysisResult(agent.name, agent.display_name, "No eligible targets", raw_data={"skipped": True})

        async def publish(event):
            await repository.progress(run_id, event, owner=owner)

        async def save_group(group, output, usage):
            await repository.checkpoint(run_id, owner, {
                '$.groups.' + json.dumps(group): output.model_dump(mode="json"), "$.usage": usage,
            })

        budget = Budget(policy, deadline=deadline - min(5, duration * .1), usage=checkpoint.get("usage"))
        research = await run_groups(prepared, policy, ResearchModelGateway(context.ai_client), budget,
            publish_event=publish, completed=checkpoint.get("groups", {}), save_group=save_group)
        result = present(agent, context, prepared, research, elapsed_seconds=time.monotonic() - started)
        agent.localize_result_title(result, context)
        notify = research.completion != "skipped" and can_notify
        if agent.name == "intraday_monitor":
            notify = notify and result.raw_data["should_alert"]
        ttl = (0 if getattr(agent, "bypass_throttle", False) else agent.throttle_minutes) if agent.name == "intraday_monitor" else agent._notify_dedupe_ttl_minutes(context)
        status = await repository.commit(run_id, agent, prepared, research, result, snapshots,
                                        [key for key, _ in targets], notify, ttl, owner=owner)
        if status != "stale" and research.completion in {"complete", "skipped"}:
            from .signals import acknowledge
            try:
                await asyncio.to_thread(acknowledge, getattr(context, "_research_gate_observations", {}))
            except OSError:
                # Delivery is already committed; a cache failure must not erase its audit result.
                logger.warning("Intraday change baseline could not be saved; next run may reanalyse")
                await repository.progress(run_id, {"role": "gate", "status": "warning", "baseline_saved": False})
        result.raw_data["notified"] = False
        if status == "stale":
            result.raw_data["notify_skipped"] = "stale_input"
            return result
        if notify:
            await deliver(run_id, result, targets)
        if not notify or not targets:
            result.raw_data["notify_skipped"] = "policy" if not notify else "no_channels"
        return result
    except asyncio.CancelledError:
        await asyncio.shield(repository.fail(run_id, "cancelled", owner=owner))
        raise
    except Exception as exc:
        await repository.fail(run_id, type(exc).__name__, owner=owner)
        raise


def present(agent, context, prepared, research, *, elapsed_seconds=None):
    title = f"【{agent.display_name}】" + ", ".join(s.name or s.key for s in prepared.securities)
    report = build_report(agent.name, research.model_dump(mode="json"),
        securities=[s.model_dump(mode="json") for s in prepared.securities],
        evidence=[e.model_dump(mode="json") for e in prepared.evidence], as_of=prepared.as_of.isoformat(),
        signals=[s.model_dump(mode="json") for s in prepared.signals],
        language=context.report_language, timezone=context.config.settings.app_timezone, elapsed_seconds=elapsed_seconds)
    suggestions = {rec.stock_key: normalize_suggestion(rec.model_dump(), agent_name=agent.name)
                   for rec in research.recommendations} if research.completion == "complete" else {}
    raw = {"engine": "langgraph", "research_run_id": prepared.run_id, "result_status": research.status,
        "completion": research.completion, "graph": research.graph, "skipped": research.completion == "skipped", "research_reason": research.reason, "research_report": report,
        "drafts": [r.model_dump() for r in research.recommendations] if research.completion != "complete" else [],
        "suggestions": suggestions, "symbols": [s.key.split(":", 1)[1] for s in prepared.securities],
        "scope": [s.key for s in prepared.securities],
        "analysis_date": prepared.as_of.astimezone(ZoneInfo(context.config.settings.app_timezone)).date().isoformat(),
        "usage": research.usage, "should_alert": research.completion not in {"complete", "skipped"} or any(s["should_alert"] for s in suggestions.values())}
    return AnalysisResult(agent_name=agent.name, title=title, content=report["content"], raw_data=raw)
