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


RESEARCH_SYMBOL = r"^(?:(SH|SZ|BJ)[0-9]{6}|HK_[0-9]{5}|US_[A-Z][A-Z0-9.\-]{0,19})$"


def symbol_market(symbol: str) -> str:
    return "HK" if symbol.startswith("HK_") else "US" if symbol.startswith("US_") else "CN"


class Bar(StrictInput):
    date: date
    symbol: str = Field(pattern=RESEARCH_SYMBOL)
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
    symbol: str = Field(pattern=RESEARCH_SYMBOL)
    start: date
    end: date
    price_limit: float | None = Field(ge=0.01, le=0.3, allow_inf_nan=False)
    special_treatment: bool
    lot: int = Field(default=100, ge=1, le=100000)
    currency: str | None = Field(default=None, max_length=6)

    @model_validator(mode="after")
    def dates_ordered(self):
        if self.end < self.start:
            raise ValueError("qlib_membership_invalid")
        if self.symbol.startswith("US_") and "lot" not in self.model_fields_set:
            self.lot = 1
        if self.symbol.startswith("HK_") and "lot" not in self.model_fields_set:
            raise ValueError("qlib_hk_board_lot_required")
        return self


class DatasetInput(StrictInput):
    name: str = Field(min_length=1, max_length=120)
    market: Literal["CN", "HK", "US"] = "CN"
    source: str = Field(min_length=1, max_length=300)
    license: str = Field(min_length=1, max_length=300)
    license_acknowledged: Literal[True]
    data_kind: Literal["market", "synthetic"]
    universe_kind: Literal["historical_membership", "fixed_research"]
    adjustment: Literal["point_in_time", "snapshot_adjusted"]
    price_basis: Literal["raw_execution", "provider_snapshot"] = "raw_execution"
    names: dict[str, str] = Field(default_factory=dict, max_length=512)
    provenance: dict[str, str] = Field(default_factory=dict, max_length=8)
    calendar: list[date] = Field(min_length=220, max_length=10000)
    members: list[Membership] = Field(min_length=3, max_length=512)
    bars: list[Bar] = Field(min_length=660, max_length=200000)

    @model_validator(mode="after")
    def validate_panel(self):
        from src.platform.scheduling.trading_calendar import calendar_known, is_trading_day
        current = datetime.now(ZoneInfo({"CN": "Asia/Shanghai", "HK": "Asia/Hong_Kong", "US": "America/New_York"}[self.market]))
        if self.calendar != sorted(set(self.calendar)):
            raise ValueError("qlib_calendar_not_unique_ordered")
        for day in self.calendar:
            if day.weekday() >= 5 or (calendar_known(self.market, day) and not is_trading_day(self.market, day)):
                raise ValueError("qlib_non_session_bar")
            from src.platform.scheduling.trading_calendar import trading_sessions
            sessions = trading_sessions(self.market, day)
            close = sessions[-1].end if sessions else datetime.strptime("15:00" if self.market == "CN" else "16:00", "%H:%M").time()
            if day > current.date() or (day == current.date() and current.time().replace(tzinfo=None) < close):
                raise ValueError("qlib_incomplete_session")
        calendar = set(self.calendar)
        seen = set()
        intervals: dict[str, list[Membership]] = {}
        currencies={m.currency or {"CN":"CNY","HK":"HKD","US":"USD"}[self.market] for m in self.members}
        if len(currencies)!=1 or not currencies.issubset({"CN":{"CNY"},"HK":{"HKD","CNY","USD"},"US":{"USD"}}[self.market]):
            raise ValueError("qlib_dataset_currency_mismatch")
        for member in self.members:
            intervals.setdefault(member.symbol, []).append(member)
        for symbol, members in intervals.items():
            if symbol_market(symbol) != self.market or (self.market == "CN" and not re.fullmatch(r"(SH(60|68)|SZ(00|30)|BJ(43|83|87|88|92))[0-9]{4}", symbol)):
                raise ValueError("qlib_board_not_supported")
            if self.market != "CN" and any(m.price_limit is not None or m.special_treatment for m in members):
                raise ValueError("qlib_market_rules_invalid")
            ordered = sorted(members, key=lambda m: m.start)
            if any(b.start <= a.end for a, b in zip(ordered, ordered[1:])):
                raise ValueError("qlib_membership_overlap")
        counts: dict[str, int] = {}
        if any(not re.fullmatch(RESEARCH_SYMBOL, key) or len(value) > 300 for key, value in self.names.items()):
            raise ValueError("qlib_names_invalid")
        if any(len(key) > 60 or len(value) > 300 for key, value in self.provenance.items()):
            raise ValueError("qlib_provenance_invalid")
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
    algorithm: Literal["lightgbm", "ridge", "trend_breakout", "strength_continuation", "oversold_repair"] = "lightgbm"
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


class MarketDataSourceInput(StrictInput):
    symbols: list[str] = Field(min_length=3, max_length=20)
    vendor: Literal["auto", "tencent", "eastmoney"] = "auto"
    license: str = Field(min_length=5, max_length=300)
    license_acknowledged: Literal[True]
    automatic: bool = False

    @model_validator(mode="after")
    def supported_pool(self):
        if len(set(self.symbols)) != len(self.symbols) or any(not re.fullmatch(r"(SH(600|601|603|605)|SZ(000|001|002))[0-9]{3}", s) for s in self.symbols):
            raise ValueError("qlib_research_pool_invalid")
        self.symbols = sorted(self.symbols)
        return self


class MarketDataSyncInput(StrictInput):
    request_key: str = Field(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    train_when_ready: bool = False
    reinitialize: bool = False
    predict_when_ready: bool = True


class UniverseConfigInput(StrictInput):
    markets: list[Literal["CN", "HK", "US"]] = Field(default_factory=lambda: ["CN", "HK", "US"], min_length=1, max_length=3)
    license: str = Field(min_length=5, max_length=300)
    license_acknowledged: Literal[True]
    automatic: bool = False
    training_stocks: int = Field(default=128, ge=3, le=256)

    @model_validator(mode="after")
    def unique_markets(self):
        if len(set(self.markets)) != len(self.markets):
            raise ValueError("qlib_markets_duplicate")
        return self


class UniverseJobInput(StrictInput):
    request_key: str = Field(min_length=8, max_length=100, pattern=r"^[A-Za-z0-9_-]+$")
    market: Literal["CN", "HK", "US"] | None = None
    config: ResearchConfig = Field(default_factory=ResearchConfig)
    reinitialize: bool = False


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
