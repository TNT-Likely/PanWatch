"""策略目录与权重读取。"""

from __future__ import annotations

from dataclasses import dataclass

from src.web.database import SessionLocal
from src.web.models import StrategyCatalog, StrategyWeight


@dataclass(frozen=True)
class StrategySpec:
    code: str
    name: str
    description: str
    version: str = "v1"
    enabled: bool = True
    market_scope: str = "ALL"
    risk_level: str = "medium"
    params: dict | None = None
    default_weight: float = 1.0


DEFAULT_STRATEGIES: tuple[StrategySpec, ...] = (
    StrategySpec(
        code="trend_follow",
        name="趋势延续",
        description="顺势跟随，优先均线多头且动量延续",
        risk_level="medium",
        params={"horizon_days": 5},
        default_weight=1.15,
    ),
    StrategySpec(
        code="macd_golden",
        name="MACD金叉",
        description="MACD 金叉确认，偏中短线",
        risk_level="medium",
        params={"horizon_days": 3},
        default_weight=1.10,
    ),
    StrategySpec(
        code="volume_breakout",
        name="放量突破",
        description="放量突破关键位，偏进攻",
        risk_level="high",
        params={"horizon_days": 3},
        default_weight=1.18,
    ),
    StrategySpec(
        code="pullback",
        name="回踩确认",
        description="回踩支撑后二次启动",
        risk_level="low",
        params={"horizon_days": 5},
        default_weight=1.05,
    ),
    StrategySpec(
        code="rebound",
        name="超跌反弹",
        description="超跌后的反弹交易",
        risk_level="high",
        params={"horizon_days": 3},
        default_weight=0.95,
    ),
    StrategySpec(
        code="watchlist_agent",
        name="Agent建议",
        description="来自既有 Agent 的综合建议映射",
        risk_level="medium",
        params={"horizon_days": 3},
        default_weight=1.00,
    ),
    StrategySpec(
        code="market_scan",
        name="市场扫描",
        description="市场池扫描策略（热门与活跃）",
        risk_level="medium",
        params={"horizon_days": 3},
        default_weight=1.08,
    ),
)


def ensure_strategy_catalog() -> None:
    db = SessionLocal()
    try:
        changed = False
        for spec in DEFAULT_STRATEGIES:
            row = (
                db.query(StrategyCatalog)
                .filter(StrategyCatalog.code == spec.code)
                .first()
            )
            if not row:
                db.add(
                    StrategyCatalog(
                        code=spec.code,
                        name=spec.name,
                        description=spec.description,
                        version=spec.version,
                        enabled=bool(spec.enabled),
                        market_scope=spec.market_scope,
                        risk_level=spec.risk_level,
                        params=spec.params or {},
                        default_weight=float(spec.default_weight),
                    )
                )
                changed = True
                continue
            if row.name != spec.name:
                row.name = spec.name
                changed = True
            if (row.description or "") != (spec.description or ""):
                row.description = spec.description
                changed = True
            if (row.version or "v1") != (spec.version or "v1"):
                row.version = spec.version
                changed = True
            if (row.market_scope or "ALL") != (spec.market_scope or "ALL"):
                row.market_scope = spec.market_scope
                changed = True
            if (row.risk_level or "medium") != (spec.risk_level or "medium"):
                row.risk_level = spec.risk_level
                changed = True
            if float(row.default_weight or 1.0) != float(spec.default_weight):
                row.default_weight = float(spec.default_weight)
                changed = True
            if not row.params:
                row.params = spec.params or {}
                changed = True
        if changed:
            db.commit()
    finally:
        db.close()


def list_strategy_catalog(enabled_only: bool = True) -> list[dict]:
    ensure_strategy_catalog()
    db = SessionLocal()
    try:
        q = db.query(StrategyCatalog)
        if enabled_only:
            q = q.filter(StrategyCatalog.enabled.is_(True))
        rows = q.order_by(StrategyCatalog.code.asc()).all()
        out = []
        for r in rows:
            out.append(
                {
                    "code": r.code,
                    "name": r.name,
                    "description": r.description or "",
                    "version": r.version or "v1",
                    "enabled": bool(r.enabled),
                    "market_scope": r.market_scope or "ALL",
                    "risk_level": r.risk_level or "medium",
                    "params": r.params or {},
                    "default_weight": float(r.default_weight or 1.0),
                }
            )
        return out
    finally:
        db.close()


def get_strategy_profile_map() -> dict[str, dict]:
    rows = list_strategy_catalog(enabled_only=False)
    return {x["code"]: x for x in rows}


def get_effective_weight_map(*, market: str = "ALL", regime: str = "default") -> dict[str, float]:
    ensure_strategy_catalog()
    mkt = (market or "ALL").strip().upper() or "ALL"
    reg = (regime or "default").strip() or "default"
    db = SessionLocal()
    try:
        defaults = {
            s.code: float(s.default_weight or 1.0)
            for s in db.query(StrategyCatalog).all()
        }
        rows = (
            db.query(StrategyWeight)
            .filter(
                StrategyWeight.regime == reg,
                StrategyWeight.market.in_(("ALL", mkt)),
            )
            .all()
        )
        out = dict(defaults)
        for r in rows:
            key = (r.strategy_code or "").strip()
            if not key:
                continue
            # Market-specific weight overrides ALL.
            if (r.market or "ALL").upper() == mkt:
                out[key] = float(r.weight or out.get(key, 1.0))
            elif key not in out:
                out[key] = float(r.weight or 1.0)
        return out
    finally:
        db.close()
