"""Scope-isolated graph execution and single-owner result delivery for all three sessions."""
from datetime import datetime
from copy import copy, deepcopy
import asyncio
import hashlib
import json
import uuid
import time
import logging

from pan_investment_research import InvestmentResearch, ResearchPolicy

from src.modules.automation.base import AnalysisResult
from src.modules.research.signals.actions import normalize_suggestion
from src.platform.observability.log_context import get_log_context
from . import repository
from .evidence import prepare
from .reporting import build_report
from .gateway import EvidenceTools, ResearchModelGateway

from .policies import PROFILES, validate_config

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
        "max_output_tokens": min(8000, policy.max_output_tokens + 768 * (targets - 1)),
    }
    updates.update({key: value for key, value in defaults.items() if key not in config})
    return ResearchPolicy.model_validate({**policy.model_dump(), **updates})


class Events:
    def __init__(self, run_id):
        self.run_id = run_id

    async def publish(self, event):
        await repository.progress(self.run_id, event)


async def execute(agent, context, config):
    mode, policy = validate_config(config, agent.name)
    keys = sorted(f"{s.market.value}:{s.symbol}" for s in context.watchlist)
    scope = hashlib.sha256(json.dumps(keys).encode()).hexdigest()
    trace = getattr(context, "_trace_id", None) or get_log_context().get("trace_id") or str(uuid.uuid4())
    run_id = hashlib.sha256(f"{trace}/{agent.name}/{scope}/{mode}".encode()).hexdigest()
    prior = await repository.start(run_id, trace, agent.name, scope, mode)
    if prior is not None:
        if prior.get("busy"):
            return AnalysisResult(agent.name, agent.display_name, "Research already running", raw_data={"skipped": True})
        return AnalysisResult(agent.name, **prior)
    try:
        started = time.monotonic()
        context = copy(context)
        context.config, context.portfolio = deepcopy(context.config), deepcopy(context.portfolio)
        context._research_shadow = False
        context._research_fast = policy.graph == "intraday_fast"
        context._research_specialists = policy.specialists
        context._research_force = policy.trigger_mode == "always"
        prepared, details, snapshots = await asyncio.wait_for(prepare(agent, context, run_id), policy.deadline_seconds)
        if prepared is None:
            await repository.fail(run_id, "No eligible targets")
            return AnalysisResult(agent.name, agent.display_name, "No eligible targets", raw_data={"skipped": True})
        # Input groups get an explicit run budget, preserving every target.
        policy = scope_policy(policy, config, len(prepared.securities))
        remaining = policy.deadline_seconds - (time.monotonic() - started)
        if remaining <= 0:
            raise TimeoutError("Research preparation exhausted the deadline")
        policy = policy.model_copy(update={"deadline_seconds": remaining})
        research = await InvestmentResearch().run(prepared, model=ResearchModelGateway(context.ai_client),
            tools=EvidenceTools(details), events=Events(run_id), policy=policy)
        result = present(agent, context, prepared, research, elapsed_seconds=time.monotonic() - started)
        agent.localize_result_title(result, context)
        targets = context.notifier.delivery_targets() if hasattr(context.notifier, "delivery_targets") else [("configured", context.notifier)]
        notify = research.completion != "skipped" and not getattr(context, "suppress_notify", False)
        if context.notify_policy and context.notify_policy.is_quiet_now():
            notify = False
        if agent.name == "intraday_monitor":
            notify = notify and result.raw_data["should_alert"]
        ttl = (0 if getattr(agent, "bypass_throttle", False) else agent.throttle_minutes) if agent.name == "intraday_monitor" else agent._notify_dedupe_ttl_minutes(context)
        status = await repository.commit(run_id, agent, prepared, research, result, snapshots,
                                        [key for key, _ in targets], notify, ttl)
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
        for key, notifier in targets:
            if await repository.claim(run_id, key):
                try:
                    outcome = await notifier.notify_with_result(result.title, result.content)
                except Exception:
                    outcome = {"success": False, "error": "Notification acknowledgement unknown"}
                await repository.sent(run_id, key, outcome)
                result.raw_data["notified"] |= bool(outcome.get("success"))
        if not notify or not targets:
            result.raw_data["notify_skipped"] = "policy" if not notify else "no_channels"
        return result
    except asyncio.CancelledError:
        await asyncio.shield(repository.fail(run_id, "cancelled"))
        raise
    except Exception as exc:
        await repository.fail(run_id, type(exc).__name__)
        raise


def present(agent, context, prepared, research, *, elapsed_seconds=None):
    title = f"【{agent.display_name}】" + ", ".join(s.name or s.key for s in prepared.securities)
    report = build_report(agent.name, research.model_dump(mode="json"),
        securities=[s.model_dump(mode="json") for s in prepared.securities],
        evidence=[e.model_dump(mode="json") for e in prepared.evidence], as_of=prepared.as_of.isoformat(),
        language=context.report_language, timezone=context.config.settings.app_timezone, elapsed_seconds=elapsed_seconds)
    suggestions = {rec.stock_key: normalize_suggestion(rec.model_dump(), agent_name=agent.name)
                   for rec in research.recommendations} if research.completion == "complete" else {}
    raw = {"engine": "langgraph", "research_run_id": prepared.run_id, "result_status": research.status,
        "completion": research.completion, "graph": research.graph, "skipped": research.completion == "skipped", "research_reason": research.reason, "research_report": report,
        "drafts": [r.model_dump() for r in research.recommendations] if research.completion != "complete" else [],
        "suggestions": suggestions, "symbols": [s.key.split(":", 1)[1] for s in prepared.securities],
        "scope": [s.key for s in prepared.securities], "analysis_date": datetime.now().date().isoformat(),
        "usage": research.usage, "should_alert": research.completion not in {"complete", "skipped"} or any(s["should_alert"] for s in suggestions.values())}
    return AnalysisResult(agent_name=agent.name, title=title, content=report["content"], raw_data=raw)
