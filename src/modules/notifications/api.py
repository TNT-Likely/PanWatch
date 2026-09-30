from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from src.platform.persistence.database import get_db
from src.web.errors import api_error
from .schemas import ArchiveNotifications, NotificationSelection, Source, View
from .service import NotificationService

router = APIRouter()

@router.get('/summary')
def summary(db: Session = Depends(get_db)):
    return NotificationService(db).summary()

@router.get('')
def list_notifications(source: Source | None = None, view: View = 'all', cursor: str | None = Query(None, max_length=2048), limit: int = Query(30, ge=1, le=100), db: Session = Depends(get_db)):
    try:
        return NotificationService(db).list(source=source, view=view, cursor=cursor, limit=limit)
    except ValueError as exc:
        raise api_error(400, 'notification_cursor_invalid', '通知分页游标无效') from exc

@router.post('/read')
def read_notifications(body: NotificationSelection, db: Session = Depends(get_db)):
    return {'updated': NotificationService(db).mark_read(body)}

@router.post('/archive')
def archive_notifications(body: ArchiveNotifications, db: Session = Depends(get_db)):
    try:
        return {'updated': NotificationService(db).archive(body)}
    except ValueError as exc:
        raise api_error(409, 'notification_pending_action', '待审批通知处理后才可归档') from exc

@router.get('/{notification_id}/target')
def get_target(notification_id: int, db: Session = Depends(get_db)):
    try:
        return NotificationService(db).target(notification_id)
    except LookupError as exc:
        raise api_error(404, 'notification_source_unavailable', '通知来源已删除或不可用') from exc
