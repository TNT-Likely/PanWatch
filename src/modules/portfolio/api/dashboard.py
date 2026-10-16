"""首页聚合 API（轻量版：不包含机会消息中心）。"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from src.platform.runtime.config import Settings
from src.platform.ai.ai_failover import get_configured_failover_client
from src.platform.persistence.database import get_db
from src.platform.persistence.models import (
    AnalysisHistory,
    LogEntry,
    NewsTopicSnapshot,
    Position,
    Stock,
)

router = APIRouter()


def _format_datetime(dt) -> str:
    if not dt:
        return ""
    tz_name = Settings().app_timezone or "UTC"
    try:
        tzinfo = ZoneInfo(tz_name)
    except Exception:
        tzinfo = timezone.utc
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(tzinfo).isoformat(timespec="seconds")


def _to_market(market: str) -> str:
    m = (market or "ALL").strip().upper()
    return m if m in ("ALL", "CN", "HK", "US") else "ALL"





def _summarize_topics(raw_topics) -> list[dict]:
    out: list[dict] = []
    topics = raw_topics if isinstance(raw_topics, list) else []
    for item in topics:
        if isinstance(item, dict):
            name = str(item.get("topic") or item.get("name") or "").strip()
            if not name:
                continue
            out.append(
                {
                    "name": name,
                    "score": float(item.get("score") or 0.0),
                    "sentiment": str(item.get("sentiment") or "neutral"),
                }
            )
        elif isinstance(item, str):
            text = item.strip()
            if text:
                out.append({"name": text, "score": 0.0, "sentiment": "neutral"})
        if len(out) >= 8:
            break
    return out


def _load_latest_insights(db: Session) -> list[dict]:
    out = []
    agents = (
        ("premarket_outlook", "盘前分析"),
        ("daily_report", "收盘复盘"),
    )
    for agent_name, label in agents:
        row = (
            db.query(AnalysisHistory)
            .filter(AnalysisHistory.agent_name == agent_name)
            .order_by(
                AnalysisHistory.analysis_date.desc(),
                AnalysisHistory.updated_at.desc(),
                AnalysisHistory.id.desc(),
            )
            .first()
        )
        if not row:
            continue
        out.append(
            {
                "id": int(row.id),
                "agent_name": agent_name,
                "agent_label": label,
                "analysis_date": row.analysis_date or "",
                "title": row.title or "",
                "updated_at": _format_datetime(row.updated_at),
            }
        )
    return out


@router.get("/overview")
def get_dashboard_overview(
    market: str = Query("ALL"), action_limit: int = Query(6, ge=3, le=20),
    risk_limit: int = Query(6, ge=3, le=20), days: int = Query(45, ge=7, le=365),
    db: Session = Depends(get_db),
):
    from src.modules.qlib_research.service import opportunities
    result = opportunities()
    if market.upper() not in ("ALL", "CN"):
        result = {"items": [], "run": None, "status": "unsupported_market", "mode": "research"}
    return {"generated_at": _format_datetime(datetime.now(timezone.utc)), "market": _to_market(market),
            "selection": result, "insights": _load_latest_insights(db)}


logger = logging.getLogger(__name__)


# ── 今日必读 AI 策展(Phase C)────────────────────────────────────────────
class CurateCandidate(BaseModel):
    type: str
    symbol: str = ""
    name: str = ""
    market: str = "CN"
    signal: str = ""
    change_pct: float | None = None


class CurateRequest(BaseModel):
    candidates: list[CurateCandidate]
    model_id: int | None = None


@router.post("/curate")
async def curate_today(req: CurateRequest, db: Session = Depends(get_db)):
    """把首页候选事件交 AI 排序+精炼,返回 [{index, importance, why}];AI 失败按原序兜底。"""
    cands = req.candidates[:20]
    if not cands:
        return {"items": []}

    listing = "\n".join(
        f"{i}. [{c.type}] {c.name or c.symbol}"
        + (f" {c.change_pct:+.2f}%" if c.change_pct is not None else "")
        + (f" {c.signal}" if c.signal else "")
        for i, c in enumerate(cands)
    )
    from src.platform.language import resolve_report_language

    english = resolve_report_language(db) == "en-US"
    system_prompt = (
        "You are a market-monitoring assistant. Rank the user's candidate events by importance, prioritizing triggered alerts, large moves in holdings, and portfolio risk. "
        "Write the explanation in English even when an input description is Chinese. Output one item per line using: index|importance (integer 0-100)|one-sentence reason. Do not explain the format or invent facts."
        if english
        else "你是盯盘助手。从用户今日候选事件里挑出最值得关注的,按重要度排序,重点关照:已触发的提醒、持仓的大幅异动、组合风险。只输出每条一行,格式: 序号|重要度(0-100整数)|一句话说明为什么值得看。不解释、不臆造。"
    )
    user_content = (
        f"Today's candidates (from this user's holdings, watchlist, alerts, and opportunities):\n{listing}"
        if english else f"今日候选(均来自该用户的持仓/自选/提醒/机会):\n{listing}"
    )

    items: list[dict] = []
    try:
        content = await get_configured_failover_client(db, req.model_id).chat(
            system_prompt, user_content, temperature=0.3
        )
        for line in (content or "").splitlines():
            parts = line.split("|")
            idx_raw = parts[0].strip().rstrip(".、) ")
            if len(parts) >= 3 and idx_raw.isdigit():
                i = int(idx_raw)
                if 0 <= i < len(cands):
                    try:
                        imp = int(re.sub(r"[^0-9]", "", parts[1]) or 0)
                    except Exception:
                        imp = 0
                    items.append({"index": i, "importance": imp, "why": parts[2].strip()})
    except Exception as e:
        logger.debug(f"curate AI 失败,按原序兜底: {e}")

    if not items:  # 兜底:原序 + 递减重要度
        items = [
            {"index": i, "importance": max(0, 100 - i * 5), "why": c.signal or ""}
            for i, c in enumerate(cands)
        ]
    items.sort(key=lambda x: x["importance"], reverse=True)
    return {"items": items}


@router.get("/brief")
def get_brief(type: str = Query("eod", description="premarket | eod"), db: Session = Depends(get_db)):
    """盘前/盘后 AI 简报(复用 premarket_outlook / daily_report agent 的最新报告)。"""
    agent = "premarket_outlook" if type == "premarket" else "daily_report"
    label = "盘前分析" if type == "premarket" else "收盘复盘"
    row = (
        db.query(AnalysisHistory)
        .filter(AnalysisHistory.agent_name == agent)
        .order_by(
            AnalysisHistory.analysis_date.desc(),
            AnalysisHistory.updated_at.desc(),
            AnalysisHistory.id.desc(),
        )
        .first()
    )
    if not row:
        return {"empty": True, "type": type, "agent_label": label}
    return {
        "type": type,
        "agent_label": label,
        "title": row.title or "",
        "content": row.content or "",
        "date": row.analysis_date or "",
        "updated_at": _format_datetime(row.updated_at),
    }
