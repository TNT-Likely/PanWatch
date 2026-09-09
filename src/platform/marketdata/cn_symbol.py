"""CN symbol helpers."""

from __future__ import annotations


def get_cn_exchange(symbol: str) -> str:
    """Return CN exchange code: SH / SZ / BJ.

    Rules:
    - BJ: 920xxx, 83xxxx, 87xxxx, 88xxxx
    - SH: 5xxxxx, 6xxxxx, 900xxx
    - SZ: others (default)
    """
    sym = (symbol or "").strip()
    if sym.startswith("920") or sym.startswith(("83", "87", "88")):
        return "BJ"
    if sym.startswith(("5", "6")) or sym.startswith("900"):
        return "SH"
    return "SZ"


def get_cn_prefix(symbol: str, upper: bool = False) -> str:
    """Return market prefix for CN symbol.

    - BJ symbols return "bj"/"BJ".
    - SH/SZ symbols return "sh"/"sz" or uppercase.
    """
    exchange = get_cn_exchange(symbol)
    if upper:
        return exchange
    return exchange.lower()


def is_cn_sh(symbol: str) -> bool:
    return get_cn_exchange(symbol) == "SH"
