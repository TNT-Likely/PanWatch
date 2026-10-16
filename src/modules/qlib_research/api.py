"""Authenticated, bounded research API. No user code or model deserialization."""

import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Query
from fastapi.responses import JSONResponse
from pydantic import Field, ValidationError

from . import service
from .contracts import DatasetInput, JobInput, MarketDataSourceInput, MarketDataSyncInput, PublishInput, ResearchConfig, ScheduleInput, StrictInput, UniverseConfigInput, UniverseJobInput, RESEARCH_SYMBOL


def current_actor():
    """Fail closed unless the composition root wires its authentication dependency."""
    raise HTTPException(401, "qlib_authenticated_user_required")


def require_actor(user=Depends(current_actor)):
    if not user:
        raise HTTPException(401, "qlib_authenticated_user_required")
    return str(user)


router = APIRouter(dependencies=[Depends(require_actor)])


def invoke(function, *args, **kwargs):
    try:
        return function(*args, **kwargs)
    except ValueError as error:
        code = str(error)
        raise HTTPException(409 if code.endswith("conflict") else 422, code if code.startswith("qlib_") else "qlib_invalid_request") from error


@router.get("/health")
def health():
    return {**service.runtime_health(), "supported_markets": ["CN", "HK", "US"], "monitor": service.monitor()}


@router.get("/universe")
def universe_status():
    from . import universe
    return invoke(universe.status)


@router.put("/universe")
def configure_universe(body: UniverseConfigInput, actor=Depends(require_actor)):
    from . import universe
    return invoke(universe.configure,body,actor)


@router.get("/universe/members")
def universe_members(market: Literal["CN","HK","US"]="CN", page:int=Query(1,ge=1,le=10000), limit:int=Query(100,ge=1,le=200), search:str=Query("",max_length=120), status:Literal["","pending","ready","excluded","stale"]=""):
    from . import universe
    return invoke(universe.members,market,page=page,limit=limit,search=search,status_filter=status)


@router.post("/universe/jobs/{kind}")
def universe_job(kind:Literal["catalog","history","train","predict"],body:UniverseJobInput,actor=Depends(require_actor)):
    from . import universe
    if kind in ("train","predict") and not body.market:
        raise HTTPException(422,"qlib_market_required")
    if body.reinitialize and kind!="history":
        raise HTTPException(422,"qlib_reinitialize_history_only")
    return invoke(universe.enqueue,kind,body.request_key,actor,market=body.market,config=body.config.model_dump(),reinitialize=body.reinitialize)


@router.post("/universe/jobs/{job_id}/{action}")
def universe_job_action(job_id:str,action:Literal["cancel","resume"],actor=Depends(require_actor)):
    from . import universe
    return invoke(universe.cancel if action=="cancel" else universe.resume,job_id,actor)


@router.get("/datasets")
def datasets():
    return {"items": service.list_records("dataset")}


@router.post("/datasets")
async def upload_dataset(request: Request, actor=Depends(require_actor)):
    payload = bytearray()
    async for chunk in request.stream():
        payload.extend(chunk)
        if len(payload) > 40_000_000:
            raise HTTPException(413, "qlib_dataset_too_large")
    try:
        data = DatasetInput.model_validate_json(payload)
    except ValidationError:
        raise HTTPException(422, "qlib_dataset_invalid") from None
    return invoke(service.import_dataset, data, actor)


@router.get("/jobs")
def jobs():
    return {"items": service.jobs()}


@router.get("/data-source")
def data_source():
    from . import marketdata_sync
    return marketdata_sync.status()


@router.put("/data-source")
def configure_data_source(body: MarketDataSourceInput, actor=Depends(require_actor)):
    from . import marketdata_sync
    return invoke(marketdata_sync.configure, body, actor)


@router.post("/data-source/sync")
def sync_data_source(body: MarketDataSyncInput, actor=Depends(require_actor)):
    from . import marketdata_sync
    return invoke(marketdata_sync.enqueue, body.request_key, actor, train_when_ready=body.train_when_ready, reinitialize=body.reinitialize, predict_when_ready=body.predict_when_ready)


