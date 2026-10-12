"""Ghostfolio 单向同步:拉取 /api/v1/export,按不可变上游账户 ID 映射本地账户与持仓。

只读:绝不写回 Ghostfolio。通过显式本地映射识别账户,仅清理已映射账户的持仓。
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone

import requests
from sqlalchemy.orm import Session

from src.platform.persistence.models import Account, GhostfolioAccountMap, GhostfolioConfig, Position, Stock

logger = logging.getLogger(__name__)

GHOSTFOLIO_ENABLED = os.getenv("GHOSTFOLIO_ENABLED", "0") == "1"


class ExportValidationError(ValueError):
    """导出不完整或结构非法——此时绝不应用任何变更。"""


def validate_export(export: object) -> None:
    """校验导出完整性;不完整/部分导出必须让同步整体中止而非部分应用。"""
    if not isinstance(export, dict):
        raise ExportValidationError("export is not a JSON object")
    accounts = export.get("accounts")
    if not isinstance(accounts, list) or not accounts:
        raise ExportValidationError("accounts missing or empty (partial export)")
    ids = set()
    for acc in accounts:
        if not isinstance(acc, dict) or not acc.get("id"):
            raise ExportValidationError("account entry without id")
        ids.add(acc["id"])
    activities = export.get("activities")
    if not isinstance(activities, list):
        raise ExportValidationError("activities missing (partial export)")
    for i, act in enumerate(activities):
        if not isinstance(act, dict):
            raise ExportValidationError(f"activity[{i}] is not an object")
        if not act.get("accountId") or act["accountId"] not in ids:
            raise ExportValidationError(f"activity[{i}] references unknown accountId")
        if not act.get("type"):
            raise ExportValidationError(f"activity[{i}] missing type")
        if act["type"] in ("BUY", "SELL"):
            for field_name in ("quantity", "unitPrice"):
                try:
                    float(act.get(field_name))
                except (TypeError, ValueError):
                    raise ExportValidationError(f"activity[{i}] non-numeric {field_name}")
            if act.get("fee") is not None:
                try:
                    float(act["fee"])
                except (TypeError, ValueError):
                    raise ExportValidationError(f"activity[{i}] non-numeric fee")
        if not act.get("date"):
            raise ExportValidationError(f"activity[{i}] missing date")


_US_RE = re.compile(r"^[A-Z.]{1,6}$")
_AU_RE = re.compile(r"^[A-Z]{1,6}$")


@dataclass
class Holding:
    symbol: str
    name: str
    market: str
    quantity: float
    cost: float  # 净买入成本(含 fee)


@dataclass
class SyncResult:
    accounts: int = 0
    created: int = 0
    updated: int = 0
    removed: int = 0
    skipped: int = 0
    pending_removals: int = 0   # Task 6 起使用
    status: str = "ok"


def map_market(symbol: str, data_source: str) -> str | None:
    s = (symbol or "").upper()
    if s.endswith(".AX"):
        return "AU" if _AU_RE.fullmatch(s.removesuffix(".AX")) else None
    if s.endswith(".HK"):
        return "HK"
    if (data_source or "").upper() != "YAHOO":  # COINGECKO/MANUAL 等 → None
        return None
    return "US" if _US_RE.fullmatch(s) else None


def _exchange_token(base_url: str, token: str) -> str:
    r = requests.post(
        f"{base_url.rstrip('/')}/api/v1/auth/anonymous",
        json={"accessToken": token},
        timeout=30,
    )
    r.raise_for_status()
    auth = r.json().get("authToken")
    if not auth:
        raise ValueError("ghostfolio auth exchange returned no authToken")
    return auth

def fetch_export(base_url: str, token: str) -> dict:
    base = base_url.rstrip("/")
    r = requests.get(
        f"{base}/api/v1/export",
        headers={"Authorization": f"Bearer {token}"},
        timeout=30,
    )
    if r.status_code == 401:
        r = requests.get(
            f"{base}/api/v1/export",
            headers={"Authorization": f"Bearer {_exchange_token(base, token)}"},
            timeout=30,
        )
    r.raise_for_status()
    return r.json()


def compute_holdings(export: dict) -> dict[tuple[str, str, str], Holding]:
    holdings: dict[tuple[str, str, str], Holding] = {}
    for act in export.get("activities") or []:
        if act.get("type") not in ("BUY", "SELL"):
            continue  # DIVIDEND/TRANSFER 不参与持仓与成本
        sp = act.get("symbolProfile") or {}
        raw = (sp.get("symbol") or act.get("symbol") or "").upper()
        market = map_market(raw, sp.get("dataSource") or act.get("dataSource") or "")
        if market is None:
            continue
        base = raw.removesuffix(".AX")
        if raw.endswith(".HK"):
            # Ghostfolio 港股 0700.HK → PanWatch 约定 5 位基码 00700
            stem = raw.removesuffix(".HK")
            base = stem.zfill(5) if stem.isdigit() else stem
        key = (act.get("accountId") or "default", base, market)
        h = holdings.get(key) or Holding(symbol=base, name=sp.get("name") or base,
                                         market=market, quantity=0.0, cost=0.0)
        qty = float(act.get("quantity") or 0)
        price = float(act.get("unitPrice") or 0)
        fee = float(act.get("fee") or 0)
        if act["type"] == "BUY":
            h.quantity += qty
            h.cost += qty * price + fee
        elif h.quantity > 0:  # SELL 按移动平均成本减记
            avg = h.cost / h.quantity
            cut = min(qty, h.quantity)
            h.quantity -= cut
            h.cost -= avg * cut
        holdings[key] = h
    return {k: h for k, h in holdings.items() if h.quantity > 1e-9}


def get_or_create_config(db: Session) -> GhostfolioConfig:
    cfg = db.query(GhostfolioConfig).first()
    if cfg is None:
        cfg = GhostfolioConfig(base_url="", api_token="")
        db.add(cfg)
        db.commit()
    return cfg


def _resolve_account(db: Session, cfg: GhostfolioConfig, gf_id: str, display_name: str) -> Account:
    """按上游 ID 解析本地账户;历史“前缀+名称”账户在尚无映射时按名称收编一次。"""
    m = db.query(GhostfolioAccountMap).filter_by(gf_account_id=gf_id).one_or_none()
    if m:
        return db.query(Account).filter_by(id=m.account_id).one()
    legacy = (
        db.query(Account)
        .filter(
            Account.name == f"{cfg.account_prefix}{display_name}",
            ~Account.id.in_(db.query(GhostfolioAccountMap.account_id)),
        )
        .one_or_none()
    )
    acc = legacy or Account(name=f"{cfg.account_prefix}{display_name}")
    if not legacy:
        db.add(acc)
        db.flush()
    db.add(GhostfolioAccountMap(gf_account_id=gf_id, account_id=acc.id))
    return acc


def sync_now(db: Session, dry_run: bool = False, confirm_empty: bool = False) -> SyncResult:
    cfg = get_or_create_config(db)
    if not GHOSTFOLIO_ENABLED or not cfg.enabled:
        cfg.last_status = "disabled"
        db.commit()
        res = SyncResult()
        res.status = "disabled"
        return res
    res = SyncResult()
    export = fetch_export(cfg.base_url, cfg.api_token)
    try:
        validate_export(export)
    except ExportValidationError as e:
        cfg.last_sync_at = datetime.now()
        cfg.last_status = f"aborted: {e}"
        db.commit()
        raise
    holdings = compute_holdings(export)
    if dry_run:
        return res

    names = {a["id"]: a.get("name") or a["id"] for a in export["accounts"]}
    wanted_accounts = {gf_id for (gf_id, _, _) in holdings}
    gf_account_ids: dict[str, int] = {}
    for gf_id in wanted_accounts:
        row = _resolve_account(db, cfg, gf_id, names[gf_id])
        gf_account_ids[gf_id] = row.id
        res.accounts += 1

    for (gf_id, base, market), h in holdings.items():
        stock = db.query(Stock).filter_by(symbol=base, market=market).first()
        if stock is None:
            stock = Stock(symbol=base, name=h.name, market=market)
            db.add(stock)
            db.flush()
        pos = db.query(Position).filter_by(account_id=gf_account_ids[gf_id],
                                           stock_id=stock.id).first()
        if pos is None:
            pos = Position(account_id=gf_account_ids[gf_id], stock_id=stock.id,
                           cost_price=h.cost / h.quantity, quantity=h.quantity,
                           invested_amount=h.cost)
            db.add(pos)
            res.created += 1
        else:
            pos.cost_price = h.cost / h.quantity
            pos.quantity = h.quantity
            pos.invested_amount = h.cost
            res.updated += 1

    # 删除腿:仅有效导出可触发;整账户清空需 confirm_empty 显式确认
    res.pending_removals = 0
    for acc in db.query(Account).filter(Account.name.like(f"{cfg.account_prefix}%")).all():
        maps = db.query(GhostfolioAccountMap).filter_by(account_id=acc.id).all()
        gf_ids = {m.gf_account_id for m in maps}
        if not gf_ids:
            continue  # 无映射的前缀账户不托管(历史遗留,人工处理)
        positions = db.query(Position).filter(Position.account_id == acc.id).all()
        drops = []
        for pos in positions:
            stock = db.query(Stock).filter(Stock.id == pos.stock_id).one()
            if any((gid, stock.symbol, stock.market) in holdings for gid in gf_ids):
                continue
            drops.append(pos)
        if drops and len(drops) == len(positions) and not confirm_empty:
            res.pending_removals += len(drops)
            continue
        for pos in drops:
            db.delete(pos)
            res.removed += 1

    cfg.last_sync_at = datetime.now(timezone.utc).replace(tzinfo=None)  # naive UTC,与列类型一致
    cfg.last_status = "pending account-emptying removals" if res.pending_removals else "ok"
    db.commit()
    return res


def run_scheduled_sync() -> None:
    """定时入口:每次运行都重新检查用户开关,而不是只在注册时检查。"""
    from src.platform.persistence.database import SessionLocal

    if not GHOSTFOLIO_ENABLED:
        return
    db = SessionLocal()
    try:
        if not get_or_create_config(db).enabled:
            logger.info("Ghostfolio 同步未启用,跳过本次运行")
            return
        sync_now(db)
    except Exception:
        logger.exception("Ghostfolio 定时同步失败")
    finally:
        db.close()
