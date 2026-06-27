"""数据源管理 API"""

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from src.collectors.news_collector import (
    XUEQIU_COOKIE_UPDATE_HINT,
    build_xueqiu_cookie_health_record,
    normalize_xueqiu_cookies,
    probe_xueqiu_cookie,
    resolve_xueqiu_cookie_health,
)
from src.web.database import get_db
from src.web.models import DataSource

logger = logging.getLogger(__name__)

router = APIRouter()


# 数据源类型说明
TYPE_LABELS = {
    "news": "新闻资讯",
    "kline": "K线数据",
    "capital_flow": "资金流向",
    "quote": "实时行情",
    "events": "事件日历",
    "chart": "K线截图",
}


class DataSourceCreate(BaseModel):
    name: str
    type: str  # news / kline / capital_flow / quote / events / chart
    provider: str
    config: dict = {}
    enabled: bool = True
    priority: int = 0
    supports_batch: bool = False
    test_symbols: list[str] = []


class DataSourceUpdate(BaseModel):
    name: str | None = None
    type: str | None = None
    provider: str | None = None
    config: dict | None = None
    enabled: bool | None = None
    priority: int | None = None
    supports_batch: bool | None = None
    test_symbols: list[str] | None = None


class DataSourceResponse(BaseModel):
    id: int
    name: str
    type: str
    type_label: str = ""
    provider: str
    config: dict
    enabled: bool
    priority: int
    supports_batch: bool = False
    test_symbols: list[str] = []

    class Config:
        from_attributes = True


def _to_response(source: DataSource) -> dict:
    """转换为响应格式"""
    payload = {
        "id": source.id,
        "name": source.name,
        "type": source.type,
        "type_label": TYPE_LABELS.get(source.type, source.type),
        "provider": source.provider,
        "config": source.config or {},
        "enabled": source.enabled,
        "priority": source.priority,
        "supports_batch": source.supports_batch or False,
        "test_symbols": source.test_symbols or [],
    }
    if source.type == "news" and source.provider == "xueqiu":
        payload["cookie_health"] = resolve_xueqiu_cookie_health(source.config)
    return payload


def _is_xueqiu_news(source: DataSource) -> bool:
    return source.type == "news" and source.provider == "xueqiu"


def _persist_xueqiu_cookie_health(db: Session, source: DataSource, probe: dict) -> dict:
    config = dict(source.config or {})
    config["cookie_health"] = build_xueqiu_cookie_health_record(probe)
    source.config = config
    db.commit()
    db.refresh(source)
    return resolve_xueqiu_cookie_health(source.config) or {}


def _normalize_xueqiu_config(config: dict | None) -> dict | None:
    if config is None:
        return None
    cookies = str(config.get("cookies") or "").strip()
    if not cookies:
        return config
    normalized = normalize_xueqiu_cookies(cookies)
    if not normalized or normalized == cookies:
        return config
    merged = dict(config)
    merged["cookies"] = normalized
    merged.pop("cookie_health", None)
    return merged


def _clear_xueqiu_cookie_health_if_changed(
    source: DataSource, new_config: dict | None
) -> dict | None:
    if not _is_xueqiu_news(source) or new_config is None:
        return new_config
    old_cookies = str((source.config or {}).get("cookies") or "").strip()
    new_cookies = str(new_config.get("cookies") or "").strip()
    if old_cookies == new_cookies:
        return new_config
    merged = dict(new_config)
    merged.pop("cookie_health", None)
    return merged


@router.get("")
def list_datasources(type: str | None = None, db: Session = Depends(get_db)):
    """获取数据源列表，可按类型筛选"""
    query = db.query(DataSource)
    if type:
        query = query.filter(DataSource.type == type)
    sources = query.order_by(DataSource.type, DataSource.priority, DataSource.id).all()
    return [_to_response(s) for s in sources]


@router.get("/types")
def get_datasource_types():
    """获取数据源类型列表"""
    return [{"type": k, "label": v} for k, v in TYPE_LABELS.items()]


@router.get("/{source_id}")
def get_datasource(source_id: int, db: Session = Depends(get_db)):
    """获取单个数据源"""
    source = db.query(DataSource).filter(DataSource.id == source_id).first()
    if not source:
        raise HTTPException(status_code=404, detail="数据源不存在")
    return _to_response(source)


