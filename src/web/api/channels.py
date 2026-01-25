from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel

from src.web.database import get_db
from src.web.models import NotifyChannel
from src.core.notifier import NotifierManager, CHANNEL_TYPES

router = APIRouter()


class ChannelCreate(BaseModel):
    name: str
    type: str = "telegram"
    config: dict = {}
    enabled: bool = True
    is_default: bool = False


class ChannelUpdate(BaseModel):
    name: str | None = None
    type: str | None = None
    config: dict | None = None
    enabled: bool | None = None
    is_default: bool | None = None


class ChannelResponse(BaseModel):
    id: int
    name: str
    type: str
    config: dict
    enabled: bool
    is_default: bool

    class Config:
        from_attributes = True


@router.get("", response_model=list[ChannelResponse])
def list_channels(db: Session = Depends(get_db)):
    return db.query(NotifyChannel).order_by(NotifyChannel.id).all()


@router.get("/types")
def list_channel_types():
    """返回支持的渠道类型及其字段"""
    return CHANNEL_TYPES


@router.post("", response_model=ChannelResponse)
def create_channel(body: ChannelCreate, db: Session = Depends(get_db)):
    if body.is_default:
        db.query(NotifyChannel).update({"is_default": False})
    channel = NotifyChannel(**body.model_dump())
    db.add(channel)
    db.commit()
    db.refresh(channel)
    return channel


@router.put("/{channel_id}", response_model=ChannelResponse)
def update_channel(channel_id: int, body: ChannelUpdate, db: Session = Depends(get_db)):
    channel = db.query(NotifyChannel).filter(NotifyChannel.id == channel_id).first()
    if not channel:
        raise HTTPException(404, "通知渠道不存在")

    data = body.model_dump(exclude_unset=True)
    if data.get("is_default"):
        db.query(NotifyChannel).update({"is_default": False})

    for key, value in data.items():
        setattr(channel, key, value)

    db.commit()
    db.refresh(channel)
    return channel


@router.delete("/{channel_id}")
def delete_channel(channel_id: int, db: Session = Depends(get_db)):
    channel = db.query(NotifyChannel).filter(NotifyChannel.id == channel_id).first()
    if not channel:
        raise HTTPException(404, "通知渠道不存在")
    db.delete(channel)
    db.commit()
    return {"ok": True}


@router.post("/{channel_id}/test")
async def test_channel(channel_id: int, db: Session = Depends(get_db)):
    """发送测试通知"""
    channel = db.query(NotifyChannel).filter(NotifyChannel.id == channel_id).first()
    if not channel:
        raise HTTPException(404, "通知渠道不存在")

    notifier = NotifierManager()
    try:
        notifier.add_channel(channel.type, channel.config or {})
    except Exception as e:
        raise HTTPException(400, f"渠道配置无效: {e}")

    result = await notifier.notify_with_result(
        title="测试通知",
        content="这是一条来自盯盘侠的测试通知，如果您收到此消息说明通知渠道配置正确。",
    )

    if result.get("success"):
        return {"ok": True, "message": "测试通知发送成功"}
    else:
        raise HTTPException(500, f"通知发送失败: {result.get('error', '未知错误')}")
