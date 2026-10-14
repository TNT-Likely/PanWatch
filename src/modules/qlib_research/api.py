"""Authenticated, bounded research API. No user code or model deserialization."""

import json
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import Field, ValidationError

from . import service
from .contracts import DatasetInput, JobInput, MarketDataSourceInput, MarketDataSyncInput, PublishInput, ResearchConfig, ScheduleInput, StrictInput


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
    return {**service.runtime_health(), "supported_markets": ["CN"], "monitor": service.monitor()}


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
def records(kind: Literal["experiment", "model", "selection", "decision"]):
    return {"items": service.list_records(kind)}


@router.get("/records/{kind}/{record_id}")
def record(kind: Literal["experiment", "model", "selection", "decision"], record_id: str):
    return invoke(service.get_record, record_id, kind)


@router.get("/experiments/{record_id}/export")
def export_experiment(record_id: str):
    item = invoke(service.get_record, record_id, "experiment")
    # Data license may not allow redistribution: never include bars or model files.
    return JSONResponse(item, headers={"Content-Disposition": f'attachment; filename="panwatch-qlib-{record_id}.json"'})


@router.get("/opportunities")
def opportunities():
    return service.opportunities()


class DecisionInput(StrictInput):
    acknowledge_research: bool = False


@router.post("/selections/{run_id}/decision")
def decision(run_id: str, body: DecisionInput, actor=Depends(require_actor)):
    return invoke(service.make_decision, run_id, actor, body.acknowledge_research)


@router.get("/governance")
def governance():
    return service.governance()


@router.get("/executions")
def executions():
    return service.executions()


@router.get("/basket")
def basket():
    return {"items": service.basket()}


class BasketInput(StrictInput):
    run_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    symbol: str = Field(pattern=r"^(SH|SZ)[0-9]{6}$")
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
