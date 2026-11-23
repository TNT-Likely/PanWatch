"""Convert one explicitly requested threshold to an immutable, single-use approval."""

import math
import re
from typing import Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from src.modules.market.price_alert_service import create_alert_rule
from src.modules.mobile.symbol_facts import Market
from src.platform.persistence.models import MobileAssistantApproval as Approval
from src.platform.persistence.models import MobileAssistantConversation as Conversation
from src.platform.persistence.models import MobileAssistantTurn as Turn
from src.platform.persistence.models import MobileDevice, Stock
from src.web.errors import api_error

_CURRENCY = {"CN": "CNY", "HK": "HKD", "US": "USD"}
_INTENT = re.compile(r"提醒我|通知我|设置.{0,20}提醒|新建.{0,20}提醒|notify me|alert me|set (?:an? |a price )?alert", re.I)
_QUALIFIED = re.compile(r"(?<![a-zA-Z])(?P<market>CN|HK|US)[ :：]+(?P<symbol>[a-zA-Z0-9.\-]{1,15})(?![a-zA-Z0-9])", re.I)
_AMOUNT = r"(?P<price>[0-9]{1,10}(?:\.[0-9]{1,6})?)"
_TTL = 30 * 60


class ApprovalView(BaseModel):
    id: UUID
    state: Literal["pending", "approved", "rejected", "expired"]
    market: Market
    symbol: str
    name: str
    comparison: Literal["<", ">"]
    price: float
    currency: Literal["CNY", "HKD", "USD"]
    expires_at: int
    rule_id: int | None


class ApprovalDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")
    decision: Literal["approve", "reject"]


def approval_view(row: Approval, now: int) -> ApprovalView:
    state = "expired" if row.state == "pending" and row.expires_at <= now else row.state
    return ApprovalView(id=row.id, state=state, market=row.market, symbol=row.symbol, name=row.name,
                        comparison=row.comparison, price=row.price, currency=_CURRENCY[row.market],
                        expires_at=row.expires_at, rule_id=row.rule_id)


def _terms(text, stock):
    names = sorted({stock.name, stock.symbol, stock.name.split("（", 1)[0], f"{stock.market} {stock.symbol}",
                    f"{stock.market}:{stock.symbol}"}, key=len, reverse=True)
    subject = "(?:" + "|".join(re.escape(n) for n in names if n) + ")"
    zh = (rf"(?:如果|当|若)?\s*(?:{subject}|这只(?:股票|标的)|该(?:股票|标的))?\s*(?:的)?(?:股价|价格|现价)?\s*"
          rf"(?P<op><|>|低于|小于|跌破|高于|大于|突破)\s*{_AMOUNT}\s*(?P<currency>CNY|HKD|USD|人民币|港元|美元|元)?\s*(?:时|就)?")
    en = (rf"(?:if |when )?(?:{subject}|this (?:stock|symbol))?\s*(?:['’]s\s+)?(?:the )?(?:price\s+)?"
          rf"(?:is |goes |falls |rises )?(?P<op><|>|below|above|under|over)\s*"
          rf"{_AMOUNT}\s*(?P<currency>CNY|HKD|USD)?")
    # Requiring a whole-statement match prevents silently discarding another
    # symbol, currency, condition, negation or time restriction in the question.
    literal = text.strip().strip("。！!，,. ")
    candidates = []
    for pat in (r"(?:请)?(?:提醒我|设置价格提醒|新建价格提醒)\s*[：:,，]?\s*(.+)",
                r"(.+?)\s*[,，]?\s*(?:请)?(?:提醒我|通知我)",
                r"(?:please\s+)?(?:alert me|notify me|set (?:an? |a price )?alert)\s+(.+)"):
        m = re.fullmatch(pat, literal, re.I)
        if m:
            candidates.append(m.group(1))
    for clause in candidates:
        match = re.fullmatch(zh, clause, re.I) or re.fullmatch(en, clause, re.I)
        if not match:
            continue
        given = (match.group("currency") or "").upper()
        currency = {"人民币": "CNY", "港元": "HKD", "美元": "USD", "元": _CURRENCY[stock.market]}.get(given, given)
        if currency and currency != _CURRENCY[stock.market]:
            return None
        price = float(match.group("price"))
        if not math.isfinite(price) or price <= 0:
            return None
        op = "<" if match.group("op").lower() in {"<", "低于", "小于", "跌破", "below", "under"} else ">"
        return op, price
    return None


