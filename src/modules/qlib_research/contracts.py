"""Bounded, data-only input contracts. No arbitrary Python, YAML or pickle upload."""

from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import date, datetime
from zoneinfo import ZoneInfo
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class Bar(StrictInput):
    date: date
    symbol: str = Field(pattern=r"^(SH|SZ)[0-9]{6}$")
    open: float = Field(gt=0, allow_inf_nan=False)
    high: float = Field(gt=0, allow_inf_nan=False)
    low: float = Field(gt=0, allow_inf_nan=False)
    close: float = Field(gt=0, allow_inf_nan=False)
    volume: float = Field(ge=0, allow_inf_nan=False)
    factor: float = Field(gt=0, allow_inf_nan=False)
    # Prices above are unadjusted execution prices. Adjusted prices = price * factor.

    @model_validator(mode="after")
    def check_ohlc(self):
        if self.low > min(self.open, self.close) or self.high < max(self.open, self.close) or self.low > self.high:
            raise ValueError("qlib_ohlc_invalid")
        return self


class Membership(StrictInput):
    symbol: str = Field(pattern=r"^(SH|SZ)[0-9]{6}$")
    start: date
    end: date
    price_limit: Literal[0.1]
    special_treatment: Literal[False]

    @model_validator(mode="after")
    def dates_ordered(self):
        if self.end < self.start:
            raise ValueError("qlib_membership_invalid")
        return self


class DatasetInput(StrictInput):
    name: str = Field(min_length=1, max_length=120)
    market: Literal["CN"] = "CN"
    source: str = Field(min_length=1, max_length=300)
    license: str = Field(min_length=1, max_length=300)
    license_acknowledged: Literal[True]
    data_kind: Literal["market", "synthetic"]
    universe_kind: Literal["historical_membership", "fixed_research"]
    adjustment: Literal["point_in_time", "snapshot_adjusted"]
    calendar: list[date] = Field(min_length=220, max_length=10000)
    members: list[Membership] = Field(min_length=3, max_length=200)
    bars: list[Bar] = Field(min_length=660, max_length=200000)

    @model_validator(mode="after")
    def validate_panel(self):
        from src.platform.scheduling.trading_calendar import calendar_known, is_trading_day
        current = datetime.now(ZoneInfo("Asia/Shanghai"))
        if self.calendar != sorted(set(self.calendar)):
            raise ValueError("qlib_calendar_not_unique_ordered")
        for day in self.calendar:
            if day.weekday() >= 5 or (calendar_known("CN", day) and not is_trading_day("CN", day)):
                raise ValueError("qlib_non_session_bar")
            if day > current.date() or (day == current.date() and current.hour < 15):
                raise ValueError("qlib_incomplete_session")
        calendar = set(self.calendar)
        seen = set()
        intervals: dict[str, list[Membership]] = {}
        for member in self.members:
            intervals.setdefault(member.symbol, []).append(member)
        for symbol, members in intervals.items():
            if not re.fullmatch(r"(SH(600|601|603|605)|SZ(000|001|002))[0-9]{3}", symbol):
                raise ValueError("qlib_board_not_supported")
            ordered = sorted(members, key=lambda m: m.start)
            if any(b.start <= a.end for a, b in zip(ordered, ordered[1:])):
                raise ValueError("qlib_membership_overlap")
        counts: dict[str, int] = {}
        for bar in self.bars:
            key = (bar.date, bar.symbol)
            if key in seen:
                raise ValueError("qlib_duplicate_bar")
            seen.add(key)
            if not any(m.start <= bar.date <= m.end for m in intervals.get(bar.symbol, [])):
                raise ValueError("qlib_bar_outside_membership")
            if bar.date not in calendar:
                raise ValueError("qlib_bar_outside_calendar")
            counts[bar.symbol] = counts.get(bar.symbol, 0) + 1
        if len(counts) < 3 or min(counts.values()) < 220:
            raise ValueError("qlib_history_insufficient")
        if len(set(b.date for b in self.bars)) < 220:
            raise ValueError("qlib_history_insufficient")
        return self


class ResearchConfig(StrictInput):
    algorithm: Literal["lightgbm", "ridge"] = "lightgbm"
    horizon: int = Field(default=5, ge=1, le=20)
    topk: int = Field(default=10, ge=1, le=50)
    train_fraction: float = Field(default=0.6, ge=0.4, le=0.7, allow_inf_nan=False)
    valid_fraction: float = Field(default=0.15, ge=0.1, le=0.25, allow_inf_nan=False)
    open_cost: float = Field(default=0.0003, ge=0, le=0.02, allow_inf_nan=False)
    close_cost: float = Field(default=0.0008, ge=0, le=0.02, allow_inf_nan=False)
    min_cost: float = Field(default=5, ge=0, le=100, allow_inf_nan=False)
    capital: float = Field(default=1000000, ge=10000, le=100000000, allow_inf_nan=False)
    limit_threshold: float = Field(default=0.095, ge=0.01, le=0.1, allow_inf_nan=False)
    max_drawdown: float = Field(default=0.3, ge=0, le=1, allow_inf_nan=False)
    min_ic: float = Field(default=0, ge=-1, le=1, allow_inf_nan=False)
    min_test_days: int = Field(default=20, ge=10, le=250)
    num_boost_round: int = Field(default=80, ge=10, le=400)

    @model_validator(mode="after")
    def fractions(self):
        if self.train_fraction + self.valid_fraction > 0.85:
            raise ValueError("qlib_test_segment_too_short")
        return self


class JobInput(StrictInput):
    dataset_id: str = Field(pattern=r"^[a-f0-9]{32}$")
    config: ResearchConfig = Field(default_factory=ResearchConfig)
    request_key: str = Field(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")


class PublishInput(StrictInput):
    revision: int = Field(ge=0)
    reason: str = Field(min_length=5, max_length=500)
    mode: Literal["research", "validated"]


class ScheduleInput(StrictInput):
    enabled: bool = False
    dataset_id: str | None = Field(default=None, pattern=r"^[a-f0-9]{32}$")
    daily_hour: int = Field(default=18, ge=16, le=23)
    retrain_every_days: int = Field(default=30, ge=7, le=365)


def content_hash(data: dict) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def valid_id(value: str) -> str:
    if not re.fullmatch(r"[a-f0-9]{32}", value):
        raise ValueError("qlib_id_invalid")
    return value


def finite_tree(value):
    """Normalize worker numbers for strict JSON; undefined metrics remain null."""
    if isinstance(value, dict):
        return {str(k): finite_tree(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [finite_tree(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value
