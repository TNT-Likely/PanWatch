"""Ghostfolio 单向同步:拉取 /api/v1/export,按不可变上游账户 ID 映射本地账户与持仓。

只读:绝不写回 Ghostfolio。通过显式本地映射识别账户,仅清理已映射账户的持仓。
"""
from __future__ import annotations

import logging
import math
import os
import re
from dataclasses import dataclass, field
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
    for i, acc in enumerate(accounts):
        if (not isinstance(acc, dict) or not isinstance(acc.get("id"), (str, int))
                or not acc["id"]):
            raise ExportValidationError(f"account[{i}] invalid id")
        if "name" in acc and not isinstance(acc["name"], str):
            raise ExportValidationError(f"account[{i}] invalid name")
        ids.add(acc["id"])
    activities = export.get("activities")
    if not isinstance(activities, list):
        raise ExportValidationError("activities missing (partial export)")
    for i, act in enumerate(activities):
        if not isinstance(act, dict):
            raise ExportValidationError(f"activity[{i}] is not an object")
        account_id = act.get("accountId")
        if not isinstance(account_id, (str, int)) or not account_id or account_id not in ids:
            raise ExportValidationError(f"activity[{i}] references unknown accountId")
        if not isinstance(act.get("type"), str) or not act["type"]:
            raise ExportValidationError(f"activity[{i}] missing type")
        profile = act.get("symbolProfile")
        if profile is not None and not isinstance(profile, dict):
            raise ExportValidationError(f"activity[{i}] invalid symbolProfile")
        for source, prefix in ((act, ""), (profile or {}, "symbolProfile.")):
            for field_name in ("symbol", "currency", "name", "dataSource"):
                if field_name in source and not isinstance(source[field_name], str):
                    raise ExportValidationError(f"activity[{i}] invalid {prefix}{field_name}")
        if act["type"] in ("BUY", "SELL"):
            for field_name in ("quantity", "unitPrice", "fee"):
                if field_name == "fee" and act.get("fee") is None:
                    continue
                try:
                    value = float(act.get(field_name))
                except (TypeError, ValueError):
                    raise ExportValidationError(f"activity[{i}] non-numeric {field_name}")
                if not math.isfinite(value) or (value <= 0 if field_name == "quantity" else value < 0):
                    raise ExportValidationError(f"activity[{i}] invalid {field_name}")
        if not act.get("date"):
            raise ExportValidationError(f"activity[{i}] missing date")
        try:
            datetime.fromisoformat(act["date"])
        except (TypeError, ValueError):
            profile = act.get("symbolProfile")
            symbol = (profile.get("symbol") if isinstance(profile, dict) else None) or act.get("symbol") or "未知标的"
            raise ExportValidationError(f"activity[{i}] {symbol} 日期无效")


_US_RE = re.compile(r"^[A-Z.]{1,6}$")
_AU_RE = re.compile(r"^[A-Z]{1,6}$")


@dataclass
class Holding:
    symbol: str
    name: str
    market: str
    quantity: float
    cost: float  # 净买入成本(含 fee)
    currency: str = ""


@dataclass
class SyncResult:
    accounts: int = 0
    created: int = 0
    updated: int = 0
    removed: int = 0
    skipped: int = 0
    skipped_types: dict[str, int] = field(default_factory=dict)
    applied: list[dict[str, str | int | float]] = field(default_factory=list)
    pending_removals: list[dict[str, str | int]] = field(default_factory=list)
    pending_removal_count: int = 0
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


def _activity_sort_key(pair: tuple[int, dict]) -> tuple[str, int]:
    """按解析后的活动日期升序;同日保持导出原序。"""
    idx, act = pair
    return (datetime.fromisoformat(act["date"]).date().isoformat(), idx)


