"""Worker-owned transactions for research audit, business output and notification claims."""
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import time
import uuid
from copy import deepcopy

from sqlalchemy import delete, func, or_, update
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


def _owned(run_id, owner):
    return (ResearchRun.id == run_id, ResearchRun.status == "running", ResearchRun.lease_owner == owner)


async def start(run_id, trace, agent, scope, mode, *, owner, signature, lease_seconds):
    def apply(db):
        now = time.time()
        recent_cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=15)
        lease = {"lease_owner": owner, "lease_until": now + lease_seconds}
        inserted = db.execute(insert(ResearchRun).values(id=run_id, trace_id=trace, agent_name=agent,
            scope_hash=scope, engine=mode, as_of=datetime.now(timezone.utc).isoformat(), status="running",
            payload={"signature": signature, "groups": {}}, **lease)
            .on_conflict_do_nothing(index_elements=["id"])).rowcount
        row = db.get(ResearchRun, run_id)
        if inserted and trace.startswith("sch-"):
            siblings = db.query(ResearchRun).filter(
                ResearchRun.id != run_id, ResearchRun.agent_name == agent,
                ResearchRun.scope_hash == scope, ResearchRun.engine == mode,
            )
            # A scheduled invocation can reclaim a recent compatible run after a worker restart.
            prior = siblings.filter(ResearchRun.trace_id == trace).first()
            if prior is None:
                prior = siblings.filter(
                    ResearchRun.trace_id.startswith("sch-"), ResearchRun.status == "running",
                    ResearchRun.created_at >= recent_cutoff, ResearchRun.lease_until <= now,
                    ResearchRun.payload["signature"].as_string() == signature,
                ).order_by(ResearchRun.created_at.desc()).first()
            if prior is not None:
                db.execute(delete(ResearchRun).where(ResearchRun.id == run_id))
                row, inserted = prior, 0
        if inserted:
            return None
        if delivery := db.get(ResearchDelivery, row.id):
            payload = deepcopy(delivery.payload)
            rows = db.query(ResearchOutbox).filter(ResearchOutbox.run_id == row.id).all()
            payload["raw_data"]["notified"] = any(row.status == "sent" for row in rows)
            return {"delivery": payload, "run_id": row.id}
        if row.status != "running":
            return {"ended": True}
        # Existing installations have no lease on runs started before the migration.
        expired = or_(ResearchRun.lease_until <= now, (
            ResearchRun.lease_until.is_(None) & (ResearchRun.created_at < recent_cutoff)))
        payload = row.payload or {}
        if payload.get("signature") != signature:
            payload = {"signature": signature, "groups": {}}
        taken = db.execute(update(ResearchRun).where(ResearchRun.id == row.id, ResearchRun.status == "running",
            expired).values(payload=payload, trace_id=trace, **lease)).rowcount
        return {"checkpoint": deepcopy(payload), "run_id": row.id} if taken else {"busy": True}
    return await transaction(apply)


async def checkpoint(run_id, owner, fields):
    def apply(db):
        changes = [part for path, value in fields.items() for part in (path, func.json(json.dumps(value)))]
        updated = db.execute(update(ResearchRun).where(*_owned(run_id, owner)).values(
            payload=func.json_set(ResearchRun.payload, *changes))).rowcount
        if not updated:
            raise RuntimeError("Research lease lost")
    await transaction(apply)


async def progress(run_id, event, *, owner=None):
    def apply(db):
        # Parallel roles update independent JSON keys without overwriting each other's events.
        where = _owned(run_id, owner) if owner is not None else (ResearchRun.id == run_id,)
        group = event.get("group")
        key = f"{group}/{event['role']}" if group and group != "portfolio" else event["role"]
        result = db.execute(update(ResearchRun).where(*where).values(
            progress=func.json_set(func.coalesce(func.nullif(ResearchRun.progress, 'null'), '{}'), '$.' + json.dumps(key),
                                   func.json(json.dumps(event, ensure_ascii=False))))
            .execution_options(synchronize_session=False))
        if result.rowcount != 1:
            raise ValueError('Unknown research run')
    await transaction(apply)


async def fail(run_id, reason, *, owner):
    def apply(db):
        db.execute(update(ResearchRun).where(*_owned(run_id, owner)).values(
            status="failed", payload={"error": reason[:1000]}, lease_owner=None, lease_until=None,
            finished_at=datetime.now(timezone.utc)))
    await transaction(apply)


async def commit(run_id, agent, prepared, research, result, snapshots, targets, notify, ttl_minutes, *, owner):
    def apply(db):
        existing = db.get(ResearchDelivery, run_id)
        if existing:
            return existing.status
        changed = db.execute(update(ResearchRun).where(*_owned(run_id, owner)).values(
            status="completed", payload=research.model_dump(mode="json"), finished_at=datetime.now(timezone.utc),
            as_of=prepared.as_of.isoformat(), lease_owner=None, lease_until=None)).rowcount
        if not changed:
            return "stale"
        run = db.get(ResearchRun, run_id)
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
        history_scope = "*" if research.completion == "complete" else "*:degraded"
        save_analysis(agent.name, history_scope, result.content, result.title, raw_data=result.raw_data,
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
