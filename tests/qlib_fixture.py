"""Deterministic synthetic OHLCV for pipeline QA, never a market-performance claim."""

from datetime import date, timedelta
import math
import uuid


def panel(end=date(2026, 10, 12), days=360, symbols=8, market="CN"):
    from src.platform.scheduling.trading_calendar import calendar_known, is_trading_day
    calendar = []
    day = end
    while len(calendar) < days:
        if day.weekday() < 5 and (not calendar_known(market, day) or is_trading_day(market, day)):
            calendar.append(day.isoformat())
        day -= timedelta(days=1)
    calendar.reverse()
    codes = [f"SH{600000 + index:06d}" for index in range(symbols)]
    if market == "HK":
        codes = [f"HK_{index+1:05d}" for index in range(symbols)]
    elif market == "US":
        codes = [f"US_QA{index}" for index in range(symbols)]
    bars = []
    for index, symbol in enumerate(codes):
        price = 15 + index * 3
        for n, stamp in enumerate(calendar):
            opening = price * (1 + 0.002 * math.sin(n * 0.39 + index))
            price = opening * (1 + 0.006 * math.sin(n * 0.13 + index) + (index - symbols / 2) * 0.0004)
            bars.append({"date": stamp, "symbol": symbol, "open": opening, "close": price,
                         "high": max(opening, price) * 1.01, "low": min(opening, price) * 0.99,
                         "volume": 1_000_000 + 100_000 * math.sin(n * 0.1 + index), "factor": 1.0})
    return {"name": "Synthetic QA only", "source": "PanWatch deterministic synthetic fixture",
            "license": "Synthetic test data, CC0", "license_acknowledged": True, "market": market,
            "data_kind": "synthetic", "universe_kind": "fixed_research", "adjustment": "snapshot_adjusted",
            "calendar": calendar, "members": [{"symbol": symbol, "start": calendar[0], "end": end.isoformat(), "price_limit": 0.1 if market=="CN" else None, "special_treatment": False, **({"lot":100*(index+1) if market=="HK" else 1,"currency":"HKD" if market=="HK" else "USD"} if market!="CN" else {})} for index,symbol in enumerate(codes)], "bars": bars}


def paper_decision(factory, asof="2026-10-08", symbols=("SH600000",), topk=1):
    """Controlled decision fixture; does not claim that a model produced it."""
    from src.platform.persistence.qlib_models import QlibControl, QlibRecord
    from src.modules.qlib_research.contracts import ResearchConfig
    from src.modules.qlib_research.policy import POLICY, execution_date
    model_id, run_id, decision_id = [uuid.uuid4().hex for _ in range(3)]
    config = ResearchConfig(topk=topk).model_dump()
    predictions = [{"symbol": symbol, "raw_score": len(symbols) - rank, "rank": rank + 1, "tradable": True} for rank, symbol in enumerate(symbols)]
    with factory() as db:
        state = db.get(QlibControl, 1)
        if not state:
            state = QlibControl(id=1, settings={"champion_mode": "research"}, revision=1)
            db.add(state)
        state.champion_id = model_id
        db.add(QlibRecord(id=model_id, kind="model", name="controlled model fixture", payload={}))
        db.add(QlibRecord(id=run_id, kind="selection", parent_id=model_id, name=asof,
                         payload={"model_id": model_id, "asof_date": asof, "config": config, "predictions": predictions, "monitor": {"fraction": 1}}))
        db.add(QlibRecord(id=decision_id, kind="decision", parent_id=run_id, name=asof,
                         payload={"model_id": model_id, "run_id": run_id, "asof_date": asof,
                                  "execution_date": execution_date(asof), "config": config, "research_mode": True,
                                  "symbols": list(symbols), "policy": POLICY}))
        db.commit()
    return decision_id


if __name__ == "__main__":
    import argparse
    import json
    from pathlib import Path
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from src.platform.scheduling.trading_calendar import is_trading_day
    parser = argparse.ArgumentParser(description="Generate synthetic engineering-test data, NEVER market-performance evidence")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output already exists; choose a new file")
    current = datetime.now(ZoneInfo("Asia/Shanghai"))
    end = current.date() if current.hour >= 15 else current.date() - timedelta(days=1)
    for _ in range(15):
        if is_trading_day("CN", end):
            break
        end -= timedelta(days=1)
    else:
        parser.error("Current exchange calendar unknown")
    args.output.write_text(json.dumps(panel(end=end), allow_nan=False), encoding="utf-8")
    args.output.chmod(0o600)