def compute_holdings(export: dict) -> tuple[dict[tuple[str, str, str], Holding], dict[str, int]]:
    names = {a["id"]: a.get("name") or a["id"] for a in export.get("accounts") or []}
    skipped: dict[str, int] = {}
    holdings: dict[tuple[str, str, str], Holding] = {}
    for _, act in sorted(enumerate(export.get("activities") or []), key=_activity_sort_key):
        if act.get("type") not in ("BUY", "SELL"):
            t = act.get("type") or "UNKNOWN"
            skipped[t] = skipped.get(t, 0) + 1  # DIVIDEND/TRANSFER/公司行为:报告而非静默忽略
            continue
        sp = act.get("symbolProfile") or {}
        raw = (sp.get("symbol") or act.get("symbol") or "").upper()
        market = map_market(raw, sp.get("dataSource") or act.get("dataSource") or "")
        if market is None:
            raise ExportValidationError(f"无法映射交易标的: {raw}")
        base = raw.removesuffix(".AX")
        if raw.endswith(".HK"):
            stem = raw.removesuffix(".HK")
            base = stem.zfill(5) if stem.isdigit() else stem
        key = (act.get("accountId") or "default", base, market)
        h = holdings.get(key) or Holding(symbol=base, name=sp.get("name") or base,
                                         market=market, quantity=0.0, cost=0.0)
        ccy = ((act.get("currency") or "") or (sp.get("currency") or "")).upper()
        if h.currency and ccy and h.currency != ccy:
            raise ExportValidationError(
                f"mixed currencies for {market}:{base}: {h.currency} vs {ccy}")
        if not h.currency:
            h.currency = ccy
        qty = float(act.get("quantity") or 0)
        price = float(act.get("unitPrice") or 0)
        fee = float(act.get("fee") or 0)
        if act["type"] == "BUY":
            h.quantity += qty
            h.cost += qty * price + fee
        elif h.quantity > 0:
            avg = h.cost / h.quantity
            cut = min(qty, h.quantity)
            h.quantity -= cut
            h.cost -= avg * cut
        holdings[key] = h
    retained: dict[tuple[str, str, str], Holding] = {}
    for k, h in holdings.items():
        if not math.isfinite(h.quantity) or not math.isfinite(h.cost):
            raise ExportValidationError(
                f"non-finite reconstructed values for {h.market}:{h.symbol}")
        if h.quantity <= 1e-9:
            continue
        if not math.isfinite(h.cost / h.quantity):
            raise ExportValidationError(
                f"non-finite unit cost for {h.market}:{h.symbol}")
        retained[k] = h
    return retained, skipped


def get_or_create_config(db: Session) -> GhostfolioConfig:
    cfg = db.query(GhostfolioConfig).first()
    if cfg is None:
        cfg = GhostfolioConfig(base_url="", api_token="")
        db.add(cfg)
        db.commit()
    return cfg


def reconcile_sync_job(scheduler_wrapper, cfg: GhostfolioConfig) -> None:
    """按部署开关与持久配置替换或移除同步任务。"""
    from apscheduler.jobstores.base import JobLookupError
    from src.platform.runtime.config import Settings
    from src.platform.scheduling.schedule_parser import parse_schedule

    if GHOSTFOLIO_ENABLED and cfg.enabled:
        scheduler_wrapper.scheduler.add_job(
            run_scheduled_sync,
            parse_schedule(cfg.sync_cron or "17 8 * * *", timezone=Settings().app_timezone),
            id="ghostfolio_sync", replace_existing=True,
        )
    else:
        try:
            scheduler_wrapper.scheduler.remove_job("ghostfolio_sync")
        except JobLookupError:
            pass


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
        holdings, skipped_types = compute_holdings(export)
    except ExportValidationError as e:
        cfg.last_sync_at = datetime.now()
        cfg.last_status = f"aborted: {e}"
        db.commit()
        raise
    res.skipped_types = skipped_types
    if dry_run:
        return res

    names = {a["id"]: a.get("name") or a["id"] for a in export["accounts"]}
    wanted_accounts = {gf_id for (gf_id, _, _) in holdings}
    gf_account_ids: dict[str, int] = {}
    local_names: dict[str, str] = {}
    for gf_id in wanted_accounts:
        row = _resolve_account(db, cfg, gf_id, names[gf_id])
        gf_account_ids[gf_id] = row.id
        local_names[gf_id] = row.name
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
                           invested_amount=h.cost, currency=h.currency)
            db.add(pos)
            res.created += 1
        else:
            pos.cost_price = h.cost / h.quantity
            pos.quantity = h.quantity
            pos.invested_amount = h.cost
            pos.currency = h.currency
            res.updated += 1
        res.applied.append({
            "account_id": gf_account_ids[gf_id], "account_name": local_names[gf_id],
            "symbol": base, "market": market, "quantity": h.quantity,
            "cost": h.cost, "currency": h.currency,
        })

    # 删除腿:仅有效导出可触发;整账户清空需 confirm_empty 显式确认
    for acc in db.query(Account).join(GhostfolioAccountMap,
                                      GhostfolioAccountMap.account_id == Account.id).distinct().all():
        maps = db.query(GhostfolioAccountMap).filter_by(account_id=acc.id).all()
        gf_ids = {m.gf_account_id for m in maps}
        positions = db.query(Position).filter(Position.account_id == acc.id).all()
        drops = []
        for pos in positions:
            stock = db.query(Stock).filter(Stock.id == pos.stock_id).one()
            if any((gid, stock.symbol, stock.market) in holdings for gid in gf_ids):
                continue
            drops.append((pos, stock))
        if drops and len(drops) == len(positions) and not confirm_empty:
            for _, stock in drops:
                res.pending_removals.append({"account_id": acc.id, "account_name": acc.name,
                                             "symbol": stock.symbol, "market": stock.market})
            res.pending_removal_count += len(drops)
            continue
        for pos, _ in drops:
            db.delete(pos)
            res.removed += 1

    cfg.last_sync_at = datetime.now(timezone.utc).replace(tzinfo=None)  # naive UTC,与列类型一致
    res.status = "pending account-emptying removals" if res.pending_removal_count else "ok"
    cfg.last_status = res.status
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
