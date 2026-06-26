"""自选股产业链自动分类（老马 LMD 框架：上游 / 中游 / 下游）。"""

from __future__ import annotations

import logging
import re
import threading
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml
from sqlalchemy.orm import Session

from src.collectors.concept_collector import can_auto_fetch_concept_tags, fetch_cn_industry
from src.core.notifier import get_global_proxy
from src.core.stock_concept_tags import _as_str_list
from src.core.timezone import utc_now
from src.web.database import SessionLocal
from src.web.models import Stock

logger = logging.getLogger(__name__)

_CHAIN_CONFIG_PATH = (
    Path(__file__).parent.parent.parent / "config" / "lmd_industry_chains.yaml"
)
_MIN_SCORE = 10
_SYMBOL_SCORE = 100


@lru_cache(maxsize=1)
def load_chain_taxonomy() -> dict[str, Any]:
    if not _CHAIN_CONFIG_PATH.is_file():
        logger.warning("产业链配置文件不存在: %s", _CHAIN_CONFIG_PATH)
        return {}
    with _CHAIN_CONFIG_PATH.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    return data.get("chains") or {}


def _normalize_symbol(symbol: str, market: str) -> str:
    sym = (symbol or "").strip().upper()
    mkt = (market or "CN").strip().upper()
    if mkt == "CN":
        return sym.zfill(6) if sym.isdigit() else sym
    if mkt == "HK":
        return sym.lstrip("0") or "0"
    return sym


def _text_blob(stock: Stock, *, industry: str = "") -> str:
    parts = [
        str(getattr(stock, "name", "") or ""),
        industry,
        *(_as_str_list(getattr(stock, "concept_tags_auto", None))),
        *(_as_str_list(getattr(stock, "concept_tags_manual", None))),
    ]
    return " ".join(p for p in parts if p)


def _keyword_hit(keyword: str, blob: str) -> bool:
    kw = (keyword or "").strip()
    if not kw:
        return False
    if kw in blob:
        return True
    # 英文 ticker / 缩写按词边界匹配
    if re.fullmatch(r"[A-Za-z0-9]+", kw):
        return bool(re.search(rf"\b{re.escape(kw)}\b", blob, flags=re.IGNORECASE))
    return False


def classify_stock(
    stock: Stock,
    *,
    industry: str = "",
    taxonomy: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """将单只股票归入产业链层级，未匹配则返回 None。"""
    chains = taxonomy if taxonomy is not None else load_chain_taxonomy()
    if not chains:
        return None

    market = (getattr(stock, "market", "CN") or "CN").strip().upper()
    symbol = _normalize_symbol(getattr(stock, "symbol", "") or "", market)
    blob = _text_blob(stock, industry=industry)

    best: dict[str, Any] | None = None
    best_score = 0

    for sector_key, sector in chains.items():
        display_name = str(sector.get("display_name") or sector_key)
        layers = sector.get("layers") or {}
        for layer_key, layer in layers.items():
            score = 0
            matched: list[str] = []
            match_source = ""

            symbols_map = layer.get("symbols") or {}
            market_symbols = symbols_map.get(market) or []
            norm_symbols = {
                _normalize_symbol(str(s), market) for s in market_symbols if str(s).strip()
            }
            if symbol and symbol in norm_symbols:
                score = _SYMBOL_SCORE
                matched.append(f"symbol:{symbol}")
                match_source = "symbol"

            for kw in layer.get("keywords") or []:
                if _keyword_hit(str(kw), blob):
                    score += 10
                    matched.append(str(kw))

            if score > best_score:
                best_score = score
                best = {
                    "sector": sector_key,
                    "sector_label": display_name,
                    "layer": layer_key,
                    "layer_label": str(layer.get("label") or layer_key),
                    "description": str(layer.get("description") or ""),
                    "score": score,
                    "matched": matched[:8],
                    "match_source": match_source or ("keyword" if score >= _MIN_SCORE else ""),
                    "display": f"{display_name}·{layer.get('label') or layer_key}",
                }

    if not best or best_score < _MIN_SCORE:
        return None
    return best


def refresh_stock_industry_chain(db: Session, stock: Stock) -> dict[str, Any] | None:
    industry = ""
    if can_auto_fetch_concept_tags(stock.market):
        proxy = (get_global_proxy() or "").strip() or None
        try:
            industry = fetch_cn_industry(stock.symbol, proxy=proxy) or ""
        except Exception:
            logger.debug("拉取行业失败 %s", stock.symbol, exc_info=True)

    result = classify_stock(stock, industry=industry)
    stock.industry_chain_auto = result or {}
    stock.industry_chain_updated_at = utc_now()
    db.commit()
    db.refresh(stock)
    return result


def refresh_stock_industry_chain_by_id(stock_id: int) -> bool:
    db = SessionLocal()
    try:
        stock = db.query(Stock).filter(Stock.id == stock_id).first()
        if not stock:
            return False
        refresh_stock_industry_chain(db, stock)
        return True
    except Exception:
        logger.exception("刷新股票 %s 产业链分类失败", stock_id)
        return False
    finally:
        db.close()


def refresh_missing_industry_chains(limit: int = 50) -> int:
    db = SessionLocal()
    refreshed = 0
    try:
        rows = db.query(Stock).order_by(Stock.id.asc()).all()
        for stock in rows:
            if refreshed >= limit:
                break
            existing = stock.industry_chain_auto
            if isinstance(existing, dict) and existing.get("sector") and existing.get("layer"):
                continue
            try:
                refresh_stock_industry_chain(db, stock)
                refreshed += 1
            except Exception:
                logger.exception("批量刷新产业链分类失败: %s", stock.symbol)
        return refreshed
    finally:
        db.close()


def schedule_refresh_stock_industry_chain(stock_id: int) -> None:
    t = threading.Thread(
        target=refresh_stock_industry_chain_by_id,
        args=(stock_id,),
        name=f"industry-chain-{stock_id}",
        daemon=True,
    )
    t.start()


def schedule_refresh_missing_industry_chains(limit: int = 50) -> None:
    t = threading.Thread(
        target=refresh_missing_industry_chains,
        args=(limit,),
        name="industry-chain-batch",
        daemon=True,
    )
    t.start()
