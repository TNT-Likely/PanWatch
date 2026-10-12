"""市场启用机制:核心市场常开,可选市场经 AppSettings 显式开启。

后端各入口与前端 GET /api/stocks/markets/enabled 共用这里的解析结果,
与 DataSource 行是否 enabled 无关。新增可选市场 = 在 OPTIONAL_MARKETS 加一项。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from sqlalchemy.orm import Session

from src.platform.persistence.models import AppSettings

logger = logging.getLogger(__name__)

SETTING_KEY = "market_enablement"

CORE_MARKETS: tuple[str, ...] = ("CN", "HK", "US")
CAPABILITY_ALL = frozenset({
    "quotes", "klines", "watchlist", "holdings", "search", "news",
    "alerts", "analysis", "discovery", "strategy", "paper",
})


@dataclass(frozen=True)
class MarketOption:
    default_enabled: bool
    capabilities: frozenset[str]


OPTIONAL_MARKETS: dict[str, MarketOption] = {
    "AU": MarketOption(
        default_enabled=False,
        capabilities=frozenset({"quotes", "klines", "watchlist", "holdings"}),
    ),
}


def _overrides(db: Session) -> dict[str, bool]:
    row = db.query(AppSettings).filter(AppSettings.key == SETTING_KEY).first()
    if not row or not (row.value or "").strip():
        return {}
    try:
        raw = json.loads(row.value)
    except ValueError:
        logger.warning("market_enablement 非法 JSON,忽略: %r", row.value)
        return {}
    if not isinstance(raw, dict):
        return {}
    return {
        str(k).upper(): v for k, v in raw.items()
        if str(k).upper() in OPTIONAL_MARKETS and isinstance(v, bool)
    }


def effective_markets(db: Session) -> list[str]:
    ov = _overrides(db)
    out = list(CORE_MARKETS)
    out += [m for m in OPTIONAL_MARKETS if ov.get(m, OPTIONAL_MARKETS[m].default_enabled)]
    return out


def is_market_enabled(db: Session, code: str) -> bool:
    return str(code).upper() in effective_markets(db)


def market_capabilities(db: Session, code: str) -> frozenset[str]:
    c = str(code).upper()
    if c in CORE_MARKETS:
        return CAPABILITY_ALL
    if c not in OPTIONAL_MARKETS or not is_market_enabled(db, c):
        return frozenset()
    return OPTIONAL_MARKETS[c].capabilities


def capability_enabled(db: Session, code: str, cap: str) -> bool:
    return cap in market_capabilities(db, code)


def set_market_enabled(db: Session, code: str, enabled: bool) -> None:
    c = str(code).upper()
    if c not in OPTIONAL_MARKETS:
        raise ValueError(f"market not toggleable: {code}")
    ov = _overrides(db)
    ov[c] = bool(enabled)
    row = db.query(AppSettings).filter(AppSettings.key == SETTING_KEY).first()
    if row is None:
        row = AppSettings(key=SETTING_KEY, value="", description="可选市场开启映射(JSON,如 {\"AU\": true})")
        db.add(row)
    row.value = json.dumps(ov)
    db.commit()


def disabled_optional_markets(db: Session) -> set[str]:
    """当前被禁用的可选市场集合(核心市场永不在内)。"""
    eff = set(effective_markets(db))
    return {m for m in OPTIONAL_MARKETS if m not in eff}


def require_market_enabled(db: Session, code: str) -> None:
    """市场未启用时抛 400 market_disabled(API 入口统一闸门)。"""
    if not hasattr(db, "query"):
        # 直接以函数形式调用(无依赖注入,如单测):按默认有效市场处理
        if str(code).upper() not in CORE_MARKETS:
            from src.web.errors import api_error
            raise api_error(400, "market_disabled", f"市场 {code} 未启用")
        return
    if not is_market_enabled(db, code):
        from src.web.errors import api_error
        raise api_error(400, "market_disabled", f"市场 {code} 未启用")
