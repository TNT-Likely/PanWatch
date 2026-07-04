from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from src.core.signals.a_share_decision import evaluate_a_share_decision

router = APIRouter()


class WatchlistSignalRequest(BaseModel):
    symbol: str
    name: str = ""
    market: str = "CN"
    quote: dict[str, Any] = Field(default_factory=dict)
    technical: dict[str, Any] = Field(default_factory=dict)
    position: dict[str, Any] = Field(default_factory=dict)
    news_flags: list[str] = Field(default_factory=list)
    sector_strength: float | None = None
    already_no_chase_today: bool = False


@router.post("/signals/evaluate")
def evaluate_signal(req: WatchlistSignalRequest) -> dict[str, Any]:
    """Evaluate one A-share watchlist item with deterministic guardrails."""

    return evaluate_a_share_decision(req.model_dump()).to_dict()

