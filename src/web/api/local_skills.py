"""本地 Skill 广场 API。"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from datetime import datetime, timezone
from types import SimpleNamespace

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from src.agents.base import AgentContext
from src.config import AppConfig, Settings, StockConfig
from src.core.hermes_config import (
    load_hermes_config,
    local_skill_agent_name,
)
from src.core.hermes_runner import find_hermes_bin, is_hermes_available, test_hermes_connection
from src.core.local_skill_report import LocalSkillReportService
from src.core.local_skill_scanner import scan_local_skills
from src.models.market import MarketCode
from src.web.database import SessionLocal, get_db
from src.web.models import LocalSkill, Stock

logger = logging.getLogger(__name__)

router = APIRouter()


class LocalSkillResponse(BaseModel):
    id: int
    slug: str
    display_name: str
    description: str
    skill_path: str
    source_root: str
    enabled: bool
    config: dict
    last_seen_at: str
    hermes_available: bool = False

    class Config:
        from_attributes = True


class LocalSkillUpdate(BaseModel):
    enabled: bool | None = None
    display_name: str | None = None
    description: str | None = None
    config: dict | None = None


class LocalSkillTriggerBody(BaseModel):
    stock_id: int = 0
    symbol: str = ""
    market: str = "CN"
    name: str = ""


def _skill_to_response(row: LocalSkill, *, hermes_ok: bool) -> dict:
    last_seen = ""
    if row.last_seen_at:
        dt = row.last_seen_at
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        last_seen = dt.isoformat()
    return {
        "id": row.id,
        "slug": row.slug,
        "display_name": row.display_name,
        "description": row.description or "",
        "skill_path": row.skill_path or "",
        "source_root": row.source_root or "",
        "enabled": bool(row.enabled),
        "config": row.config or {},
        "last_seen_at": last_seen,
        "hermes_available": hermes_ok,
    }


def _sync_skills(db: Session) -> list[LocalSkill]:
    hermes = load_hermes_config()
    scanned = scan_local_skills(hermes.local_skill_scan_dirs)
    now = datetime.now(timezone.utc)

    existing = {r.slug: r for r in db.query(LocalSkill).all()}
    out: list[LocalSkill] = []

    for item in scanned:
        row = existing.get(item.slug)
        if not row:
            row = LocalSkill(
                slug=item.slug,
                display_name=item.display_name,
                description=item.description,
                skill_path=item.skill_path,
                source_root=item.source_root,
                enabled=False,
                config={},
            )
            db.add(row)
        else:
            row.display_name = item.display_name or row.display_name
            if item.description:
                row.description = item.description
            row.skill_path = item.skill_path
            row.source_root = item.source_root
        row.last_seen_at = now
        out.append(row)

    db.commit()
    for row in out:
        db.refresh(row)
    return sorted(out, key=lambda r: (not r.enabled, r.display_name.lower()))


@router.get("", response_model=list[LocalSkillResponse])
def list_local_skills(
    enabled_only: bool = Query(default=False),
    refresh: bool = Query(default=False),
    db: Session = Depends(get_db),
):
    hermes = load_hermes_config()
    hermes_ok = is_hermes_available(hermes.hermes_bin)

    if refresh:
        rows = _sync_skills(db)
    else:
        rows = db.query(LocalSkill).order_by(
            LocalSkill.enabled.desc(), LocalSkill.display_name.asc()
        ).all()
        if not rows:
            rows = _sync_skills(db)

    if enabled_only:
        rows = [r for r in rows if r.enabled]

    return [_skill_to_response(r, hermes_ok=hermes_ok) for r in rows]


@router.post("/refresh", response_model=list[LocalSkillResponse])
def refresh_local_skills(db: Session = Depends(get_db)):
    hermes = load_hermes_config()
    hermes_ok = is_hermes_available(hermes.hermes_bin)
    rows = _sync_skills(db)
    return [_skill_to_response(r, hermes_ok=hermes_ok) for r in rows]


@router.put("/{slug}", response_model=LocalSkillResponse)
def update_local_skill(
    slug: str,
    body: LocalSkillUpdate,
    db: Session = Depends(get_db),
):
    row = db.query(LocalSkill).filter(LocalSkill.slug == slug.strip()).first()
    if not row:
        raise HTTPException(404, f"Skill {slug} 不存在，请先刷新本地 skill 列表")

    if body.enabled is not None:
        row.enabled = bool(body.enabled)
    if body.display_name is not None:
        row.display_name = (body.display_name or row.slug).strip()
    if body.description is not None:
        row.description = (body.description or "").strip()
    if body.config is not None:
        row.config = body.config

    db.commit()
    db.refresh(row)
    hermes = load_hermes_config()
    return _skill_to_response(row, hermes_ok=is_hermes_available(hermes.hermes_bin))


@router.get("/hermes/status")
def hermes_status():
    hermes = load_hermes_config()
    bin_path = find_hermes_bin(hermes.hermes_bin)
    return {
        "available": bool(bin_path),
        "bin": bin_path or "",
        "profile": hermes.hermes_profile or "default",
        "config": {
            "hermes_bin": hermes.hermes_bin,
            "hermes_profile": hermes.hermes_profile,
            "hermes_skill_source_dir": hermes.hermes_skill_source_dir,
            "hermes_model": hermes.hermes_model,
            "hermes_max_turns": hermes.hermes_max_turns,
            "hermes_timeout_sec": hermes.hermes_timeout_sec,
            "hermes_followup_timeout_sec": hermes.hermes_followup_timeout_sec,
            "hermes_ignore_rules": hermes.hermes_ignore_rules,
            "hermes_auto_expand_summary": hermes.hermes_auto_expand_summary,
            "local_skill_scan_dirs": hermes.local_skill_scan_dirs,
        },
    }


@router.post("/hermes/test")
async def hermes_test(
    skill: str = Query(default="", description="测试用 skill slug"),
):
    hermes = load_hermes_config()
    result = await test_hermes_connection(
        hermes_bin=hermes.hermes_bin,
        hermes_profile=hermes.hermes_profile,
        skill=(skill or "").strip(),
        skill_source_dir=hermes.hermes_skill_source_dir,
        timeout_sec=60,
    )
    return result


def _resolve_trigger_stock(
    db: Session,
    *,
    stock_id: int,
    symbol: str,
    market: str,
    name: str,
) -> SimpleNamespace:
    if stock_id > 0:
        db_stock = db.query(Stock).filter(Stock.id == stock_id).first()
        if not db_stock:
            raise HTTPException(404, "股票不存在")
        return db_stock

    sym = (symbol or "").strip()
    if not sym:
        raise HTTPException(400, "symbol 不能为空")
    mkt = (market or "CN").strip().upper() or "CN"
    nm = (name or "").strip() or sym
    db_stock = db.query(Stock).filter(Stock.symbol == sym, Stock.market == mkt).first()
    if db_stock:
        return db_stock
    return SimpleNamespace(id=0, symbol=sym, name=nm, market=mkt)


async def _run_local_skill_report(
    slug: str,
    stock,
    *,
    trace_id: str,
) -> dict:
    from server import load_portfolio_for_stock, record_agent_run
    from src.agents.base import PortfolioInfo

    hermes = load_hermes_config()
    db = SessionLocal()
    try:
        skill_row = db.query(LocalSkill).filter(LocalSkill.slug == slug).first()
    finally:
        db.close()

    if not skill_row or not skill_row.enabled:
        raise ValueError(f"Skill {slug} 未启用")

    try:
        market = MarketCode(stock.market)
    except ValueError:
        market = MarketCode.CN

    stock_config = StockConfig(
        symbol=stock.symbol,
        name=stock.name,
        market=market,
    )
    portfolio = (
        load_portfolio_for_stock(stock.id)
        if getattr(stock, "id", 0)
        else PortfolioInfo()
    )
    settings = Settings()
    config = AppConfig(settings=settings, watchlist=[stock_config])
    context = AgentContext(
        ai_client=None,
        notifier=None,
        config=config,
        portfolio=portfolio,
        model_label=f"Hermes/{hermes.hermes_profile or 'default'}",
        suppress_notify=True,
    )

    service = LocalSkillReportService(hermes)
    cfg = skill_row.config if isinstance(skill_row.config, dict) else {}
    start = time.monotonic()
    result = await service.run_for_stock(
        context,
        slug,
        skill_display_name=skill_row.display_name or slug,
        skill_hermes_name=str(cfg.get("hermes_skill") or slug),
    )
    record_agent_run(
        agent_name=local_skill_agent_name(slug),
        status="success",
        result=result.get("content", "")[:500],
        duration_ms=int((time.monotonic() - start) * 1000),
        trace_id=trace_id,
        trigger_source="manual",
        model_label=context.model_label,
    )
    return result


@router.post("/{slug}/trigger")
async def trigger_local_skill(
    slug: str,
    body: LocalSkillTriggerBody | None = None,
    wait: bool = Query(default=True),
    db: Session = Depends(get_db),
):
    """对单只股票触发本地 skill 报告（默认同步等待）。"""
    slug = slug.strip()
    row = db.query(LocalSkill).filter(LocalSkill.slug == slug).first()
    if not row:
        raise HTTPException(404, f"Skill {slug} 不存在")
    if not row.enabled:
        raise HTTPException(400, f"Skill {slug} 未启用，请先在 Skill 广场启用")

    hermes = load_hermes_config()
    if not is_hermes_available(hermes.hermes_bin):
        raise HTTPException(
            400,
            "Hermes 不可用，请在设置中配置 hermes_bin 或确保 hermes 在 PATH 中",
        )

    payload = body or LocalSkillTriggerBody()
    stock = _resolve_trigger_stock(
        db,
        stock_id=int(payload.stock_id or 0),
        symbol=payload.symbol or "",
        market=payload.market or "CN",
        name=payload.name or "",
    )

    trace_id = f"skill-{slug}-{stock.symbol}-{int(time.time() * 1000)}"

    if not wait:
        def _runner():
            try:
                asyncio.run(_run_local_skill_report(slug, stock, trace_id=trace_id))
            except Exception:
                logger.exception("local skill 后台执行失败 slug=%s symbol=%s", slug, stock.symbol)

        t = threading.Thread(
            target=_runner,
            name=f"local-skill-{slug}-{stock.symbol}",
            daemon=True,
        )
        t.start()
        return {
            "queued": True,
            "trace_id": trace_id,
            "message": "已提交后台执行",
        }

    try:
        result = await _run_local_skill_report(slug, stock, trace_id=trace_id)
        return {
            "queued": False,
            "trace_id": trace_id,
            "success": True,
            "message": "报告已生成",
            "result": result,
        }
    except ValueError as e:
        raise HTTPException(400, str(e))
    except Exception as e:
        logger.exception("local skill 执行失败 slug=%s", slug)
        raise HTTPException(500, f"Skill 报告生成失败: {e}")
