#!/usr/bin/env python3
"""Monitor selected A-share ETFs and email when rule-based entry conditions appear."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import smtplib
import sys
import time
from dataclasses import dataclass
from datetime import date, datetime, time as dtime
from email.message import EmailMessage
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.collectors.kline_collector import KlineCollector
from src.core.providers import ProviderRequest, get_quote_orchestrator
from src.core.signals.a_share_decision import evaluate_a_share_decision
from src.models.market import MarketCode


CN_TZ = ZoneInfo("Asia/Shanghai")
DEFAULT_SYMBOLS = {
    "159995": "芯片ETF华夏",
    "159516": "半导体设备ETF国泰",
}
STATE_FILE = ROOT / "data" / "etf_alert_state.json"


@dataclass(frozen=True)
class AlertCandidate:
    symbol: str
    name: str
    quote: dict[str, Any]
    technical: dict[str, Any]
    decision: dict[str, Any]
    trigger_reason: str


def load_dotenv(path: Path = ROOT / ".env") -> None:
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def market_is_open(now: datetime | None = None) -> bool:
    current = now or datetime.now(CN_TZ)
    if current.weekday() >= 5:
        return False
    t = current.time()
    return dtime(9, 30) <= t <= dtime(11, 30) or dtime(13, 0) <= t <= dtime(15, 0)


def as_float(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def pct_distance(price: float | None, level: float | None) -> float | None:
    if price is None or level is None or level == 0:
        return None
    return (price - level) / level * 100


async def fetch_quotes(symbols: list[str]) -> dict[str, dict[str, Any]]:
    response = await get_quote_orchestrator().fetch(
        ProviderRequest(symbols=tuple(symbols), market="CN"),
        cache_ttl_sec=0,
    )
    if not response.success:
        raise RuntimeError(response.error or "quote provider failed")
    return {str(item.get("symbol")): item for item in response.data or []}


def evaluate_symbol(
    symbol: str,
    fallback_name: str,
    quote: dict[str, Any],
    technical: dict[str, Any],
) -> AlertCandidate | None:
    price = as_float(quote.get("current_price"))
    support = as_float(
        technical.get("support") or technical.get("support_m") or technical.get("support_s")
    )
    resistance = as_float(
        technical.get("resistance")
        or technical.get("resistance_m")
        or technical.get("resistance_s")
    )
    decision = evaluate_a_share_decision(
        {
            "symbol": symbol,
            "name": quote.get("name") or fallback_name,
            "market": "CN",
            "quote": quote,
            "technical": technical,
            "position": {
                "has_position": False,
                "stop_loss": round(support * 0.99, 3) if support else None,
                "target_price": round(resistance, 3) if resistance else None,
                "trading_style": "swing",
            },
        }
    ).to_dict()

    label = decision.get("label")
    risk = decision.get("risk_level")
    high = as_float(quote.get("high_price") or quote.get("high"))
    rsi6 = as_float(technical.get("rsi6"))
    kdj_j = as_float(technical.get("kdj_j"))
    boll_status = str(technical.get("boll_status") or "")
    trend = str(technical.get("trend") or "")
    macd_status = str(technical.get("macd_status") or "")
    change_5d = as_float(technical.get("change_5d"))
    support_distance = pct_distance(price, support)
    high_pullback = None
    if price and high:
        high_pullback = (high - price) / high * 100
    resistance_distance = None
    if price and resistance:
        resistance_distance = (resistance - price) / price * 100

    hard_overheated = (
        (rsi6 is not None and rsi6 > 88)
        or (kdj_j is not None and kdj_j > 118)
        or (
            "突破上轨" in boll_status
            and high_pullback is not None
            and high_pullback < 2.5
        )
        or (support_distance is not None and support_distance > 28)
    )
    if label == "买入候选" and not hard_overheated:
        reason = "激进策略：规则标签进入买入候选，未触发硬性过热"
    elif (
        label == "观察"
        and support_distance is not None
        and 0 <= support_distance <= 15
        and rsi6 is not None
        and 32 <= rsi6 <= 78
        and (kdj_j is None or kdj_j < 105)
        and not hard_overheated
    ):
        reason = f"激进策略：回踩到可接受区间，距支撑约 {support_distance:.1f}%"
    elif (
        label == "观察"
        and resistance_distance is not None
        and resistance_distance >= 4
        and not hard_overheated
        and ("金叉" in macd_status or trend == "多头排列")
    ):
        reason = f"激进策略：趋势仍在，上方压力空间约 {resistance_distance:.1f}%"
    elif (
        label == "禁止追高"
        and high_pullback is not None
        and high_pullback >= 3
        and support_distance is not None
        and support_distance <= 28
        and resistance_distance is not None
        and resistance_distance >= 4
        and rsi6 is not None
        and rsi6 <= 85
        and (kdj_j is None or kdj_j <= 112)
        and (change_5d is None or change_5d <= 28)
    ):
        reason = (
            f"激进策略：强势标的盘中回落 {high_pullback:.1f}%，"
            f"压力空间约 {resistance_distance:.1f}%"
        )
    else:
        return None

    return AlertCandidate(
        symbol=symbol,
        name=quote.get("name") or fallback_name,
        quote=quote,
        technical=technical,
        decision=decision,
        trigger_reason=reason,
    )


def load_state() -> dict[str, Any]:
    if not STATE_FILE.exists():
        return {}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def save_state(state: dict[str, Any]) -> None:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def should_send(candidate: AlertCandidate, state: dict[str, Any]) -> bool:
    today = date.today().isoformat()
    key = f"{today}:{candidate.symbol}:{candidate.trigger_reason}"
    return not state.get(key)


def mark_sent(candidate: AlertCandidate, state: dict[str, Any]) -> None:
    today = date.today().isoformat()
    key = f"{today}:{candidate.symbol}:{candidate.trigger_reason}"
    state[key] = datetime.now(CN_TZ).isoformat(timespec="seconds")
    save_state(state)


def smtp_config() -> dict[str, Any]:
    to_addr = os.getenv("ETF_ALERT_TO", "").strip()
    user = os.getenv("SMTP_USER", "").strip()
    password = os.getenv("SMTP_PASSWORD", "").strip()
    host = os.getenv("SMTP_HOST", "smtp.qq.com").strip()
    port = int(os.getenv("SMTP_PORT", "465"))
    from_addr = os.getenv("SMTP_FROM", user).strip()
    missing = [
        name
        for name, value in {
            "ETF_ALERT_TO": to_addr,
            "SMTP_USER": user,
            "SMTP_PASSWORD": password,
            "SMTP_FROM/SMTP_USER": from_addr,
        }.items()
        if not value
    ]
    if missing:
        raise RuntimeError(f"missing email env: {', '.join(missing)}")
    return {
        "to": to_addr,
        "user": user,
        "password": password,
        "host": host,
        "port": port,
        "from": from_addr,
    }


def format_num(value: Any, digits: int = 2) -> str:
    number = as_float(value)
    if number is None:
        return "-"
    return f"{number:.{digits}f}"


def email_body(candidate: AlertCandidate) -> str:
    q = candidate.quote
    t = candidate.technical
    d = candidate.decision
    return "\n".join(
        [
            f"{candidate.symbol} {candidate.name} 出现规则盯盘机会。",
            "",
            f"触发原因：{candidate.trigger_reason}",
            f"当前价：{format_num(q.get('current_price'), 3)}",
            f"涨跌幅：{format_num(q.get('change_pct'))}%",
            f"规则标签：{d.get('label')}，风险：{d.get('risk_level')}，评分：{d.get('score')}",
            f"支撑位：{format_num(t.get('support') or t.get('support_m') or t.get('support_s'), 3)}",
            f"压力位：{format_num(t.get('resistance') or t.get('resistance_m') or t.get('resistance_s'), 3)}",
            f"RSI6：{format_num(t.get('rsi6'), 1)}，KDJ_J：{format_num(t.get('kdj_j'), 1)}",
            f"MACD/趋势：{t.get('macd_status') or '-'} / {t.get('trend') or '-'}",
            "",
            "确认条件：",
            *[f"- {item}" for item in d.get("confirm_conditions", [])],
            "",
            "失效条件：",
            *[f"- {item}" for item in d.get("invalidation_conditions", [])],
            "",
            "仅为规则盯盘提醒，不构成投资建议。",
        ]
    )


def send_email(candidate: AlertCandidate) -> None:
    cfg = smtp_config()
    msg = EmailMessage()
    msg["Subject"] = f"A股ETF机会提醒：{candidate.symbol} {candidate.name}"
    msg["From"] = cfg["from"]
    msg["To"] = cfg["to"]
    msg.set_content(email_body(candidate))

    if cfg["port"] == 465:
        with smtplib.SMTP_SSL(cfg["host"], cfg["port"], timeout=20) as server:
            server.login(cfg["user"], cfg["password"])
            server.send_message(msg)
    else:
        with smtplib.SMTP(cfg["host"], cfg["port"], timeout=20) as server:
            server.starttls()
            server.login(cfg["user"], cfg["password"])
            server.send_message(msg)


def check_once(symbols: dict[str, str], dry_run: bool = False) -> list[AlertCandidate]:
    quotes = asyncio.run(fetch_quotes(list(symbols)))
    collector = KlineCollector(MarketCode.CN)
    state = load_state()
    sent: list[AlertCandidate] = []
    for symbol, fallback_name in symbols.items():
        quote = quotes.get(symbol)
        if not quote:
            print(f"[{datetime.now(CN_TZ).isoformat(timespec='seconds')}] no quote: {symbol}")
            continue
        technical = collector.get_kline_summary(symbol)
        if technical.get("error"):
            print(f"[{datetime.now(CN_TZ).isoformat(timespec='seconds')}] no kline: {symbol}")
            continue
        candidate = evaluate_symbol(symbol, fallback_name, quote, technical)
        if not candidate:
            label = evaluate_a_share_decision(
                {
                    "symbol": symbol,
                    "name": quote.get("name") or fallback_name,
                    "market": "CN",
                    "quote": quote,
                    "technical": technical,
                    "position": {"has_position": False},
                }
            ).to_dict()["label"]
            print(
                f"[{datetime.now(CN_TZ).isoformat(timespec='seconds')}] "
                f"{symbol} no alert, label={label}"
            )
            continue
        if not should_send(candidate, state):
            print(
                f"[{datetime.now(CN_TZ).isoformat(timespec='seconds')}] "
                f"{symbol} skipped duplicate: {candidate.trigger_reason}"
            )
            continue
        if dry_run:
            print(email_body(candidate))
        else:
            send_email(candidate)
            mark_sent(candidate, state)
            print(
                f"[{datetime.now(CN_TZ).isoformat(timespec='seconds')}] "
                f"sent alert: {symbol} {candidate.trigger_reason}"
            )
        sent.append(candidate)
    return sent


def parse_symbols(raw: str | None) -> dict[str, str]:
    if not raw:
        return DEFAULT_SYMBOLS
    parsed: dict[str, str] = {}
    for item in raw.split(","):
        part = item.strip()
        if not part:
            continue
        if ":" in part:
            symbol, name = part.split(":", 1)
            parsed[symbol.strip()] = name.strip()
        else:
            parsed[part] = DEFAULT_SYMBOLS.get(part, part)
    return parsed or DEFAULT_SYMBOLS


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--interval", type=int, default=15, help="loop interval in seconds")
    parser.add_argument("--once", action="store_true", help="run one check and exit")
    parser.add_argument("--dry-run", action="store_true", help="print alerts instead of sending")
    parser.add_argument(
        "--ignore-market-hours",
        action="store_true",
        help="run even outside A-share trading hours",
    )
    parser.add_argument(
        "--symbols",
        default=os.getenv("ETF_ALERT_SYMBOLS"),
        help="comma list, e.g. 159995:芯片ETF华夏,159516:半导体设备ETF国泰",
    )
    args = parser.parse_args()

    load_dotenv()
    symbols = parse_symbols(args.symbols)

    while True:
        if args.ignore_market_hours or market_is_open():
            try:
                check_once(symbols, dry_run=args.dry_run)
            except Exception as exc:
                print(f"[{datetime.now(CN_TZ).isoformat(timespec='seconds')}] check failed: {exc}")
                if args.once:
                    return 1
        else:
            print(f"[{datetime.now(CN_TZ).isoformat(timespec='seconds')}] market closed")
        if args.once:
            return 0
        time.sleep(max(5, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())
