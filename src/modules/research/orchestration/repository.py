"""Worker-owned transactions for research audit, business output and notification claims."""
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import uuid
from copy import deepcopy

from sqlalchemy import update
from sqlalchemy.dialects.sqlite import insert

from src.modules.automation.suggestion_pool import save_suggestion, AGENT_EXPIRY_HOURS
from src.modules.research.analysis_history import save_analysis
from src.modules.research.context_store import save_agent_context_run, save_agent_prediction_outcome
from src.modules.research.signals.actions import normalize_suggestion
from src.platform.notifications.notify_dedupe import build_notify_dedupe_key
from src.platform.persistence.database import engine
from src.platform.persistence.worker import run_db_operation
from .models import ResearchRun, ResearchArtifact, ResearchDelivery, ResearchOutbox


async def transaction(operation):
    def apply(db):
        with db.begin():
            return operation(db)
    return await run_db_operation(engine, apply)


async def start(run_id, trace, agent, scope, mode):
    def apply(db):
        inserted = db.execute(insert(ResearchRun).values(id=run_id, trace_id=trace, agent_name=agent,
            scope_hash=scope, engine=mode, as_of=datetime.now(timezone.utc).isoformat(), status="running")
            .on_conflict_do_nothing(index_elements=["id"])).rowcount
        if not inserted:
            delivery = db.get(ResearchDelivery, run_id)
            if not delivery:
                return {"busy": True}
            payload = deepcopy(delivery.payload)
            rows = db.query(ResearchOutbox).filter(ResearchOutbox.run_id == run_id).all()
            payload["raw_data"]["notified"] = any(row.status == "sent" for row in rows)
            return payload
        return None
    return await transaction(apply)


async def progress(run_id, event):
    def apply(db):
        row = db.get(ResearchRun, run_id)
        stages = dict(row.progress or {})
        stages[event["role"]] = event
        row.progress = stages
    await transaction(apply)


async def fail(run_id, reason):
    def apply(db):
        row = db.get(ResearchRun, run_id)
        row.status, row.payload = "failed", {"error": reason[:1000]}
        row.finished_at = datetime.now(timezone.utc)
    await transaction(apply)


async def commit(run_id, agent, prepared, research, result, snapshots, targets, notify, ttl_minutes):
    def apply(db):
        existing = db.get(ResearchDelivery, run_id)
        if existing:
            return existing.status
        run = db.get(ResearchRun, run_id)
        run.status, run.payload = "completed", research.model_dump(mode="json")
        run.finished_at = datetime.now(timezone.utc)
        run.as_of = prepared.as_of.isoformat()
        for artifact in research.artifacts:
            db.add(ResearchArtifact(run_id=run_id, artifact_id=artifact.id, payload=artifact.model_dump(mode="json")))
        newer = db.query(ResearchRun).filter(ResearchRun.agent_name == agent.name,
            ResearchRun.scope_hash == run.scope_hash, ResearchRun.as_of > run.as_of,
            ResearchRun.status == "completed", ResearchRun.engine == "langgraph").first()
        payload = {"title": result.title, "content": result.content, "raw_data": result.raw_data}
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        db.add(ResearchDelivery(run_id=run_id, payload_hash=digest, payload=payload, status="stale" if newer else "skipped" if research.completion == "skipped" else "committed"))
        db.flush()  # Unique run_id claims ALL business writes in the same transaction.
        if newer:
            return "stale"
        if research.completion == "skipped":
            return "skipped"
        day = result.raw_data["analysis_date"]
        names = {s.key: s.name for s in prepared.securities}
        for rec in research.recommendations if research.completion == "complete" else []:
            market, symbol = rec.stock_key.split(":", 1)
            suggestion = normalize_suggestion(rec.model_dump(), agent_name=agent.name)
            save_suggestion(symbol, names[rec.stock_key], rec.action, suggestion["action_label"], agent.name,
                reason=rec.reason, agent_label=agent.display_name, stock_market=market,
                ai_response=result.content, suggestion_state=suggestion, db_session=db,
                meta={"run_id": run_id, "engine": "langgraph", "plan": rec.model_dump(), "analysis_date": day})
            prediction_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"{run_id}/{rec.stock_key}"))
            for horizon in (1, 5):
                save_agent_prediction_outcome(agent_name=agent.name, stock_symbol=symbol, stock_market=market,
                    prediction_date=day, horizon_days=horizon, action=rec.action, action_label=suggestion["action_label"],
                    prediction_group_id=prediction_id, confidence=rec.confidence,
                    meta={"run_id": run_id, "suggestion_state": suggestion}, db_session=db)
        save_agent_context_run(agent_name=agent.name, stock_symbol="*", analysis_date=day,
            context_payload={"run_id": run_id, "symbols": snapshots}, db_session=db)
        save_analysis(agent.name, "*", result.content, result.title, raw_data=result.raw_data,
                      analysis_date=date.fromisoformat(day), db_session=db)
        for channel_key in targets:
            # Intraday throttles are scope-based; other reports also dedupe content.
            discriminator = "" if agent.name == "intraday_monitor" else build_notify_dedupe_key(agent.name, result.title, result.content)
            scope = f"{agent.name}/{run.scope_hash}/{channel_key}/{discriminator}"
            cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=ttl_minutes)
            recent = db.query(ResearchOutbox).filter(ResearchOutbox.policy_scope == scope,
                ResearchOutbox.status.in_(["sent", "sending", "unknown"]), ResearchOutbox.updated_at >= cutoff).first()
            db.add(ResearchOutbox(run_id=run_id, channel_key=channel_key, policy_scope=scope,
                ttl_minutes=ttl_minutes, status="pending" if notify and not recent else "suppressed"))
        return "committed"
    return await transaction(apply)


async def claim(run_id, channel):
    def apply(db):
        changed = db.execute(update(ResearchOutbox).where(ResearchOutbox.run_id == run_id,
            ResearchOutbox.channel_key == channel, ResearchOutbox.status == "pending").values(status="sending")).rowcount
        if not changed:
            return False
        row = db.get(ResearchOutbox, (run_id, channel))
        # Claim content/scope atomically against other committed delivery attempts.
        prior = db.query(ResearchOutbox).filter(ResearchOutbox.policy_scope == row.policy_scope,
            ResearchOutbox.run_id != run_id, ResearchOutbox.status.in_(["sent", "sending", "unknown"]),
            ResearchOutbox.updated_at >= datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=row.ttl_minutes)).first()
        if prior:
            row.status = "suppressed"
            return False
        return True
    return await transaction(apply)


async def sent(run_id, channel, outcome):
    def apply(db):
        row = db.get(ResearchOutbox, (run_id, channel))
        row.status = "sent" if outcome.get("success") else "suppressed" if outcome.get("skipped") else "unknown"
        row.error = str(outcome.get("error") or outcome.get("skipped") or "")[:500]
    await transaction(apply)
