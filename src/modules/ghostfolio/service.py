"""Ghostfolio 单向同步:拉取 /api/v1/export,映射为 GF: 前缀账户与持仓。

只读:绝不写回 Ghostfolio。仅管理 account_prefix 前缀账户,其它账户不动。
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass

import requests
from sqlalchemy.orm import Session

from src.platform.persistence.models import Account, GhostfolioConfig, Position, Stock

logger = logging.getLogger(__name__)

GHOSTFOLIO_ENABLED = os.getenv("GHOSTFOLIO_ENABLED", "1") == "1"

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
    names = {a["id"]: a.get("name") or a["id"] for a in export.get("accounts") or []}
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
        key = (names.get(act.get("accountId"), act.get("accountId") or "default"), base, market)
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


def sync_now(db: Session, dry_run: bool = False) -> SyncResult:
    cfg = get_or_create_config(db)
    res = SyncResult()
    export = fetch_export(cfg.base_url, cfg.api_token)
    holdings = compute_holdings(export)
    if dry_run:
        return res

    prefix = cfg.account_prefix or "GF: "
    wanted_accounts = {acc for (acc, _, _) in holdings}
    gf_account_ids: dict[str, int] = {}
    for acc_name in wanted_accounts:
        row = db.query(Account).filter(Account.name == f"{prefix}{acc_name}").first()
        if row is None:
            row = Account(name=f"{prefix}{acc_name}")
            db.add(row)
            db.flush()
        gf_account_ids[acc_name] = row.id
        res.accounts += 1

    seen: set[tuple[int, str, str]] = set()
    for (acc_name, base, market), h in holdings.items():
        stock = db.query(Stock).filter_by(symbol=base, market=market).first()
        if stock is None:
            stock = Stock(symbol=base, name=h.name, market=market)
            db.add(stock)
            db.flush()
        pos = db.query(Position).filter_by(account_id=gf_account_ids[acc_name],
                                           stock_id=stock.id).first()
        if pos is None:
            pos = Position(account_id=gf_account_ids[acc_name], stock_id=stock.id,
                           cost_price=h.cost / h.quantity, quantity=h.quantity,
                           invested_amount=h.cost)
            db.add(pos)
            res.created += 1
        else:
            pos.cost_price = h.cost / h.quantity
            pos.quantity = h.quantity
            pos.invested_amount = h.cost
            res.updated += 1
        seen.add((gf_account_ids[acc_name], base, market))

    # 只清理前缀账户里已不在导出中的持仓
    for acc in db.query(Account).filter(Account.name.like(f"{prefix}%")).all():
        for pos in db.query(Position).filter_by(account_id=acc.id).all():
            sym = pos.stock.symbol
            mkt = pos.stock.market
            if (acc.id, sym, mkt) not in seen:
                db.delete(pos)
                res.removed += 1

    from datetime import datetime, timezone
    cfg.last_sync_at = datetime.now(timezone.utc).replace(tzinfo=None)  # naive UTC,与列类型一致
    cfg.last_status = "ok"
    db.commit()
    return res


def run_scheduled_sync() -> None:
    """供调度器调用(注册方式与 MCP 日志清理相同,见 server.py)。"""
    if not GHOSTFOLIO_ENABLED:
        return
    from src.platform.persistence.database import SessionLocal
    db = SessionLocal()
    try:
        sync_now(db)
    except Exception as e:  # 同步失败不影响其它任务
        logger.warning("ghostfolio sync failed: %s", e)
    finally:
        db.close()