@router.post("/data-source/jobs/{job_id}/cancel")
def cancel_data_sync(job_id: str, actor=Depends(require_actor)):
    from . import marketdata_sync
    return invoke(marketdata_sync.cancel, job_id, actor)


@router.post("/jobs")
def train(body: JobInput, actor=Depends(require_actor)):
    return invoke(service.enqueue, body.dataset_id, body.config.model_dump(), body.request_key, actor)


class PredictInput(StrictInput):
    dataset_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    request_key: str = Field(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")


@router.post("/models/{model_id}/predict")
def predict(model_id: str, body: PredictInput, actor=Depends(require_actor)):
    return invoke(service.enqueue, body.dataset_id, {}, body.request_key, actor, model_id=model_id)


@router.post("/jobs/{job_id}/cancel")
def cancel(job_id: str, actor=Depends(require_actor)):
    return invoke(service.cancel_job, job_id, actor)


@router.get("/records/{kind}")
def records(kind: Literal["experiment", "model", "selection", "decision"], metadata_only:bool=False, market:Literal["CN","HK","US"]|None=None):
    return {"items": service.list_records(kind,metadata_only,market)}


@router.get("/records/{kind}/{record_id}")
def record(kind: Literal["experiment", "model", "selection", "decision"], record_id: str):
    return invoke(service.get_record, record_id, kind)


@router.get("/experiments/{record_id}/export")
def export_experiment(record_id: str):
    item = invoke(service.get_record, record_id, "experiment")
    # Data license may not allow redistribution: never include bars or model files.
    return JSONResponse(item, headers={"Content-Disposition": f'attachment; filename="panwatch-qlib-{record_id}.json"'})


@router.get("/opportunities")
def opportunities(market:Literal["CN","HK","US"]="CN",page:int=Query(1,ge=1,le=10000),limit:int|None=Query(None,ge=1,le=200),search:str=Query("",max_length=120),top:int=Query(0,ge=0,le=50),source:Literal["all","watched"]="all",holding:Literal["all","held","unheld"]="all",availability:Literal["all","tradable","blocked"]="all",run_id:str|None=Query(None,pattern=r"^[a-f0-9]{32}$"),view:Literal["all","featured"]="all"):
    if top not in (0,10,20,50):
        raise HTTPException(422,"qlib_rank_filter_invalid")
    return invoke(service.opportunities,market,page,limit,search,top,source,holding,availability,run_id,view)


class DecisionInput(StrictInput):
    acknowledge_research: bool = False
    preview_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")


@router.post("/selections/{run_id}/decision")
def decision(run_id: str, body: DecisionInput, actor=Depends(require_actor)):
    if body.preview_hash is None:
        raise HTTPException(422, "qlib_plan_preview_required")
    from . import paper_plans
    return invoke(paper_plans.confirm, run_id, actor, body.preview_hash, body.acknowledge_research)


@router.get("/governance")
def governance():
    return service.governance()


@router.get("/selections/{run_id}/plan")
def preview_plan(run_id: str):
    from . import paper_plans
    return invoke(paper_plans.preview, run_id)


class ConfirmPlanInput(StrictInput):
    preview_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    acknowledge_research: bool = False


@router.post("/selections/{run_id}/plan")
def confirm_plan(run_id: str, body: ConfirmPlanInput, actor=Depends(require_actor)):
    from . import paper_plans
    return invoke(paper_plans.confirm, run_id, actor, body.preview_hash, body.acknowledge_research)


@router.get("/paper-plans")
def paper_plans_status():
    from . import paper_plans
    return invoke(paper_plans.list_plans)


class AutomationInput(StrictInput):
    enabled: bool = True
    request_key: str | None = Field(default=None, pattern=r"^[a-zA-Z0-9_-]{8,100}$")
    mode: Literal["automatic", "manual"] = "automatic"
    model_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    capital_config_hash: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    days: int = Field(default=30, ge=1, le=90)
    acknowledge_research: bool = False
    authorization_kind: Literal["validated", "paper_experiment"] = "validated"
    acknowledge_experiment: bool = False


class RulePrepareInput(StrictInput):
    algorithm: Literal["trend_breakout", "strength_continuation", "oversold_repair"] = "trend_breakout"
    market: Literal["CN", "HK", "US"] = "CN"
    request_key: str = Field(pattern=r"^[a-zA-Z0-9_-]{8,100}$")


@router.get("/strategies")
def strategy_catalog(market: Literal["CN", "HK", "US"] = "CN"):
    from . import strategies
    return invoke(strategies.catalog, market)


@router.post("/strategies/prepare")
def prepare_strategy(body: RulePrepareInput, actor=Depends(require_actor)):
    from . import universe
    config = ResearchConfig(algorithm=body.algorithm).model_dump()
    return invoke(universe.enqueue, "train", body.request_key, actor, market=body.market, config=config)


@router.post("/strategies/{model_id}/activate")
def activate_strategy(model_id: str, body: PublishInput, actor=Depends(require_actor)):
    if body.mode != "research":
        raise HTTPException(422, "qlib_rule_not_validated")
    from . import strategies
    return invoke(strategies.activate, model_id, body.revision, actor)


@router.post("/strategies/{model_id}/refresh")
def refresh_strategy(model_id: str, actor=Depends(require_actor)):
    from . import strategies
    return invoke(strategies.refresh, model_id, actor)


@router.get("/paper-automation")
def paper_automation_status():
    from . import automation
    return invoke(automation.status)


@router.put("/paper-automation")
def configure_paper_automation(body: AutomationInput, actor=Depends(require_actor)):
    from . import automation
    return invoke(automation.configure, body, actor)


@router.post("/paper-plans/{decision_id}/cancel")
def cancel_plan(decision_id: str, actor=Depends(require_actor)):
    from . import paper_plans
    return invoke(paper_plans.cancel, decision_id, actor)


@router.get("/executions")
def executions():
    return service.executions()


@router.get("/basket")
def basket():
    return {"items": service.basket()}


class BasketInput(StrictInput):
    run_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    symbol: str = Field(pattern=RESEARCH_SYMBOL)
    included: bool


@router.put("/basket")
def update_basket(body: BasketInput, actor=Depends(require_actor)):
    return {"items": invoke(service.set_basket, body.run_id, body.symbol, body.included, actor)}


class CompareInput(StrictInput):
    ids: list[str] = Field(min_length=2, max_length=4)


@router.post("/experiments/compare")
def compare(body: CompareInput):
    return invoke(service.compare_experiments, body.ids)


@router.post("/models/{model_id}/publish")
def publish(model_id: str, body: PublishInput, actor=Depends(require_actor)):
    return invoke(service.publish, model_id, body.revision, body.reason, body.mode, actor)


@router.post("/models/{model_id}/rollback")
def rollback(model_id: str, body: PublishInput, actor=Depends(require_actor)):
    return invoke(service.publish, model_id, body.revision, body.reason, body.mode, actor, rollback=True)


class RetireInput(StrictInput):
    revision: int = Field(ge=0)
    reason: str = Field(min_length=5, max_length=500)


@router.post("/models/{model_id}/retire")
def retire(model_id: str, body: RetireInput, actor=Depends(require_actor)):
    return invoke(service.retire, model_id, body.revision, body.reason, actor)


class TemplateInput(StrictInput):
    name: str = Field(min_length=1, max_length=100)
    config: ResearchConfig


@router.get("/templates")
def templates():
    return {"items": service.templates()}


@router.post("/templates")
def template(body: TemplateInput, actor=Depends(require_actor)):
    return service.save_template(body.name, body.config.model_dump(), actor)


@router.put("/schedule")
def schedule(body: ScheduleInput, actor=Depends(require_actor)):
    return invoke(service.save_schedule, body.model_dump(), actor)


@router.post("/schedule/tick")
def tick():
    return invoke(service.scheduled_tick)