def stage_if_requested(db: Session, device: MobileDevice, conv: Conversation, turn: Turn, now: int) -> bool:
    if not _INTENT.search(turn.text):
        return False
    zh = turn.locale == "zh-CN"
    turn.state, turn.attempt, turn.deadline = "complete", None, None
    if "alerts:manage" not in device.scope.split():
        turn.answer = "当前设备没有设置提醒的权限。" if zh else "This device has no permission to manage alerts."
        return True
    context = conv.context or {}
    identity = _QUALIFIED.search(turn.text)
    if context.get("kind") == "symbol":
        market, symbol = context["market"], context["symbol"]
        if identity and (identity["market"].upper(), identity["symbol"].upper()) != (market, symbol.upper()):
            market, symbol = None, None
    else:
        market, symbol = (identity["market"].upper(), identity["symbol"].upper()) if identity else (None, None)
    stock = db.scalar(select(Stock).where(Stock.market == market, Stock.symbol == symbol).order_by(Stock.id).limit(1)) if market else None
    parsed = _terms(turn.text, stock) if stock else None
    if not parsed:
        turn.answer = ("请指定一个自选标的和一个明确价位，例如「当 CN 600901 价格低于 35 时提醒我」。"
                       if zh else "Specify one watchlist symbol and one price, for example “Alert me when CN 600901 is below 35”.")
        return True
    op, price = parsed
    db.add(Approval(id=str(uuid4()), turn_id=turn.id, stock_id=stock.id, market=stock.market,
                    symbol=stock.symbol, name=stock.name, comparison=op, price=price, state="pending",
                    expires_at=now + _TTL))
    turn.answer = ("请核对这条单次价格提醒。确认后才会保存到盯盘。" if zh else
                   "Review this one-time price alert. It will be saved to Monitoring after you confirm.")
    return True


def decide(db: Session, device: MobileDevice, conv: Conversation, turn_id: UUID,
           approval_id: UUID, decision: ApprovalDecision, now: int) -> None:
    if "alerts:manage" not in device.scope.split():
        raise api_error(403, "mobile_scope_required", "设备没有提醒管理权限。")
    bound = select(Turn.id).where(Turn.id == str(turn_id), Turn.conversation_id == conv.id)
    match = (Approval.id == str(approval_id), Approval.turn_id.in_(bound))
    state = "approved" if decision.decision == "approve" else "rejected"
    # Take the decision and insert the real rule in a single transaction.
    changed = db.execute(update(Approval).where(*match, Approval.state == "pending", Approval.expires_at > now)
                         .values(state=state)).rowcount
    row = db.scalar(select(Approval).where(*match))
    if row is None:
        raise api_error(404, "mobile_assistant_not_found", "这项审批不可用。")
    if not changed:
        if row.state == "pending" and row.expires_at <= now:
            row.state = "expired"
        elif row.state not in (state, "expired"):
            raise api_error(409, "mobile_assistant_approval_decided", "此项审批已有其他决定。")
    elif decision.decision == "approve":
        stock = db.get(Stock, row.stock_id)
        if stock is None or (stock.market, stock.symbol) != (row.market, row.symbol):
            row.state = "expired"
        else:
            rule = create_alert_rule(db, stock_id=stock.id, condition_group={"op": "and", "items": [
                {"type": "price", "op": row.comparison, "value": row.price}]},
                repeat_mode="once", mobile_request_id=f"{device.id}:assistant:{row.id}", commit=False)
            row.rule_id = rule.id
    db.commit()
