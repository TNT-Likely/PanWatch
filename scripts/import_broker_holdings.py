"""券商持仓导入:广发 Hippo 提取的持仓 → PanWatch 持仓 + 模拟盘。

用法:
    python scripts/import_broker_holdings.py --file data/imports/broker_holdings-20260924.json [--dry-run]

约定(与用户确认的口径一致):
- **跳过已存在**:PanWatch 持仓 (account_id, stock) 已有、或模拟盘已有同股 open 持仓 → 跳过;
- **不动模拟盘资金**:只建 PaperTradingPosition 行,不改 current_capital/initial_capital;
- **模拟盘开关保持不变**(脚本绝不触碰 enabled);
- 溯源:模拟盘行 strategy_code="broker_import"、signal_action="buy"、signal_snapshot_date=当日;
- 幂等:重复执行结果一致。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

CN_CODE = re.compile(r"^[036]\d{5}$")


@dataclass
class Holding:
    code: str
    name: str
    quantity: int
    cost_price: float
    market_value: float | None = None

    @property
    def current_price(self) -> float | None:
        if self.market_value and self.quantity:
            return round(self.market_value / self.quantity, 4)
        return None


@dataclass
class ImportPlan:
    to_create: list[Holding] = field(default_factory=list)
    skipped_positions: list[str] = field(default_factory=list)   # PanWatch 持仓已存在
    skipped_paper: list[str] = field(default_factory=list)       # 模拟盘已存在
    invalid: list[str] = field(default_factory=list)             # 校验失败剔除


def load_holdings(path: str) -> list[Holding]:
    """读 JSON 并校验归一化:代码/A股规则、数量正整数、成本>0;非法行剔除并标注。"""
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    rows = raw.get("holdings") if isinstance(raw, dict) else raw
    out: list[Holding] = []
    for r in rows or []:
        code = str(r.get("code") or "").strip()
        name = str(r.get("name") or "").strip()
        try:
            qty = int(r.get("quantity"))
            cost = float(r.get("cost_price"))
        except (TypeError, ValueError):
            out and None
            continue
        mv = r.get("market_value")
        mv_f = float(mv) if mv not in (None, "") else None
        if not CN_CODE.match(code) or qty <= 0 or cost <= 0:
            continue
        out.append(Holding(code=code, name=name or code, quantity=qty,
                           cost_price=cost, market_value=mv_f))
    return out


def plan_import(
    holdings: list[Holding],
    existing_position_symbols: set[str],
    existing_paper_open_symbols: set[str],
) -> ImportPlan:
    """纯查重判定:PanWatch 持仓以 symbol 计,模拟盘以 open 状态同股计。"""
    plan = ImportPlan()
    for h in holdings:
        if h.code in existing_position_symbols:
            plan.skipped_positions.append(f"{h.name}({h.code})")
            continue
        if h.code in existing_paper_open_symbols:
            plan.skipped_paper.append(f"{h.name}({h.code})")
            continue
        plan.to_create.append(h)
    return plan


def run(file: str, account_id: int, dry_run: bool) -> int:
    holdings = load_holdings(file)
    if not holdings:
        print("无有效持仓行(全部校验失败或文件为空)")
        return 2

    from sqlalchemy import text

    from src.platform.persistence.database import SessionLocal
    from src.platform.persistence.models import (
        PaperTradingPosition,
        Position,
        Stock,
    )

    db = SessionLocal()
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        # SQLite 写锁重试:8001 服务的调度器会偶发持锁,退避重试即可
        last_err: Exception | None = None
        for attempt in range(4):
            try:
                return _run_once(db, today, account_id, holdings, dry_run)
            except Exception as e:
                last_err = e
                if "locked" not in str(e):
                    raise
                db.rollback()
                time.sleep(2 + attempt * 2)
        raise last_err  # type: ignore[misc]
    finally:
        db.close()


def _run_once(db, today: str, account_id: int, holdings: list, dry_run: bool) -> int:
    from sqlalchemy import text

    from src.platform.persistence.models import PaperTradingPosition, Position, Stock

    db.execute(text("PRAGMA busy_timeout = 20000"))  # 写锁排队等待 20s,替代立即 SQLITE_BUSY
    pos_syms: set[str] = {
        s for (s,) in db.query(Stock.symbol).join(
            Position, Position.stock_id == Stock.id
        ).filter(Position.account_id == account_id).all()
    }
    paper_syms: set[str] = {
        s for (s,) in db.query(PaperTradingPosition.stock_symbol).filter(
            PaperTradingPosition.status == "open"
        ).all()
    }
    plan = plan_import(holdings, pos_syms, paper_syms)

    print(f"读取 {len(holdings)} 只 | 新建 {len(plan.to_create)} | "
          f"跳过(持仓已存在) {len(plan.skipped_positions)} | "
          f"跳过(模拟盘已存在) {len(plan.skipped_paper)}")
    for h in plan.to_create:
        cur = h.current_price
        print(f"  [新建] {h.name}({h.code}) 数量 {h.quantity} 成本 {h.cost_price}"
              + (f" 现价约 {cur}" if cur else ""))
    for s in plan.skipped_positions:
        print(f"  [跳过·持仓已存在] {s}")
    for s in plan.skipped_paper:
        print(f"  [跳过·模拟盘已存在] {s}")

    if dry_run:
        print("(dry-run 未写库)")
        return 0

    created_pos = created_paper = 0
    for h in plan.to_create:
        stock = db.query(Stock).filter(
            Stock.symbol == h.code, Stock.market == "CN"
        ).first()
        if not stock:
            stock = Stock(symbol=h.code, name=h.name, market="CN")
            db.add(stock)
            db.flush()
        db.add(Position(
            account_id=account_id, stock_id=stock.id,
            cost_price=h.cost_price, quantity=h.quantity,
            invested_amount=round(h.cost_price * h.quantity, 2),
        ))
        created_pos += 1
        db.add(PaperTradingPosition(
            stock_symbol=h.code, stock_market="CN", stock_name=h.name,
            quantity=h.quantity, entry_price=h.cost_price,
            current_price=h.current_price, highest_price=h.current_price,
            unrealized_pnl=0.0, status="open",
            signal_action="buy", strategy_code="broker_import",
            signal_snapshot_date=today,
        ))
        created_paper += 1
    db.commit()
    print(f"完成:持仓 +{created_pos},模拟盘 +{created_paper}(资金未动,开关未动)")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="券商持仓导入 PanWatch(持仓+模拟盘)")
    ap.add_argument("--file", required=True, help="持仓 JSON 文件")
    ap.add_argument("--account-id", type=int, default=1, help="PanWatch 账户 id(默认 1)")
    ap.add_argument("--dry-run", action="store_true", help="只打印清单不写库")
    ns = ap.parse_args()
    return run(ns.file, ns.account_id, ns.dry_run)


if __name__ == "__main__":
    sys.exit(main())
