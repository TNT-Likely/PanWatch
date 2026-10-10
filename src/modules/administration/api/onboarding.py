"""Recoverable setup checklist and its single, notification-free first analysis."""

import asyncio
import math
import threading
import uuid
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session, sessionmaker

from src.modules.administration.onboarding import (
    STATE_KEY, channel_fingerprint, model_fingerprint, now_iso, read_value, verified, write_value,
)
from src.platform.persistence.database import get_db
from src.platform.persistence.models import AIModel, AIService, AgentRun, NotifyChannel, PriceAlertRule, Stock
from src.web.errors import api_error

router = APIRouter()
_start_lock = threading.Lock()


class ProgressUpdate(BaseModel):
    goal: Literal["quotes", "ai"] | None = None
    deferred: bool | None = None
    stock_id: int | None = None
    skip: Literal["alert", "notify"] | None = None
    unskip: Literal["alert", "notify"] | None = None


def _selected(db, state):
    return db.get(Stock, state.get("stock_id")) if state.get("stock_id") else None


def get_status(db: Session) -> dict:
    state = read_value(db, STATE_KEY)
    stock = _selected(db, state)
    models = db.query(AIModel).order_by(AIModel.id).all()
    model_items = []
    for model in models:
        service = db.get(AIService, model.service_id)
        model_items.append({"id": model.id, "name": model.name, "model": model.model,
                            "is_default": bool(model.is_default), "verified": bool(service) and verified(
                                db, "model", model.id, model_fingerprint(model, service))})
    default = next((model for model in model_items if model["is_default"]), None)
    channels = db.query(NotifyChannel).order_by(NotifyChannel.id).all()
    channel_items = [{"id": ch.id, "name": ch.name, "enabled": bool(ch.enabled),
                      "is_default": bool(ch.is_default), "verified": bool(ch.enabled) and verified(
                          db, "channel", ch.id, channel_fingerprint(ch))} for ch in channels]
    quote = state.get("quote") if stock and (state.get("quote") or {}).get("stock_id") == stock.id else None
    run = db.query(AgentRun).filter(AgentRun.trace_id == state.get("analysis_trace_id", ""),
                                    AgentRun.agent_name == "first_analysis").first()
    analysis = None
    if run:
        from src.platform.ai.errors import descriptor_for_code

        messages = {"onboarding_quote_unavailable": "行情暂不可用，请检查数据源后重试。",
                    "onboarding_analysis_empty": "模型返回了空结果，请重试或更换模型。",
                    "onboarding_analysis_interrupted": "服务重启中断了分析，请重新生成。"}
        created = run.created_at.replace(tzinfo=timezone.utc) if run.created_at.tzinfo is None else run.created_at
        analysis = {"id": run.id, "status": run.status, "content": run.result if run.status == "success" else "",
                    "error_code": run.error if run.status == "failed" else "", "model_label": run.model_label,
                    "created_at": created.isoformat(), "trace_id": run.trace_id,
                    "error_message": messages.get(run.error) or descriptor_for_code(run.error).message if run.status == "failed" else ""}
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    alert = db.query(PriceAlertRule).filter(PriceAlertRule.stock_id == stock.id, PriceAlertRule.enabled.is_(True),
                (PriceAlertRule.expire_at.is_(None) | (PriceAlertRule.expire_at > now))).first() if stock else None
    facts = {"stock": bool(stock), "quote": bool(quote), "ai": bool(default and default["verified"]),
             "analysis": bool(stock and analysis and analysis["status"] == "success" and analysis["content"]),
             "alert": bool(alert), "notify": any(ch["verified"] for ch in channel_items)}
    goal = state.get("goal", "ai")
    required = ["stock", "quote"] + (["ai", "analysis"] if goal == "ai" else [])
    skipped = state.get("skipped", [])
    steps = [{"key": key, "required": key in required,
              "status": "complete" if done else "skipped" if key in skipped else "pending"}
             for key, done in facts.items()]
    return {"goal": goal, "deferred": bool(state.get("deferred")), "started": bool(state),
            "selected_stock": {"id": stock.id, "name": stock.name, "symbol": stock.symbol, "market": stock.market} if stock else None,
            "models": model_items, "channels": channel_items, "quote": quote, "analysis": analysis,
            "steps": steps, "completed": all(facts[key] for key in required),
            "completed_count": sum(facts[key] for key in required), "required_count": len(required)}


@router.get("")
def status(db: Session = Depends(get_db)):
    return get_status(db)


@router.patch("")
def update(body: ProgressUpdate, db: Session = Depends(get_db)):
    state = read_value(db, STATE_KEY)
    if body.stock_id is not None:
        if not db.get(Stock, body.stock_id):
            raise api_error(404, "stock_not_found", "股票不存在")
        run = db.query(AgentRun).filter(AgentRun.trace_id == state.get("analysis_trace_id", ""),
                                       AgentRun.status == "running").first()
        if run and body.stock_id != state.get("stock_id"):
            raise api_error(409, "onboarding_analysis_running", "分析进行中，请完成后再更换标的")
        if state.get("stock_id") != body.stock_id:
            state.pop("quote", None)
            state.pop("analysis_trace_id", None)
        state["stock_id"] = body.stock_id
    for key in ("goal", "deferred"):
        value = getattr(body, key)
        if value is not None:
            state[key] = value
    skipped = set(state.get("skipped", []))
    if body.skip:
        skipped.add(body.skip)
    if body.unskip:
        skipped.discard(body.unskip)
    state["skipped"] = sorted(skipped)
    write_value(db, STATE_KEY, state)
    db.commit()
    return get_status(db)


