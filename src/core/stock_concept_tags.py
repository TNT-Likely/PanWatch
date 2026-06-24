"""股票概念标签：自动采集 + 手动维护。"""

from __future__ import annotations

import logging
import threading

from typing import Any

from sqlalchemy.orm import Session

from src.collectors.concept_collector import can_auto_fetch_concept_tags, fetch_cn_concept_tags
from src.core.notifier import get_global_proxy
from src.core.timezone import utc_now
from src.web.database import SessionLocal
from src.web.models import Stock

logger = logging.getLogger(__name__)

_MAX_MANUAL_TAGS = 20
_MAX_TAG_LEN = 32


def _as_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        tag = item.strip()
        if tag:
            out.append(tag)
    return out


def normalize_manual_tags(tags: list[str] | None) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in tags or []:
        tag = (raw or "").strip()
        if not tag or len(tag) > _MAX_TAG_LEN:
            continue
        if tag in seen:
            continue
        seen.add(tag)
        out.append(tag)
        if len(out) >= _MAX_MANUAL_TAGS:
            break
    return out


def merge_concept_tags(stock: Stock) -> list[dict[str, str]]:
    auto = _as_str_list(stock.concept_tags_auto)
    manual = _as_str_list(stock.concept_tags_manual)
    merged: list[dict[str, str]] = []
    seen: set[str] = set()

    for tag in manual:
        if tag in seen:
            continue
        seen.add(tag)
        merged.append({"name": tag, "source": "manual"})

    for tag in auto:
        if tag in seen:
            continue
        seen.add(tag)
        merged.append({"name": tag, "source": "auto"})

    return merged


def refresh_stock_concept_tags(db: Session, stock: Stock) -> list[str]:
    if not can_auto_fetch_concept_tags(stock.market):
        stock.concept_tags_auto = []
        stock.concept_tags_updated_at = utc_now()
        db.commit()
        db.refresh(stock)
        return []

    proxy = (get_global_proxy() or "").strip() or None
    tags = fetch_cn_concept_tags(stock.symbol, proxy=proxy)
    stock.concept_tags_auto = tags
    stock.concept_tags_updated_at = utc_now()
    db.commit()
    db.refresh(stock)
    return tags


def refresh_stock_concept_tags_by_id(stock_id: int) -> bool:
    db = SessionLocal()
    try:
        stock = db.query(Stock).filter(Stock.id == stock_id).first()
        if not stock:
            return False
        refresh_stock_concept_tags(db, stock)
        return True
    except Exception:
        logger.exception("刷新股票 %s 概念标签失败", stock_id)
        return False
    finally:
        db.close()


def refresh_missing_concept_tags(limit: int = 20) -> int:
    db = SessionLocal()
    refreshed = 0
    try:
        q = (
            db.query(Stock)
            .filter(Stock.market == "CN")
            .order_by(Stock.id.asc())
        )
        rows = q.all()
        for stock in rows:
            if refreshed >= limit:
                break
            auto = _as_str_list(stock.concept_tags_auto)
            if auto:
                continue
            try:
                refresh_stock_concept_tags(db, stock)
                refreshed += 1
            except Exception:
                logger.exception("批量刷新概念标签失败: %s", stock.symbol)
        return refreshed
    finally:
        db.close()


def schedule_refresh_stock_concept_tags(stock_id: int) -> None:
    t = threading.Thread(
        target=refresh_stock_concept_tags_by_id,
        args=(stock_id,),
        name=f"concept-tags-{stock_id}",
        daemon=True,
    )
    t.start()


def schedule_refresh_missing_concept_tags(limit: int = 20) -> None:
    t = threading.Thread(
        target=refresh_missing_concept_tags,
        args=(limit,),
        name="concept-tags-batch",
        daemon=True,
    )
    t.start()
