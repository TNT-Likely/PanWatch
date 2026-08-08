"""站内消息中心 API。"""

from datetime import datetime

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from src.core.notify_center import push_notification
from src.web.database import get_db
from src.web.models import Notification

router = APIRouter()


class NotificationOut(BaseModel):
    id: int
    category: str
    level: str
    title: str
    body: str = ""
    link: str = ""
    source: str = ""
    trace_id: str = ""
    push_status: str = ""
    push_error: str = ""
    read: bool = False
    created_at: str = ""


def _to_out(n: Notification) -> NotificationOut:
    return NotificationOut(
        id=n.id,
        category=n.category or "system",
        level=n.level or "info",
        title=n.title or "",
        body=n.body or "",
        link=n.link or "",
        source=n.source or "",
        trace_id=n.trace_id or "",
        push_status=n.push_status or "",
        push_error=n.push_error or "",
        read=n.read_at is not None,
        created_at=n.created_at.isoformat() if n.created_at else "",
    )


@router.get("/unread-count")
def unread_count(db: Session = Depends(get_db)):
    """铃铛红点用。轻量, 供前端高频轮询。"""
    n = db.query(Notification).filter(Notification.read_at.is_(None)).count()
    return {"unread": n}


@router.get("")
def list_notifications(
    limit: int = Query(30, ge=1, le=200),
    only_unread: bool = Query(False),
    category: str | None = Query(None),
    db: Session = Depends(get_db),
):
    q = db.query(Notification)
    if only_unread:
        q = q.filter(Notification.read_at.is_(None))
    if category:
        q = q.filter(Notification.category == category)
    rows = q.order_by(Notification.id.desc()).limit(limit).all()
    unread = db.query(Notification).filter(Notification.read_at.is_(None)).count()
    return {"items": [_to_out(r).model_dump() for r in rows], "unread": unread}


@router.post("/{nid}/read")
def mark_read(nid: int, db: Session = Depends(get_db)):
    n = db.query(Notification).filter(Notification.id == nid).first()
    if not n:
        return {"ok": False, "error": "not found"}
    if n.read_at is None:
        n.read_at = datetime.utcnow()
        db.commit()
    return {"ok": True}


@router.post("/read-all")
def mark_all_read(db: Session = Depends(get_db)):
    now = datetime.utcnow()
    cnt = (
        db.query(Notification)
        .filter(Notification.read_at.is_(None))
        .update({Notification.read_at: now}, synchronize_session=False)
    )
    db.commit()
    return {"ok": True, "marked": cnt}


@router.delete("/clear")
def clear_read(db: Session = Depends(get_db)):
    """清空已读, 保留未读。"""
    cnt = (
        db.query(Notification)
        .filter(Notification.read_at.isnot(None))
        .delete(synchronize_session=False)
    )
    db.commit()
    return {"ok": True, "deleted": cnt}


@router.post("/test")
def send_test(db: Session = Depends(get_db)):
    """自检: 写一条站内通知并尝试外发, 返回外发状态便于排查渠道。"""
    nid = push_notification(
        "🔔 通知中心测试",
        "这是一条测试消息。若 push_status=skipped 说明未配置外发渠道（站内仍可见）。",
        category="system",
        level="success",
        source="manual_test",
    )
    n = db.query(Notification).filter(Notification.id == nid).first()
    return {
        "ok": True,
        "id": nid,
        "push_status": n.push_status if n else "",
        "push_error": n.push_error if n else "",
    }
