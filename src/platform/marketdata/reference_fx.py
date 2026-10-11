"""Bounded, dated reference FX shared by valuation and research consumers."""
import logging
import math
import time
from datetime import datetime, timezone

import httpx

logger = logging.getLogger(__name__)
EXCHANGE_RATE_TTL = 3600
CAD_RATE_CACHE = {"rate": None, "ts": 0}
USD_RATE_CACHE = {"rate": None, "ts": 0}
HKD_RATE_CACHE = {"rate": None, "ts": 0}


def reference_cny_rate(currency: str, cache: dict, *, refresh: bool = True) -> float | None:
    """Bounded Frankfurter/ECB reference FX. Never substitute an invented rate.

    https://frankfurter.dev/v1/ documents base/symbols and source publication dates.
    """
    if time.time() - cache["ts"] < EXCHANGE_RATE_TTL:
        return cache["rate"]
    if not refresh:
        return None
    try:
        response = httpx.get("https://api.frankfurter.dev/v1/latest",
                             params={"base": currency, "symbols": "CNY"}, timeout=5)
        response.raise_for_status()
        data = response.json()
        date = datetime.fromisoformat(data["date"]).date()
        age = (datetime.now(timezone.utc).date() - date).days
        rate = float(data["rates"]["CNY"])
        if data.get("base") != currency or not 0 <= age <= 7 or not math.isfinite(rate) or rate <= 0:
            raise ValueError("Invalid or stale reference rate")
        cache.update(rate=rate, ts=time.time(), source_date=date.isoformat())
        return rate
    except Exception as exc:
        logger.warning("%s/CNY reference rate unavailable: %s", currency, exc)
        return None
