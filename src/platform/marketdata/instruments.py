"""Instrument identity shared by search, persistence and research."""

from typing import Literal

AssetType = Literal["stock", "etf"]


def infer_asset_type(name: str = "", asset_type: str | None = None) -> AssetType:
    """Prefer an explicit classification; names are a legacy-data fallback.

    Some Hong Kong ETFs have no ETF in their name. Their type can be selected
    explicitly when adding them instead of guessing from the ticker range.
    """
    if asset_type in ("stock", "etf"):
        return asset_type
    return "etf" if "ETF" in (name or "").upper() else "stock"


ETF_RESEARCH_CONTEXT = (
    "This instrument is an exchange-traded fund (ETF), not an operating company. "
    "Evaluate the tracked index or underlying exposure, diversification, liquidity, "
    "tracking error, fees and premium/discount only when supporting data is available. "
    "Company income statements, balance sheets and cash flow statements do not apply "
    "to the fund. Do not invent holdings, NAV, fees or fund financials; explicitly "
    "state when these data are unavailable."
)
