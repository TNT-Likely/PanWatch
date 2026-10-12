from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from src.modules.ghostfolio import service
from src.platform.persistence.database import get_db

router = APIRouter()


class GhostfolioConfigIn(BaseModel):
    base_url: str
    api_token: str
    account_prefix: str = "GF: "
    sync_cron: str = "17 8 * * *"
    enabled: bool = False


@router.get("/status")
def status(db: Session = Depends(get_db)):
    cfg = service.get_or_create_config(db)
    return {
        "enabled": cfg.enabled, "base_url": cfg.base_url,
        "account_prefix": cfg.account_prefix, "sync_cron": cfg.sync_cron,
        "last_sync_at": cfg.last_sync_at.isoformat() if cfg.last_sync_at else None,
        "last_status": cfg.last_status,
    }  # 不回显 api_token


@router.put("/config")
def put_config(body: GhostfolioConfigIn, db: Session = Depends(get_db)):
    cfg = service.get_or_create_config(db)
    for k, v in body.model_dump().items():
        setattr(cfg, k, v)
    db.commit()
    return {"ok": True}


@router.post("/sync/preview")
def preview(db: Session = Depends(get_db)):
    cfg = service.get_or_create_config(db)
    if not service.GHOSTFOLIO_ENABLED or not cfg.enabled:
        return {"error": "disabled"}
    if not cfg.base_url:
        return {"error": "not configured"}
    export = service.fetch_export(cfg.base_url, cfg.api_token)
    holdings = service.compute_holdings(export)
    return {"holdings": {f"{a}|{s}|{m}": {"quantity": h.quantity, "cost": h.cost}
                         for (a, s, m), h in holdings.items()}}


@router.post("/sync")
def sync(confirm_empty: bool = False, db: Session = Depends(get_db)):
    return service.sync_now(db, confirm_empty=confirm_empty).__dict__