@router.post("/quote")
async def observe_quote(db: Session = Depends(get_db)):
    from src.platform.marketdata.marketdata_client import md_quote_rows

    state = read_value(db, STATE_KEY)
    stock = _selected(db, state)
    if not stock:
        raise api_error(400, "onboarding_stock_required", "请先选择一个标的")
    stock_id, symbol, market = stock.id, stock.symbol, stock.market
    try:
        rows = await asyncio.wait_for(asyncio.to_thread(md_quote_rows, [symbol], market), 30)
        quote = next((row for row in rows if row.get("symbol") == symbol), {})
        price = quote.get("current_price")
        if not isinstance(price, (float, int)) or not math.isfinite(price) or price <= 0:
            raise ValueError("missing quote")
    except Exception as exc:
        raise api_error(503, "onboarding_quote_unavailable", "行情暂不可用，请检查数据源后重试") from exc
    db.expire_all()
    state = read_value(db, STATE_KEY)
    if state.get("stock_id") != stock_id or not db.get(Stock, stock_id):
        raise api_error(409, "onboarding_stock_changed", "标的已更换，请重新获取行情")
    change_pct = quote.get("change_pct")
    state["quote"] = {"stock_id": stock_id, "current_price": price,
                      "change_pct": change_pct if isinstance(change_pct, (int, float)) and math.isfinite(change_pct) else None,
                      "source": quote.get("source") or "", "observed_at": now_iso(),
                      "source_time": str(quote.get("source_timestamp") or quote.get("quote_date") or "")}
    write_value(db, STATE_KEY, state)
    db.commit()
    return get_status(db)


async def _execute_analysis(bind, run_id: int, snapshot: dict):
    from src.modules.automation.first_analysis import FirstAnalysisAgent
    from src.modules.automation.base import AgentContext
    from src.platform.ai.ai_client import AIClient
    from src.platform.ai.errors import classify_ai_service_error
    from src.platform.marketdata.models import MarketCode
    from src.platform.notifications.notifier import NotifierManager
    from src.platform.observability.log_context import log_context
    from src.platform.runtime.config import AppConfig, Settings, StockConfig

    factory = sessionmaker(bind=bind)
    client = None
    try:
        client = AIClient(base_url=snapshot["base_url"], api_key=snapshot["api_key"], model=snapshot["model"])
        context = AgentContext(ai_client=client, notifier=NotifierManager(), suppress_notify=True,
                               model_label=snapshot["model_label"], report_language=snapshot["language"],
                               config=AppConfig(settings=Settings(), watchlist=[StockConfig(
                                   symbol=snapshot["symbol"], name=snapshot["name"], market=MarketCode(snapshot["market"]))]))
        with log_context(trace_id=snapshot["trace_id"], agent_name="first_analysis", event="onboarding_analysis"):
            result = await asyncio.wait_for(FirstAnalysisAgent().run(context), 180)
        values = {"status": "success", "result": result.content, "error": ""}
    except Exception as exc:
        code = str(exc) if str(exc) in {"onboarding_quote_unavailable", "onboarding_analysis_empty"} else classify_ai_service_error(exc).code
        values = {"status": "failed", "error": code}
    finally:
        if client:
            try:
                await client.client.close()
            except Exception:
                pass
    with factory() as db:
        db.query(AgentRun).filter(AgentRun.id == run_id, AgentRun.status == "running").update(values)
        db.commit()


@router.post("/analysis")
def start_analysis(db: Session = Depends(get_db)):
    from src.platform.language import resolve_report_language

    with _start_lock:
        db.expire_all()
        state = read_value(db, STATE_KEY)
        stock = _selected(db, state)
        if not stock or not (state.get("quote") or {}).get("stock_id") == stock.id:
            raise api_error(400, "onboarding_quote_required", "请先选择标的并查看行情")
        existing = db.query(AgentRun).filter(AgentRun.trace_id == state.get("analysis_trace_id", ""),
                                            AgentRun.status == "running").first()
        if existing:
            return get_status(db)
        model = db.query(AIModel).filter(AIModel.is_default.is_(True)).first()
        service = db.get(AIService, model.service_id) if model else None
        if not service or not verified(db, "model", model.id, model_fingerprint(model, service)):
            raise api_error(400, "onboarding_model_test_required", "请先设置默认模型并测试通过")
        trace = "first-" + uuid.uuid4().hex
        run = AgentRun(agent_name="first_analysis", status="running", trace_id=trace,
                       trigger_source="onboarding", model_label=f"{service.name}/{model.model}")
        db.add(run)
        state["analysis_trace_id"] = trace
        write_value(db, STATE_KEY, state)
        db.commit()
        snapshot = {"symbol": stock.symbol, "market": stock.market, "name": stock.name,
                    "base_url": service.base_url, "api_key": service.api_key, "model": model.model,
                    "model_label": run.model_label, "language": resolve_report_language(db), "trace_id": trace}
        # Capture IDs/bind before the request-scoped Session is closed.
        bind, run_id = db.get_bind(), run.id
        worker = threading.Thread(target=lambda: asyncio.run(_execute_analysis(bind, run_id, snapshot)),
                                  name=f"first-analysis-{run_id}", daemon=True)
        worker.start()
        return get_status(db)