@router.post("")
def create_datasource(data: DataSourceCreate, db: Session = Depends(get_db)):
    """创建数据源"""
    config = data.config
    if data.type == "news" and data.provider == "xueqiu":
        config = _normalize_xueqiu_config(config) or config
    source = DataSource(
        name=data.name,
        type=data.type,
        provider=data.provider,
        config=config,
        enabled=data.enabled,
        priority=data.priority,
        supports_batch=data.supports_batch,
        test_symbols=data.test_symbols,
    )
    db.add(source)
    db.commit()
    db.refresh(source)
    logger.info(f"创建数据源: {source.name} ({source.provider})")
    return _to_response(source)


@router.put("/{source_id}")
def update_datasource(
    source_id: int, data: DataSourceUpdate, db: Session = Depends(get_db)
):
    """更新数据源"""
    source = db.query(DataSource).filter(DataSource.id == source_id).first()
    if not source:
        raise HTTPException(status_code=404, detail="数据源不存在")

    for key, value in data.model_dump(exclude_unset=True).items():
        if key == "config":
            value = _normalize_xueqiu_config(value)
            value = _clear_xueqiu_cookie_health_if_changed(source, value)
        setattr(source, key, value)

    db.commit()
    db.refresh(source)
    logger.info(f"更新数据源: {source.name}")
    return _to_response(source)


@router.delete("/{source_id}")
def delete_datasource(source_id: int, db: Session = Depends(get_db)):
    """删除数据源"""
    source = db.query(DataSource).filter(DataSource.id == source_id).first()
    if not source:
        raise HTTPException(status_code=404, detail="数据源不存在")

    db.delete(source)
    db.commit()
    logger.info(f"删除数据源: {source.name}")
    return {"ok": True, "message": f"已删除 {source.name}"}


@router.post("/{source_id}/test")
async def test_datasource(source_id: int, db: Session = Depends(get_db)):
    """测试数据源连接"""
    source = db.query(DataSource).filter(DataSource.id == source_id).first()
    if not source:
        raise HTTPException(status_code=404, detail="数据源不存在")

    from src.core.data_collector import get_collector_manager

    manager = get_collector_manager()
    manager.clear_logs()

    result = await manager.test_source(source)

    if _is_xueqiu_news(source):
        cookies = str((source.config or {}).get("cookies") or "").strip()
        test_symbol = (source.test_symbols or ["600519"])[0]
        if result.success:
            probe = {
                "status": "ok",
                "label": "正常",
                "message": f"测试成功，获取到 {result.count} 条新闻",
                "sample_count": result.count,
            }
        else:
            probe = await probe_xueqiu_cookie(cookies, test_symbol=test_symbol)
        cookie_health = _persist_xueqiu_cookie_health(db, source, probe)
    else:
        cookie_health = None

    # 不用 success / data 作为顶层字段,避免被 ResponseWrapperMiddleware 当成业务响应
    # 拆解后导致 metadata 丢失(详见 src/web/response.py:59 的特殊分支)。
    return {
        "test_passed": result.success,
        "source_name": source.name,
        "source_type": source.type,
        "type_label": TYPE_LABELS.get(source.type, source.type),
        "provider": source.provider,
        "supports_batch": source.supports_batch or False,
        "test_symbols": source.test_symbols or [],
        "count": result.count,
        "duration_ms": result.duration_ms,
        "error": result.error,
        "items": result.data,
        "logs": manager.get_logs(),
        "cookie_health": cookie_health,
    }


@router.post("/{source_id}/probe-cookie")
async def probe_datasource_cookie(source_id: int, db: Session = Depends(get_db)):
    """轻量检测雪球新闻采集连通性（Playwright，Cookie 可选）。"""
    source = db.query(DataSource).filter(DataSource.id == source_id).first()
    if not source:
        raise HTTPException(status_code=404, detail="数据源不存在")
    if not _is_xueqiu_news(source):
        raise HTTPException(status_code=400, detail="仅雪球新闻数据源支持连通检测")

    cookies = str((source.config or {}).get("cookies") or "").strip()
    test_symbol = (source.test_symbols or ["600519"])[0]
    probe = await probe_xueqiu_cookie(cookies, test_symbol=test_symbol)
    cookie_health = _persist_xueqiu_cookie_health(db, source, probe)
    return {
        "cookie_health": cookie_health,
        "update_hint": XUEQIU_COOKIE_UPDATE_HINT,
    }
