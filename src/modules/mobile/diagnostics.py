"""Device-scoped read-only views of the existing service diagnostics."""

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, FiniteFloat
from sqlalchemy import select
from sqlalchemy.orm import Session

from src.modules.mobile.fact_snapshots import FactPhase
from src.modules.mobile.schemas import MobileResponse
from src.platform.persistence.database import get_db
from src.platform.persistence.models import DataSource, MobileDevice, MobileFactTask
from src.web.errors import api_error


DB = Annotated[Session, Depends(get_db)]
RunState = Literal["running", "success", "failed", "cancelled", "skipped"]


class FactBrief(BaseModel):
    task_id: UUID
    finished_at: int


class AgentHealth(BaseModel):
    name: str
    label: str
    enabled: bool
    scheduled: bool | None
    next_run: datetime | None
    last_run: datetime | None
    result: RunState | None
    fact_phase: FactPhase | None
    latest_fact_brief: FactBrief | None


class SourceHealth(BaseModel):
    id: int
    name: str
    kind: str
    provider: str
    priority: int | None
    enabled: bool
    provider_samples: int = Field(ge=0)
    provider_success_rate: FiniteFloat | None = Field(ge=0, le=1)
    provider_last_success: datetime | None


class SourceProbe(BaseModel):
    passed: bool
    checked_at: datetime
    duration_ms: int = Field(ge=0)
    sample_count: int | None = Field(default=None, ge=0)


class Diagnostics(BaseModel):
    agents: list[AgentHealth]
    sources: list[SourceHealth]


_RUN_STATES = {state: state for state in ("running", "success", "failed", "cancelled", "skipped")}
_FACT_PHASE_BY_AGENT: dict[str, FactPhase] = {
    "premarket_outlook": "premarket",
    "intraday_monitor": "intraday",
    "daily_report": "postmarket",
}


def _latest_fact_brief(db: Session, device_id: str, phase: FactPhase) -> FactBrief | None:
    task = MobileFactTask
    row = db.execute(select(task.id, task.finished_at).where(
        task.device_id == device_id, task.phase == phase, task.state == "complete",
        task.market.is_(None), task.symbol.is_(None), task.finished_at.is_not(None),
    ).order_by(task.finished_at.desc(), task.created_at.desc(), task.id.desc()).limit(1)).first()
    return FactBrief(task_id=row.id, finished_at=row.finished_at) if row else None


def create_diagnostics_router(
    require_device, read_agent_health: Callable[[Session], dict], *, fact_task_scopes: frozenset[str],
) -> APIRouter:
    router = APIRouter()
    Device = Annotated[MobileDevice, Depends(require_device)]

    @router.get("/diagnostics")
    def snapshot(db: DB, device: Device) -> MobileResponse[Diagnostics]:
        from src.platform.marketdata.marketdata_client import get_market_data

        can_read_facts = fact_task_scopes.issubset(device.scope.split())
        agents = []
        for row in read_agent_health(db)["agents"]:
            last = row["last_run"] or {}
            upcoming = row["next_runs"]
            phase = _FACT_PHASE_BY_AGENT.get(row["name"]) if can_read_facts else None
            agents.append(AgentHealth(
                name=row["name"], label=row["display_name"], enabled=row["enabled"], scheduled=row["scheduled"],
                next_run=upcoming[0] if upcoming else None, last_run=last.get("created_at"),
                result=_RUN_STATES.get(last.get("status")),
                fact_phase=phase, latest_fact_brief=_latest_fact_brief(db, device.id, phase) if phase else None,
            ))
        health = get_market_data().health()
        sources = []
        for row in db.scalars(select(DataSource).order_by(DataSource.type, DataSource.priority, DataSource.id)):
            stats = health.get(row.provider, {})
            last_success = stats.get("last_success_at", 0)
            sources.append(SourceHealth(
                id=row.id, name=row.name, kind=row.type, provider=row.provider,
                priority=row.priority, enabled=bool(row.enabled),
                provider_samples=stats.get("count", 0), provider_success_rate=stats.get("success_rate"),
                provider_last_success=datetime.fromtimestamp(last_success, UTC) if last_success > 0 else None,
            ))
        return MobileResponse(data=Diagnostics(agents=agents, sources=sources))

    @router.post("/diagnostics/sources/{source_id}/test", dependencies=[Depends(require_device)])
    async def test_source(source_id: int, db: DB) -> MobileResponse[SourceProbe]:
        from src.modules.market.data_collector import get_collector_manager

        source = db.get(DataSource, source_id)
        if source is None or not source.enabled:
            raise api_error(404, "mobile_source_unavailable", "数据源不可用。")
        try:
            result = await asyncio.wait_for(get_collector_manager().test_source(source), timeout=8)
        except TimeoutError:
            raise api_error(504, "mobile_source_timeout", "数据源检查超时。") from None
        return MobileResponse(data=SourceProbe(
            passed=bool(result.success), checked_at=datetime.now(UTC), duration_ms=max(0, result.duration_ms),
            sample_count=result.count if result.success else None,
        ))

    return router
